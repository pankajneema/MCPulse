import unittest

from mcpulse.core import Broker
from mcpulse.telegram import Telegram
from tests.test_core import payload


class FakeTelegram(Telegram):
    def __init__(self, broker):
        super().__init__(broker, "not-a-real-token", 123)
        self.calls = []

    def call(self, method, **data):
        self.calls.append((method, data))
        return {"message_id": 42}


def callback(request, action="allow", sender=123, chat_type="private", message_id=42):
    return {"callback_query": {"id": "cb", "from": {"id": sender},
            "message": {"message_id": message_id, "chat": {"id": 123, "type": chat_type}},
            "data": f"{action}:{request.id}"}}


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(":memory:", "away", timeout=10)
        self.addCleanup(self.broker.close)
        self.telegram = FakeTelegram(self.broker)

    def test_card_and_answer(self):
        request = self.broker.open(payload())
        self.telegram.sync_cards()
        self.assertEqual(self.telegram.calls[0][0], "sendMessage")
        self.telegram.handle(callback(request))
        self.assertEqual(request.state, "ALLOWED")
        self.telegram.sync_cards()
        self.assertEqual(self.telegram.calls[-1][0], "editMessageText")
        self.assertNotIn(request.id, self.telegram.messages)

    def test_spoofed_sender_group_and_wrong_message_ignored(self):
        request = self.broker.open(payload())
        self.telegram.sync_cards()
        for options in ({"sender": 999}, {"chat_type": "group"}, {"message_id": 999}):
            self.telegram.handle(callback(request, **options))
            self.assertEqual(request.state, "OPEN")
        self.assertEqual(len(self.telegram.calls), 1)

    def test_high_risk_requires_two_distinct_steps(self):
        request = self.broker.open(payload("git push origin main"))
        self.telegram.sync_cards()
        forged = callback(request)
        forged["callback_query"]["data"] = f"confirm:{request.id}:forged"
        self.telegram.handle(forged)
        self.assertEqual(request.state, "OPEN")
        self.telegram.handle(callback(request))
        self.assertEqual(request.state, "OPEN")
        nonce = self.telegram.confirmations[request.id][0]
        forged["callback_query"]["data"] = f"confirm:{request.id}:{nonce}"
        self.telegram.handle(forged)
        self.assertEqual(request.state, "ALLOWED")
        self.telegram.handle(forged)
        self.assertEqual(len(self.broker.audit()), 2)

    def test_confirmation_expiration(self):
        request = self.broker.open(payload("sudo something"))
        self.telegram.sync_cards()
        self.telegram.handle(callback(request))
        self.telegram.confirmations[request.id] = ("old", 0)
        update = callback(request)
        update["callback_query"]["data"] = f"confirm:{request.id}:old"
        self.telegram.handle(update)
        self.assertEqual(request.state, "OPEN")

    def test_redacted_card_has_no_allow_button_or_secret(self):
        request = self.broker.open(payload("echo password=mysecret"))
        self.telegram.sync_cards()
        card = self.telegram.calls[0][1]
        self.assertNotIn("mysecret", card["text"])
        self.assertFalse(any(b["callback_data"].startswith("allow:") for b in card["reply_markup"]["inline_keyboard"][0]))

    def test_api_failure_does_not_close_pending_request(self):
        request = self.broker.open(payload())
        def fail(*args, **kwargs):
            raise RuntimeError("Telegram unavailable")
        self.telegram.call = fail
        with self.assertRaises(RuntimeError):
            self.telegram.sync_cards()
        self.assertEqual(request.state, "OPEN")
        self.broker.set_presence("present")
        self.assertEqual(self.broker.wait(request), {})

class TelegramQuestionTests(unittest.TestCase):
    def test_multiple_questions_and_numeric_labels(self):
        from tests.test_workflows import question
        broker = Broker(":memory:", "away", config={"context": False})
        self.addCleanup(broker.close)
        body = question()
        body["tool_input"]["questions"][0]["options"] = [{"label": "2"}, {"label": "1"}]
        body["tool_input"]["questions"].append({"question": "Reason?", "options": []})
        request = broker.open(body)
        telegram = FakeTelegram(broker)
        telegram.sync_cards()
        first = callback(request)
        first["callback_query"]["data"] = f"pick:{request.id}:0:1"
        telegram.handle(first)
        self.assertEqual(request.state, "OPEN")
        self.assertIn("Question 2 of 2", telegram.card_text(request.card))
        telegram.answer_question(request.card, "Because it fits")
        self.assertEqual(request.response["hookSpecificOutput"]["updatedInput"]["answers"], {"Choose": "2", "Reason?": "Because it fits"})

class TelegramFailureTests(unittest.TestCase):
    def test_deleted_closed_card_does_not_block_other_cards(self):
        broker = Broker(":memory:", "away", config={"context": False})
        self.addCleanup(broker.close)
        adapter = FakeTelegram(broker)
        request = broker.open(payload())
        adapter.sync_cards()
        broker.answer(request.id, "deny", "local")
        original = adapter.call
        def fail_edit(method, **kwargs):
            if method == "editMessageText":
                raise RuntimeError("message unavailable")
            return original(method, **kwargs)
        adapter.call = fail_edit
        adapter.sync_cards()
        self.assertNotIn(request.id, adapter.messages)
        next_request = broker.open(payload())
        adapter.sync_cards()
        self.assertIn(next_request.id, adapter.messages)
