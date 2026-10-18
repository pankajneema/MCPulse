import importlib.util
from types import SimpleNamespace
import unittest

from mcpulse.channels import Slack
from mcpulse.core import Broker
from tests.test_core import payload


class FakeWeb:
    def __init__(self):
        self.calls = []
    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ts": "100.1"}
    def chat_update(self, **kwargs):
        self.calls.append(kwargs)


@unittest.skipUnless(importlib.util.find_spec("slack_sdk"), "Install optional Slack SDK to run Slack contract tests")
class SlackTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(":memory:", "away", config={"context": False})
        self.addCleanup(self.broker.close)
        self.slack = Slack(self.broker, {"user_id": "U1", "channel_id": "D1"})
        self.web = FakeWeb()
        self.client = SimpleNamespace(web_client=self.web, send_socket_mode_response=lambda value: None)
        self.slack.client = self.client

    def click(self, value, user="U1", channel="D1", ts="100.1"):
        return SimpleNamespace(type="interactive", envelope_id="envelope", payload={"user": {"id": user}, "channel": {"id": channel}, "message": {"ts": ts}, "actions": [{"value": value}]})

    def test_wrong_sender_channel_or_message_cannot_answer(self):
        request = self.broker.open(payload())
        self.slack.send(request.card)
        for kwargs in ({"user": "evil"}, {"channel": "evil"}, {"ts": "evil"}):
            self.slack.receive(self.client, self.click("allow:" + request.id, **kwargs))
            self.assertEqual(request.state, "OPEN")
        self.slack.receive(self.client, self.click("allow:" + request.id))
        self.assertEqual(request.state, "ALLOWED")

    def test_high_risk_two_steps(self):
        request = self.broker.open(payload("git push origin main"))
        self.slack.send(request.card)
        self.slack.receive(self.client, self.click("allow:" + request.id))
        self.assertEqual(request.state, "OPEN")
        nonce = self.slack.confirmations[request.id][0]
        self.slack.receive(self.client, self.click(f"confirm:{request.id}:{nonce}"))
        self.assertEqual(request.state, "ALLOWED")
