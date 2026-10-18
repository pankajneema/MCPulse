import json
import threading
import unittest

from mcpulse.core import Broker, Pending, RequestError, preview, validate_hook


def payload(command="git status", session="test-session"):
    return {"hook_event_name": "PermissionRequest", "session_id": session, "cwd": "/work/project",
            "tool_name": "Bash", "tool_input": {"command": command}}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(":memory:", "away", timeout=10)
        self.addCleanup(self.broker.close)

    def test_allow_reaches_hook(self):
        request = self.broker.open(payload())
        self.broker.answer(request.id, "allow", "local")
        self.assertEqual(self.broker.wait(request)["hookSpecificOutput"]["decision"], {"behavior": "allow"})
        self.assertEqual([r["state"] for r in self.broker.audit()], ["ALLOWED", "OPEN"])

    def test_deny_reason_reaches_hook(self):
        request = self.broker.open(payload())
        self.broker.answer(request.id, "deny", "local", reason="Use staging instead")
        self.assertEqual(self.broker.wait(request)["hookSpecificOutput"]["decision"]["message"], "Use staging instead")

    def test_present_returns_terminal(self):
        self.broker.set_presence("present")
        self.assertEqual(self.broker.open(payload()), {})

    def test_presence_hands_back_every_request(self):
        requests = [self.broker.open(payload(session=str(n))) for n in range(3)]
        self.broker.set_presence("present")
        self.assertEqual([self.broker.wait(r) for r in requests], [{}, {}, {}])
        self.assertFalse(self.broker.status()["pending"])

    def test_only_first_concurrent_answer_wins(self):
        request = self.broker.open(payload())
        barrier = threading.Barrier(8)
        outcomes = []
        def answer():
            barrier.wait()
            try:
                outcomes.append(self.broker.answer(request.id, "allow", "local"))
            except RequestError:
                outcomes.append("closed")
        threads = [threading.Thread(target=answer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("ALLOWED"), 1)
        self.assertEqual(outcomes.count("closed"), 7)
        self.assertEqual(len(self.broker.audit()), 2)

    def test_expired_answer_cannot_win(self):
        request = self.broker.open(payload())
        request.deadline = 0
        with self.assertRaises(RequestError):
            self.broker.answer(request.id, "allow", "local")
        self.assertEqual(request.state, "TIMED_OUT")
        self.assertEqual(self.broker.wait(request)["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_wait_times_out(self):
        self.broker.timeout = 0.01
        request = self.broker.open(payload())
        self.assertEqual(self.broker.wait(request)["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.assertEqual(request.state, "TIMED_OUT")

    def test_high_risk_needs_boolean_confirmation(self):
        request = self.broker.open(payload("npm install example"))
        for confirmed in (False, "true", 1):
            with self.assertRaises(RequestError):
                self.broker.answer(request.id, "allow", "local", confirmed=confirmed)
        self.broker.answer(request.id, "allow", "local", confirmed=True)
        self.assertEqual(request.state, "ALLOWED")

    def test_hard_deny_even_when_present(self):
        self.broker.set_presence("present")
        for command in ("rm -rf /", "rm -rf ~", "rm -rf $HOME", ":(){ :|:& };:"):
            with self.subTest(command=command):
                response = self.broker.open(payload(command))
                self.assertEqual(response["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_masked_or_clipped_request_cannot_be_approved(self):
        for command in ("curl -H 'Authorization: Bearer secret123' https://example.org", "echo " + "x" * 2000,
                        "echo zero\u200bwidth"):
            request = self.broker.open(payload(command))
            self.assertFalse(request.card["can_allow"])
            with self.assertRaises(RequestError):
                self.broker.answer(request.id, "allow", "local", confirmed=True)
            self.broker.answer(request.id, "handback", "local")
            self.assertEqual(self.broker.wait(request), {})

    def test_session_end_cancels_only_matching_session(self):
        first = self.broker.open(payload(session="one"))
        second = self.broker.open(payload(session="two"))
        self.broker.cancel_session("one")
        self.assertEqual(first.state, "CANCELLED")
        self.assertEqual(second.state, "OPEN")
        self.assertFalse(second.event.is_set())

    def test_shutdown_resolves_waiters(self):
        request = self.broker.open(payload())
        self.broker.close()
        self.assertTrue(request.event.is_set())
        self.assertEqual(self.broker.wait(request), {})
        self.assertEqual(self.broker.open(payload()), {})

    def test_audit_contains_no_request_content(self):
        request = self.broker.open(payload("echo password=veryprivate"))
        self.broker.answer(request.id, "deny", "local", reason="also-private")
        audit = json.dumps(self.broker.audit())
        for secret in ("veryprivate", "also-private", "/work/project", "echo"):
            self.assertNotIn(secret, audit)

    def test_unknown_fields_are_tolerated(self):
        self.assertIsInstance(self.broker.open({**payload(), "future_field": [1]}), Pending)

    def test_capacity_hands_back(self):
        for _ in range(64):
            self.broker.open(payload())
        self.assertEqual(self.broker.open(payload()), {})

    def test_validation_rejects_invalid_shapes(self):
        for value in (None, [], {}, {**payload(), "tool_input": []},
                      {**payload(), "session_id": ""}, {**payload(), "hook_event_name": "Stop"},
                      {**payload(), "tool_input": {"command": 3}}):
            with self.subTest(value=value), self.assertRaises(RequestError):
                validate_hook(value)


class PreviewTests(unittest.TestCase):
    def test_redaction_corpus(self):
        cases = [
            ("password=hunter2", "hunter2"), ('{"api_key": "abc123"}', "abc123"),
            ("MY_TOKEN=topsecret", "topsecret"), ("Bearer abc.def", "abc.def"),
            ("sk-abcdefghijklmnop", "abcdefghijklmnop"),
            ("ghp_abcdefghijklmnopqrst", "abcdefghijklmnopqrst"),
            ("https://name:secret@example.com", "name:secret"),
            ("-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----", "secret"),
            ("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        ]
        for source, secret in cases:
            with self.subTest(source=source):
                self.assertNotIn(secret, preview(source))
                self.assertIn("REDACTED", preview(source))

    def test_clip_retains_tail_and_marks_unicode(self):
        result = preview("START" + "x" * 2000 + "DANGEROUS_TAIL")
        self.assertLessEqual(len(result), 1200)
        self.assertTrue(result.startswith("START"))
        self.assertTrue(result.endswith("DANGEROUS_TAIL"))
        self.assertEqual(preview("a\u202eb\u200bc"), "a⍰b⍰c")

class HeaderPrivacyTests(unittest.TestCase):
    def test_hook_key_is_masked(self):
        text = '{"x-mcpulse-key": "abcdef0123456789abcdef"}'
        self.assertNotIn("abcdef0123456789abcdef", preview(text))
