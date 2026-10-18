import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from mcpulse.config import initialize
from mcpulse.core import Broker, RequestError
from mcpulse.web import WebServer, issue, verify
from mcpulse.channels import Delivery, WhatsApp, validate_channel
from mcpulse.replies import pin_hash, parse
from tests.test_core import payload


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = initialize(Path(self.temporary.name))
        self.config.update(web_port=0, web_origin="https://phone.example", context=False)
        self.broker = Broker(":memory:", "away", config=self.config)
        self.server = WebServer(self.broker, self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.broker.close()

    def request(self, token, body=None):
        request = Request(self.base + "/r/" + token, None if body is None else urlencode(body).encode(), headers={"Origin": "https://phone.example"} if body else {})
        try:
            response = self.opener.open(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode()

    def test_get_cannot_approve_and_post_is_single_use(self):
        request = self.broker.open(payload())
        token = issue(self.config["secret"], request.id, "allow", time.time() + 60)
        for _ in range(3):
            self.assertEqual(self.request(token)[0], 200)
            self.assertEqual(request.state, "OPEN")
        self.assertEqual(self.request(token, {"csrf": "wrong"})[0], 403)
        self.assertEqual(self.request(token, {"csrf": token})[0], 200)
        self.assertEqual(request.state, "ALLOWED")
        self.assertEqual(self.request(token, {"csrf": token})[0], 400)

    def test_tamper_expiry_and_action_binding(self):
        token = issue(self.config["secret"], "a" * 16, "allow", time.time() + 60)
        for invalid in (token + "x", issue(self.config["secret"], "a" * 16, "allow", 0)):
            with self.assertRaises(RequestError):
                verify(self.config["secret"], invalid)
        self.assertEqual(verify(self.config["secret"], token), ("a" * 16, "allow"))

    def test_high_risk_web_requires_pin(self):
        self.config["pin_salt"] = "ab" * 16
        self.config["pin_hash"] = pin_hash("123456", self.config["pin_salt"])
        request = self.broker.open(payload("git push"))
        token = issue(self.config["secret"], request.id, "allow", time.time() + 60)
        self.assertEqual(self.request(token, {"csrf": token})[0], 403)
        self.assertEqual(self.request(token, {"csrf": token, "pin": "123456"})[0], 200)
        self.assertEqual(request.state, "ALLOWED")

    def test_html_escapes_request_content(self):
        request = self.broker.open(payload("echo '<script>alert(1)</script>'"))
        token = issue(self.config["secret"], request.id, "deny", time.time() + 60)
        status, body = self.request(token)
        self.assertEqual(status, 200)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(":memory:", "away", config={"secret": "ab" * 32, "web_origin": "https://phone.example", "context": False})
        self.addCleanup(self.broker.close)

    def test_ntfy_push_uses_view_not_http_approval_actions(self):
        request = self.broker.open(payload())
        adapter = Delivery(self.broker, {"server": "https://ntfy.example", "topic": "private-topic"}, "ntfy")
        with patch("mcpulse.channels.post") as post:
            adapter.send(request.card)
        self.assertTrue(all(a["action"] == "view" for a in post.call_args.args[1]["actions"]))
        self.assertEqual(request.state, "OPEN")

    def test_failed_card_does_not_starve_others_and_only_failures_retry(self):
        first = self.broker.open(payload())
        second = self.broker.open(payload())
        adapter = Delivery(self.broker, {}, "ntfy")
        with patch.object(adapter, "send", side_effect=[RuntimeError("private token"), None]) as send:
            adapter.sync_cards()
            self.assertEqual(send.call_count, 2)
        self.assertIsNone(self.broker.message_ref("ntfy", first.id))
        self.assertTrue(self.broker.message_ref("ntfy", second.id))
        self.assertEqual(self.broker.channel_health["ntfy"], "1 delivery/update failed; retrying")
        self.assertEqual(first.state, "OPEN")
        with patch.object(adapter, "send") as send:
            adapter.sync_cards()
            self.assertEqual([c.args[0]["id"] for c in send.call_args_list], [first.id])
        self.assertEqual(self.broker.channel_health["ntfy"], "connected")
        self.broker.answer(first.id, "handback", "test")
        self.broker.answer(second.id, "handback", "test")
        adapter.sync_cards()
        self.assertFalse(adapter.sent)

    def test_idle_delayed_and_recovered_channels_are_not_falsely_connected(self):
        adapter = Delivery(self.broker, {}, "ntfy")
        adapter.sync_cards()
        self.assertEqual(self.broker.channel_health["ntfy"], "ready; delivery not yet verified")
        request = self.broker.open(payload())
        adapter.config["after_seconds"] = 300
        with patch.object(adapter, "send") as send:
            adapter.sync_cards()
            send.assert_not_called()
        self.assertNotEqual(self.broker.channel_health["ntfy"], "connected")
        adapter.config.clear()
        self.broker.message_ref("ntfy", request.id, True)
        with patch.object(adapter, "send") as send:
            adapter.sync_cards()
            send.assert_not_called()
        self.assertNotEqual(self.broker.channel_health["ntfy"], "connected")

    def test_channel_content_includes_agent_and_session(self):
        request = self.broker.open({**payload(), "agent":"opencode"})
        text, _ = Delivery(self.broker, {}, "slack").content(request.card)
        self.assertIn("Agent: OpenCode", text)
        self.assertIn("Session ID: test-session", text)

    def test_whatsapp_signature_identity_and_replay(self):
        config = {"app_secret": "secret", "phone_number_id": "777", "recipient": "123"}
        adapter = WhatsApp(self.broker, config)
        request = self.broker.open(payload())
        def event(sender):
            return json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "777"}, "messages": [{"id": sender, "from": sender, "timestamp": str(int(time.time())), "text": {"body": "y " + request.id}}]}}]}]}).encode()
        with self.assertRaises(RequestError):
            adapter.receive(event("123"), "wrong")
        for sender in ("999", "123"):
            raw = event(sender)
            signed = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
            adapter.receive(raw, signed)
            self.assertEqual(request.state, "OPEN" if sender == "999" else "ALLOWED")
        adapter.receive(raw, signed)
        self.assertEqual(len(self.broker.audit()), 2)

    def test_reply_parser_requires_disambiguation(self):
        first = self.broker.open(payload())
        second = self.broker.open(payload())
        pending = self.broker.status()["pending"]
        with self.assertRaises(ValueError):
            parse("y", pending)
        self.assertEqual(parse("y " + first.id, pending), (first.id, "allow", ""))
        self.assertEqual(parse("Use staging", pending, second.id), (second.id, "text", "Use staging"))

    def test_channel_config_validation(self):
        with self.assertRaises(ValueError):
            validate_channel("ntfy", {"server": "http://example.org", "topic": "foo"})
        with self.assertRaises(ValueError):
            validate_channel("email", {})
