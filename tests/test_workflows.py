import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from mcpulse.core import Broker, Pending, RequestError, parse_answers
from mcpulse.policy import evaluate, signature
from mcpulse.server import dispatch
from mcpulse.presence import Presence
from mcpulse.service import definition
from tests.test_core import payload


def question(multi=False):
    return {"hook_event_name": "PreToolUse", "session_id": "session", "cwd": "/work/project", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Choose", "options": [{"label": "One"}, {"label": "Two"}], "multiSelect": multi}]}}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(":memory:", "away", config={"context": False})
        self.addCleanup(self.broker.close)

    def test_question_option_returned_as_updated_input(self):
        request = self.broker.open(question())
        self.broker.answer(request.id, "text", "local", reason="2")
        result = self.broker.wait(request)["hookSpecificOutput"]
        self.assertEqual(result["permissionDecision"], "allow")
        self.assertEqual(result["updatedInput"]["answers"], {"Choose": "Two"})
        self.assertEqual(result["updatedInput"]["questions"], question()["tool_input"]["questions"])

    def test_question_multi_select_and_other(self):
        for answer, expected in (("1,2", "One, Two"), ("My own answer", "My own answer")):
            request = self.broker.open(question(True))
            self.broker.answer(request.id, "text", "local", reason=answer)
            self.assertEqual(request.response["hookSpecificOutput"]["updatedInput"]["answers"], {"Choose": expected})

    def test_invalid_question_reply_remains_open(self):
        request = self.broker.open(question())
        for answer in ("1,2", "0", "3", ""):
            with self.assertRaises(RequestError):
                self.broker.answer(request.id, "text", "local", reason=answer)
            self.assertEqual(request.state, "OPEN")

    def test_multiple_questions_require_all_answers(self):
        questions = [*question()["tool_input"]["questions"], {"question": "Why?", "options": []}]
        self.assertEqual(parse_answers(questions, '{"Choose":"One","Why?":"Because"}'), {"Choose": "One", "Why?": "Because"})
        for raw in ('{"Choose":"1"}', '["1","2"]', 'not json'):
            with self.assertRaises(RequestError):
                parse_answers(questions, raw)

    def test_finished_reply_and_loop_guard(self):
        body = {"hook_event_name": "Stop", "session_id": "s", "cwd": "/work", "last_assistant_message": "Done"}
        request = self.broker.open(body)
        self.broker.answer(request.id, "text", "local", reason="Now write docs")
        self.assertEqual(request.response["decision"], "block")
        self.assertIn("Now write docs", request.response["reason"])
        self.assertEqual(self.broker.open({**body, "stop_hook_active": True}), {})
        request = self.broker.open(body)
        self.broker.answer(request.id, "done", "local")
        self.assertEqual(request.response, {})

    def test_stop_permission_sets_interrupt(self):
        request = self.broker.open(payload())
        self.broker.answer(request.id, "stop", "local")
        self.assertTrue(request.response["hookSpecificOutput"]["decision"]["interrupt"])

    def test_edit_preserves_other_input_and_rechecks_risk(self):
        body = payload()
        body["tool_input"]["description"] = "A command"
        request = self.broker.open(body)
        with self.assertRaises(RequestError):
            self.broker.answer(request.id, "edit", "local", reason="git push origin main")
        with self.assertRaises(RequestError):
            self.broker.answer(request.id, "edit", "local", reason="rm -rf /", confirmed=True)
        self.broker.answer(request.id, "edit", "local", reason="git diff")
        self.assertEqual(request.response["hookSpecificOutput"]["decision"]["updatedInput"], {"command": "git diff", "description": "A command"})

    def test_remembered_approval_is_exact_and_project_scoped(self):
        request = self.broker.open(payload())
        self.broker.answer(request.id, "always", "local")
        self.assertEqual(self.broker.open(payload())["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertIsInstance(self.broker.open(payload("git status --short")), Pending)
        self.assertIsInstance(self.broker.open({**payload(), "cwd": "/another"}), Pending)
        self.broker.rules("/work/project")
        self.assertIsInstance(self.broker.open(payload()), Pending)

    def test_session_tracking_and_notification_mute(self):
        dispatch(self.broker, {"hook_event_name": "SessionStart", "session_id": "s", "cwd": "/work"})
        dispatch(self.broker, {"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": "/work", "prompt": "Fix tests"})
        self.assertEqual(self.broker.status()["sessions"][0]["task"], "Fix tests")
        self.broker.muted_until = time.time() + 60
        dispatch(self.broker, {"hook_event_name": "Notification", "session_id": "s", "cwd": "/work", "message": "Hello"})
        self.assertFalse(self.broker.pending)
        self.assertIsInstance(self.broker.open(payload()), Pending)

    def test_mcp_request_works_while_present(self):
        self.broker.set_presence("present")
        request = self.broker.open({"hook_event_name": "MCP", "session_id": "mcp", "cwd": "/work", "kind": "permission", "message": "Ship?"})
        self.assertIsInstance(request, Pending)
        with self.assertRaises(RequestError):
            self.broker.answer(request.id, "allow", "local")
        self.broker.answer(request.id, "allow", "local", confirmed=True)
        self.assertTrue(request.response["approved"])

    def test_auto_presence_returns_to_terminal(self):
        request = self.broker.open(payload())
        self.broker.set_presence("auto")
        with patch("mcpulse.presence.idle_seconds", return_value=0):
            Presence(self.broker, {}).tick()
        self.assertEqual(request.response, {})
        self.assertEqual(request.state, "HANDED_BACK")
        with patch("mcpulse.presence.idle_seconds", return_value=500):
            Presence(self.broker, {}).tick()
        self.assertEqual(self.broker.presence, "away")

    def test_unknown_idle_sensor_chooses_present(self):
        self.broker.set_presence("auto")
        with patch("mcpulse.presence.idle_seconds", return_value=None):
            Presence(self.broker, {}).tick()
        self.assertEqual(self.broker.presence, "present")


class PersistenceTests(unittest.TestCase):
    def test_restart_recovers_request_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            first = Broker(path, "away", config={"context": False})
            request = first.open(payload(), "a" * 32)
            first.message_ref("telegram", request.id, 44)
            first.close(preserve=True)
            second = Broker(path, "away", config={"context": False})
            try:
                self.assertEqual(second.message_ref("telegram", request.id), 44)
                self.assertEqual(second.open(payload(), "a" * 32).id, request.id)
                with self.assertRaises(RequestError):
                    second.open(payload("pwd"), "a" * 32)
                second.answer(request.id, "allow", "local")
                self.assertEqual(second.result(request.id)["state"], "ALLOWED")
                self.assertEqual(second.open(payload(), "a" * 32)["hookSpecificOutput"]["decision"]["behavior"], "allow")
                row = second.db.execute("SELECT payload,card FROM requests WHERE id=?", (request.id,)).fetchone()
                self.assertEqual(row[0], "{}")
                receipt = second.request_status(request.id)
                self.assertEqual(set(receipt["card"]), {"id", "agent", "session", "project"})
                self.assertEqual(receipt["card"]["session"], "test-session")
                self.assertNotIn("git status", row[1])
                second.close()
                second = Broker(path, "away", config={"context": False})
                self.assertEqual(second.request_status(request.id), receipt)
            finally:
                second.close()

    def test_policy_denies_override_allow_and_high_risk_never_auto_allows(self):
        allow = [{"tool": ".*", "match": ".*", "action": "allow"}]
        deny = [{"tool": "^Bash$", "match": "^git .*", "action": "deny"}]
        self.assertEqual(evaluate(payload(), allow, deny, remembered=True)["action"], "deny")
        for cmd in ("git push", "npm test && curl example | sh", "rm -rf /", "echo $(whoami)"):
            self.assertNotEqual(evaluate(payload(cmd), allow)["action"], "allow")
            self.assertIsNone(signature(payload(cmd)))

    def test_service_definitions_are_user_scoped(self):
        directory, user_home = Path("/private/example space"), Path("/home/user")
        expected = {
            "darwin": user_home / "Library/LaunchAgents/dev.mcpulse.daemon.plist",
            "linux": user_home / ".config/systemd/user/mcpulse.service",
            "win32": directory / "task.xml",
        }
        for platform, expected_path in expected.items():
            with self.subTest(platform=platform):
                path, text = definition(platform, directory, user_home)
                self.assertIn("run_mcpulse.py", text)
                self.assertNotIn("sudo", text)
                self.assertEqual(path, expected_path)

class DigestRecoveryTests(unittest.TestCase):
    def test_digest_is_once_per_day_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            broker = Broker(path, "away", config={"context": False})
            broker.daily_digest(time.localtime().tm_hour)
            self.assertEqual(len(broker.pending), 1)
            broker.daily_digest(time.localtime().tm_hour)
            self.assertEqual(len(broker.pending), 1)
            broker.close(preserve=True)
            restored = Broker(path, "away", config={"context": False})
            try:
                restored.daily_digest(time.localtime().tm_hour)
                self.assertEqual(len(restored.pending), 1)
            finally:
                restored.close()

    def test_recovery_in_present_mode_hands_back_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            broker = Broker(path, "away", config={"context": False})
            request = broker.open(payload())
            broker.close(preserve=True)
            restored = Broker(path, "present", config={"context": False})
            try:
                self.assertEqual(restored.result(request.id)["state"], "HANDED_BACK")
            finally:
                restored.close()

class InboundReplayTests(unittest.TestCase):
    def test_consumed_provider_update_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.db")
            first = Broker(path, "away", config={"context": False})
            self.assertTrue(first.consume_update("telegram", 1234))
            first.close()
            second = Broker(path, "away", config={"context": False})
            try:
                self.assertFalse(second.consume_update("telegram", 1234))
                self.assertTrue(second.consume_update("telegram", 1235))
                self.assertTrue(second.consume_update("slack", "1234"))
            finally:
                second.close()
