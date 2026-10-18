"""Direct Bot API polling. Only the configured user in a private chat can answer."""

import json
import secrets
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler

from .core import Broker, RequestError, preview
from .replies import parse, valid_pin


class Telegram:
    def __init__(self, broker: Broker, token: str, user_id: int):
        self.broker = broker
        self.token = token
        self.user_id = user_id
        self.offset = 0
        self.started = time.time()
        self.pin_failures = []
        self.question_answers = {}
        self.messages: dict[str, int] = {}
        self.confirmations: dict[str, tuple[str, float]] = {}
        self.stopped = threading.Event()
        self.connected = False
        self.opener = build_opener(ProxyHandler({}))

    def call(self, method: str, **payload):
        request = Request(f"https://api.telegram.org/bot{self.token}/{method}",
                          json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=15) as response:
                body = json.load(response)
            if body.get("ok") is not True:
                raise RuntimeError("Telegram API rejected the request")
            return body["result"]
        except Exception:
            # urllib errors contain the URL, which contains the bot token.
            raise RuntimeError("Telegram unavailable; check network and bot configuration") from None

    def card_text(self, card):
        context = card.get("context", {})
        if card.get("kind") == "question" and card.get("questions"):
            questions = card["questions"]
            answers = self.question_answers.setdefault(card["id"], {})
            index = next((i for i, q in enumerate(questions) if q["question"] not in answers), 0)
            question = questions[index]
            options = "\n".join(f"{i + 1}. {o['label']}" for i, o in enumerate(question.get("options", [])))
            return (f"MCPulse · {card['id']} · Question {index + 1} of {len(questions)}\n{card['project']}\n"
                    f"{question['question']}\n{options}\nReply with your own answer" + (" or numbers such as 1,3." if question.get("multiSelect") else " or an option number."))[:3800]
        warning = "\nMasked/shortened input: use the terminal." if not card["can_allow"] else ""
        return (f"MCPulse · {card['id']} · {card.get('kind', 'permission')}\nProject: {card['project']}\nSession: {card['session']}\n"
                f"Tool: {card['tool']} · Risk: {card['risk']}\nBranch: {context.get('branch', '')}\n"
                f"Task: {context.get('task', '')}\nLatest: {context.get('last_message', '')}\n\n{card['input']}{warning}")[:3800]

    def buttons(self, card):
        rid = card["id"]
        rows = []
        if card.get("kind") == "permission":
            row = []
            if card["can_allow"]:
                row.append({"text": "Allow…" if card["risk"] == "high" else "Allow", "callback_data": f"allow:{rid}"})
                if card.get("can_remember"):
                    row.append({"text": "Always exact action", "callback_data": f"always:{rid}"})
            row += [{"text": "Deny", "callback_data": f"deny:{rid}"}, {"text": "Use terminal", "callback_data": f"handback:{rid}"}]
            rows.append(row)
        elif card.get("kind") == "question":
            questions = card.get("questions", [])
            answers = self.question_answers.setdefault(rid, {})
            if questions and card["can_allow"]:
                index = next((i for i, q in enumerate(questions) if q["question"] not in answers), 0)
                question = questions[index]
                if not question.get("multiSelect"):
                    for choice, option in enumerate(question.get("options", [])):
                        rows.append([{"text": preview(option["label"], 80), "callback_data": f"pick:{rid}:{index}:{choice + 1}"}])
            rows.append([{"text": "Other / how to reply", "callback_data": f"other:{rid}"}, {"text": "Use terminal", "callback_data": f"handback:{rid}"}])
        else:
            rows.append([{"text": "Done", "callback_data": f"done:{rid}"}])
        extra = [{"text": "More context", "callback_data": f"more:{rid}"}]
        if card.get("kind") != "notification":
            extra.append({"text": "Stop Claude", "callback_data": f"stop:{rid}"})
        rows.append(extra)
        return rows

    def sync_cards(self):
        delivery_failed = False
        cards = {card["id"]: card for card in self.broker.status()["pending"]}
        for request_id, card in cards.items():
            if request_id in self.messages:
                continue
            saved = self.broker.message_ref("telegram", request_id)
            if saved:
                self.messages[request_id] = saved
                continue
            try:
                result = self.call("sendMessage", chat_id=self.user_id, text=self.card_text(card),
                                   reply_markup={"inline_keyboard": self.buttons(card)},
                                   link_preview_options={"is_disabled": True})
                self.broker.message_ref("telegram", request_id, result["message_id"])
                self.messages[request_id] = result["message_id"]
            except RuntimeError:
                delivery_failed = True
        for request_id in list(self.messages):
            if request_id not in cards:
                try:
                    self.call("editMessageText", chat_id=self.user_id, message_id=self.messages[request_id],
                              text=f"MCPulse · {request_id}\nClosed. See the local audit log for the outcome.",
                              reply_markup={"inline_keyboard": []})
                except RuntimeError:
                    pass  # Closed requests remain unanswerable even if a card cannot be edited.
                del self.messages[request_id]
                self.confirmations.pop(request_id, None)
        if delivery_failed:
            raise RuntimeError("Some Telegram cards could not be delivered")

    def handle(self, update: dict):
        if "message" in update:
            return self.handle_text(update["message"], update.get("update_id"))
        callback = update.get("callback_query", {})
        message = callback.get("message", {})
        if (callback.get("from", {}).get("id") != self.user_id
                or message.get("chat", {}).get("type") != "private"
                or message.get("chat", {}).get("id") != self.user_id):
            return
        data = callback.get("data", "")
        if not isinstance(data, str):
            return
        parts = data.split(":")
        if len(parts) not in {2, 3, 4}:
            return
        action, request_id = parts[:2]
        if self.messages.get(request_id) != message.get("message_id"):
            return
        if "update_id" in update and not self.broker.consume_update("telegram", update["update_id"]):
            return
        card = next((c for c in self.broker.status()["pending"] if c["id"] == request_id), None)
        answer = "Request already closed"
        if card is not None:
            try:
                if action == "allow" and card["risk"] == "high":
                    nonce = secrets.token_hex(8)
                    self.confirmations[request_id] = (nonce, time.monotonic() + 60)
                    self.call("editMessageReplyMarkup", chat_id=self.user_id,
                              message_id=message["message_id"], reply_markup={"inline_keyboard": [[
                                  {"text": "Yes, allow this high-risk action", "callback_data": f"confirm:{request_id}:{nonce}"},
                                  {"text": "Deny", "callback_data": f"deny:{request_id}"},
                                  {"text": "Use terminal", "callback_data": f"handback:{request_id}"}]]})
                    answer = "Review the full command before confirming"
                elif action == "confirm":
                    expected = self.confirmations.get(request_id)
                    if len(parts) != 3 or expected is None or time.monotonic() >= expected[1] or not secrets.compare_digest(parts[2], expected[0]):
                        raise RequestError("Confirmation expired; return to the terminal")
                    answer = self.broker.answer(request_id, "allow", "telegram", confirmed=True)
                    self.confirmations.pop(request_id, None)
                elif action == "pick" and len(parts) == 4:
                    answer = self.answer_question(card, parts[3], int(parts[2]))
                elif action == "more":
                    with self.broker.lock:
                        request = self.broker.pending.get(request_id)
                        text = preview(json.dumps(request.payload.get("tool_input", request.payload), ensure_ascii=False, indent=2), 3800) if request else "Request closed"
                    self.call("sendMessage", chat_id=self.user_id, text=text, link_preview_options={"is_disabled": True})
                    answer = "More context sent"
                elif action == "other":
                    self.call("sendMessage", chat_id=self.user_id, text=f"Reply to the card or send {request_id} your answer. For multiple choice use 1 or 1,3. Answer the question currently shown; the next question follows automatically.")
                    answer = "Reply instructions sent"
                elif action in {"allow", "always", "deny", "handback", "done", "stop"}:
                    answer = self.broker.answer(request_id, action, "telegram")
            except (RequestError, ValueError, IndexError) as error:
                answer = str(error)
        self.call("answerCallbackQuery", callback_query_id=callback["id"], text=answer[:180])

    def run(self):
        while not self.stopped.is_set():
            try:
                delivery_ok = True
                try:
                    self.sync_cards()
                except RuntimeError:
                    delivery_ok = False
                updates = self.call("getUpdates", offset=self.offset, timeout=2,
                                    allowed_updates=["callback_query", "message"])
                for update in updates:
                    if self.stopped.is_set():
                        break
                    self.handle(update)
                    self.offset = update["update_id"] + 1
                self.connected = True
                self.broker.channel_health["telegram"] = "connected" if delivery_ok else "receiving; some cards could not be delivered"
            except (RuntimeError, KeyError, TypeError, ValueError):
                self.connected = False
                self.broker.channel_health["telegram"] = "unavailable; retrying"
                self.stopped.wait(3)

    def answer_question(self, card, value, index=None):
        from .core import parse_answers
        if not card["can_allow"]:
            raise RequestError("Question preview changed; use the terminal")
        questions = card.get("questions", [])
        if not questions:
            return self.broker.answer(card["id"], "text", "telegram", reason=value)
        answers = self.question_answers.setdefault(card["id"], {})
        if index is None:
            index = next((i for i, q in enumerate(questions) if q["question"] not in answers), 0)
        if not 0 <= index < len(questions):
            raise RequestError("Invalid question number")
        answers.update(parse_answers([questions[index]], value))
        if len(answers) == len(questions):
            # Use the original labels, validated by the broker, and complete once.
            raw = json.dumps(answers)
            return self.broker.answer(card["id"], "text", "telegram", reason=raw)
        self.call("editMessageText", chat_id=self.user_id, message_id=self.messages[card["id"]],
                  text=self.card_text(card), reply_markup={"inline_keyboard": self.buttons(card)})
        return "Answer saved; next question shown"

    def handle_text(self, message, update_id=None):
        if (message.get("from", {}).get("id") != self.user_id
                or message.get("chat", {}).get("id") != self.user_id
                or message.get("chat", {}).get("type") != "private"):
            return
        if update_id is not None and not self.broker.consume_update("telegram", update_id):
            return
        if message.get("date", 0) < self.started - 1:
            return
        text = message.get("text", "").strip()
        if not text:
            return
        try:
            command = text.split()[0].split("@")[0]
            if command in {"/away", "/present", "/auto"}:
                self.broker.set_presence(command[1:])
                response = "Presence: " + command[1:]
            elif command == "/status":
                response = preview(json.dumps(self.broker.status(), ensure_ascii=False, indent=2), 3800)
            elif command == "/pending":
                cards = self.broker.status()["pending"]
                for card in cards:
                    self.messages.pop(card["id"], None)
                    self.broker.message_ref("telegram", card["id"], 0)
                self.sync_cards()
                response = f"Re-sent {len(cards)} pending cards"
            elif command == "/audit":
                response = json.dumps(self.broker.audit()[:10], indent=2)
            elif command == "/rules":
                response = preview(json.dumps(self.broker.rules(), indent=2), 3800)
            elif command in {"/mute", "/unmute"}:
                seconds = 0 if command == "/unmute" else 3600
                if command == "/mute" and len(text.split()) > 1:
                    import re
                    match = re.fullmatch(r"(\d+)([mh]?)", text.split()[1])
                    if not match:
                        raise ValueError("Use /mute 30m or /mute 1h")
                    seconds = int(match[1]) * {"": 1, "m": 60, "h": 3600}[match[2]]
                if seconds > 86400:
                    raise ValueError("Maximum mute duration is 24 hours")
                self.broker.muted_until = time.time() + seconds
                response = "Notifications unmuted" if not seconds else "Notifications muted; approvals remain active"
            elif command in {"/help", "/start"}:
                response = "/status /pending /away /present /auto /mute 1h /unmute /audit /rules\nReply to a card, or send: y ID, n ID reason, always ID, ID answer, more ID, stop ID. High-risk typed allow requires: y ID PIN."
            else:
                pending = self.broker.status()["pending"]
                reply_message = message.get("reply_to_message", {}).get("message_id")
                reply_id = next((rid for rid, mid in self.messages.items() if mid == reply_message), None)
                rid, action, reason = parse(text, pending, reply_id)
                card = next(c for c in pending if c["id"] == rid)
                if message.get("date", 0) < card["created"] - 1:
                    return
                if action == "more":
                    response = self.card_text(card)
                else:
                    confirmed = False
                    if action == "allow" and card["risk"] == "high":
                        self.pin_failures = [stamp for stamp in self.pin_failures if time.time() - stamp < 300]
                        if len(self.pin_failures) >= 5 or not valid_pin(reason, self.broker.config):
                            self.pin_failures.append(time.time())
                            raise ValueError("A valid configured PIN is required; alternatively use the card buttons")
                        confirmed, reason = True, ""
                    if card.get("kind") == "question" and action == "text":
                        response = self.answer_question(card, reason)
                    else:
                        response = self.broker.answer(rid, action, "telegram_text", confirmed=confirmed, reason=reason)
        except (ValueError, RuntimeError, StopIteration) as error:
            response = str(error) or "Request closed"
        self.call("sendMessage", chat_id=self.user_id, text=response[:3800], link_preview_options={"is_disabled": True})


def pair(token, timeout=300):
    code = secrets.token_hex(8)
    print(f"Send /pair {code} to your bot in a private chat within five minutes.", flush=True)
    adapter = Telegram(None, token, 0)
    deadline = time.monotonic() + timeout
    offset = 0
    while time.monotonic() < deadline:
        updates = adapter.call("getUpdates", offset=offset, timeout=2, allowed_updates=["message"])
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})
            if (message.get("text") == "/pair " + code and message.get("chat", {}).get("type") == "private"
                    and message.get("from", {}).get("id") == message.get("chat", {}).get("id")):
                return message["from"]["id"]
    raise ValueError("Pairing expired; run channel pair again")
