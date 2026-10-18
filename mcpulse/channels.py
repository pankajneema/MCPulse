"""Optional delivery adapters. Telegram remains the zero-dependency default."""
from email.message import EmailMessage
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.parse import urlsplit

from .core import preview, RequestError
from .web import links
from .replies import parse, valid_pin


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def post(url, body, headers=None):
    try:
        request = Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=10) as response:
            data = response.read(65536)
            return json.loads(data) if data else {}
    except Exception:
        raise RuntimeError("Channel request failed; check credentials and connectivity") from None


def validate_channel(name, config):
    fields = {"slack": ("bot_token", "app_token", "user_id", "channel_id"),
              "ntfy": ("server", "topic"), "email": ("host", "username", "password", "from", "to"),
              "whatsapp": ("token", "phone_number_id", "recipient", "api_version", "app_secret", "verify_token")}
    if not isinstance(config, dict) or any(not isinstance(config.get(key), str) or not config[key] for key in fields[name]):
        raise ValueError("Missing required " + name + " configuration fields")
    if name == "ntfy" and (urlsplit(config["server"]).scheme != "https" or not re.fullmatch(r"[A-Za-z0-9_-]+", config["topic"])):
        raise ValueError("ntfy requires HTTPS and an alphanumeric topic")
    if name == "whatsapp" and (not re.fullmatch(r"v\d{2}\.\d+", config["api_version"]) or not config["recipient"].isdigit() or not config["phone_number_id"].isdigit()):
        raise ValueError("Invalid WhatsApp identifiers or API version")
    quiet = config.get("quiet_hours")
    if quiet is not None and (not isinstance(quiet, list) or len(quiet) != 2 or any(type(h) is not int or not 0 <= h <= 23 for h in quiet)):
        raise ValueError("quiet_hours must contain two hours from 0 to 23")
    if "port" in config and (type(config["port"]) is not int or not 1 <= config["port"] <= 65535):
        raise ValueError("Invalid channel port")
    delay = config.get("after_seconds", 0)
    if type(delay) is not int or not 0 <= delay <= 86400:
        raise ValueError("Invalid escalation delay")


class Delivery:
    def __init__(self, broker, config, name):
        self.broker, self.config, self.name = broker, config, name
        self.stopped = threading.Event()
        self.sent = set()

    def content(self, card):
        text = (f"MCPulse · {card['id']} · {card['kind']}\n{card['project']}\nAgent: {card.get('agent', 'Unknown agent')}\nSession ID: {card.get('session', 'Unavailable')}\n{card['tool']} ({card['risk']})\n{card['input']}")
        return text, links(self.broker, card, self.broker.config.get("web_origin", "")) if self.broker.config.get("web_origin") else {}

    def send(self, card):
        text, actions = self.content(card)
        if self.name == "ntfy":
            body = {"topic": self.config["topic"], "title": "MCPulse needs you", "message": text,
                    "actions": [{"action": "view", "label": action.title(), "url": url} for action, url in actions.items()]}
            headers = {"Authorization": "Bearer " + self.config["token"]} if self.config.get("token") else {}
            post(self.config["server"].rstrip("/"), body, headers)
        elif self.name == "email":
            message = EmailMessage()
            message["Subject"] = "MCPulse needs your answer · " + card["id"]
            message["From"], message["To"] = self.config["from"], self.config["to"]
            message.set_content(text + "\n\n" + "\n".join(action + ": " + url for action, url in actions.items()))
            with smtplib.SMTP(self.config["host"], self.config.get("port", 587), timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.config["username"], self.config["password"])
                smtp.send_message(message)
        elif self.name == "whatsapp":
            body = {"messaging_product": "whatsapp", "to": self.config["recipient"]}
            combined = text + "\n\n" + "\n".join(action + ": " + url for action, url in actions.items())
            if self.config.get("template"):
                body.update(type="template", template={"name": self.config["template"], "language": {"code": self.config.get("language", "en")},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": combined[:3000]}]}]})
            else:
                body.update(type="text", text={"body": combined[:4000], "preview_url": False})
            post(f"https://graph.facebook.com/{self.config['api_version']}/{self.config['phone_number_id']}/messages", body, {"Authorization": "Bearer " + self.config["token"]})

    def sync_cards(self):
        cards = self.broker.status()["pending"]
        open_ids = {card["id"] for card in cards}
        self.sent.intersection_update(open_ids)
        failed, accepted = 0, 0
        for card in cards:
            if self.stopped.is_set():
                break
            if card["id"] in self.sent or time.time() - card["created"] < self.config.get("after_seconds", 0):
                continue
            try:
                saved = self.broker.message_ref(self.name, card["id"])
                if saved:
                    if self.name == "slack":
                        self.refs[card["id"]] = saved
                    self.sent.add(card["id"])
                    continue
                quiet = self.config.get("quiet_hours")
                hour = time.localtime().tm_hour
                if quiet and (quiet[0] <= hour < quiet[1] if quiet[0] < quiet[1] else hour >= quiet[0] or hour < quiet[1]):
                    continue
                if card["kind"] in {"notification", "finished"} and time.time() < self.broker.muted_until:
                    continue
                self.send(card)
                if self.name != "slack":
                    self.broker.message_ref(self.name, card["id"], True)
                self.sent.add(card["id"])
                accepted += 1
            except Exception:
                # A rejected card must not prevent other requests from reaching the user.
                failed += 1
        if self.name == "slack":
            for rid in list(self.refs):
                if rid not in open_ids:
                    try:
                        self.client.web_client.chat_update(channel=self.config["channel_id"], ts=self.refs[rid], text="MCPulse · " + rid + " · closed", blocks=[])
                        del self.refs[rid]
                        self.confirmations.pop(rid, None)
                        accepted += 1
                    except Exception:
                        failed += 1
        if failed:
            self.broker.channel_health[self.name] = f"{failed} delivery/update failed; retrying"
        elif accepted:
            self.broker.channel_health[self.name] = "connected"
        elif self.broker.channel_health.get(self.name) != "connected":
            self.broker.channel_health[self.name] = "ready; delivery not yet verified"

    def run(self):
        while not self.stopped.is_set():
            try:
                self.sync_cards()
            except Exception:
                self.broker.channel_health[self.name] = "unavailable; retrying"
            self.stopped.wait(2)


class WhatsApp(Delivery):
    def __init__(self, broker, config):
        super().__init__(broker, config, "whatsapp")
        self.seen = {}
        self.pin_failures = []
        self.receive_lock = threading.Lock()

    def receive(self, raw, signature):
        expected = "sha256=" + hmac.new(self.config["app_secret"].encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise RequestError("Invalid webhook signature")
        payload = json.loads(raw)
        with self.receive_lock:
            self.seen = {mid: at for mid, at in self.seen.items() if time.time() - at < 86400}
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if value.get("metadata", {}).get("phone_number_id") != self.config["phone_number_id"]:
                        continue
                    for message in value.get("messages", []):
                        if message.get("from") != self.config["recipient"] or message.get("id") in self.seen:
                            continue
                        if abs(time.time() - int(message.get("timestamp", 0))) > 300:
                            continue
                        if not self.broker.consume_update("whatsapp", message.get("id")):
                            continue
                        self.seen[message.get("id")] = time.time()
                        try:
                            pending = self.broker.status()["pending"]
                            rid, action, reason = parse(message.get("text", {}).get("body", ""), pending)
                            card = next(c for c in pending if c["id"] == rid)
                            if int(message["timestamp"]) < card["created"] - 1:
                                continue
                            confirmed = False
                            if action == "allow" and card["risk"] == "high":
                                self.pin_failures = [at for at in self.pin_failures if time.time() - at < 300]
                                if len(self.pin_failures) >= 5 or not valid_pin(reason, self.broker.config):
                                    self.pin_failures.append(time.time())
                                    continue
                                confirmed, reason = True, ""
                            self.broker.answer(rid, action, "whatsapp", confirmed=confirmed, reason=reason)
                        except (ValueError, StopIteration):
                            continue


class Slack(Delivery):
    def __init__(self, broker, config):
        super().__init__(broker, config, "slack")
        self.refs, self.confirmations, self.seen = {}, {}, set()
        self.receive_lock = threading.Lock()
        self.pin_failures = []
        self.client = None

    def send(self, card):
        text, _ = self.content(card)
        actions = ["allow", "deny", "handback"] if card["kind"] == "permission" else ["done", "handback"]
        if not card["can_allow"]:
            actions = ["handback", "deny"]
        blocks = [{"type": "section", "text": {"type": "plain_text", "text": text[:2900]}},
                  {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": action.title()}, "value": action + ":" + card["id"], "action_id": action} for action in actions]}]
        result = self.client.web_client.chat_postMessage(channel=self.config["channel_id"], text=text[:2900], blocks=blocks, unfurl_links=False, unfurl_media=False)
        self.refs[card["id"]] = result["ts"]
        self.broker.message_ref("slack", card["id"], result["ts"])

    def receive(self, client, request):
        from slack_sdk.socket_mode.response import SocketModeResponse
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        payload = request.payload
        with self.receive_lock:
            try:
                if request.type == "interactive":
                    if payload.get("user", {}).get("id") != self.config["user_id"] or payload.get("channel", {}).get("id") != self.config["channel_id"]:
                        return
                    value = payload.get("actions", [{}])[0].get("value", "")
                    parts = value.split(":")
                    if len(parts) not in {2, 3}:
                        return
                    action, rid = parts[:2]
                    if self.broker.message_ref("slack", rid) != payload.get("message", {}).get("ts"):
                        return
                    card = next(c for c in self.broker.status()["pending"] if c["id"] == rid)
                    if action == "allow" and card["risk"] == "high":
                        nonce = secrets.token_hex(8)
                        self.confirmations[rid] = (nonce, time.time() + 60)
                        client.web_client.chat_update(channel=self.config["channel_id"], ts=payload["message"]["ts"], text=self.content(card)[0][:2900], blocks=[
                            {"type": "section", "text": {"type": "plain_text", "text": self.content(card)[0][:2900]}},
                            {"type": "actions", "elements": [{"type": "button", "action_id": "confirm", "text": {"type": "plain_text", "text": "Confirm high-risk approval"}, "value": f"confirm:{rid}:{nonce}"}]}])
                        return
                    confirmed = False
                    if action == "confirm":
                        expected = self.confirmations.pop(rid, None)
                        if not expected or time.time() >= expected[1] or len(parts) != 3 or not secrets.compare_digest(expected[0], parts[2]):
                            return
                        action, confirmed = "allow", True
                    self.broker.answer(rid, action, "slack", confirmed=confirmed)
                elif request.type == "events_api":
                    event = payload.get("event", {})
                    event_id = payload.get("event_id")
                    if event_id in self.seen or event.get("subtype") or event.get("user") != self.config["user_id"] or event.get("channel") != self.config["channel_id"]:
                        return
                    if not self.broker.consume_update("slack", event_id):
                        return
                    if len(self.seen) > 10000:
                        self.seen.clear()
                    self.seen.add(event_id)
                    pending = self.broker.status()["pending"]
                    rid, action, reason = parse(event.get("text", ""), pending)
                    card = next(c for c in pending if c["id"] == rid)
                    if float(event.get("ts", 0)) < card["created"]:
                        return
                    confirmed = False
                    if action == "allow" and card["risk"] == "high":
                        self.pin_failures = [at for at in self.pin_failures if time.time() - at < 300]
                        if len(self.pin_failures) >= 5 or not valid_pin(reason, self.broker.config):
                            self.pin_failures.append(time.time())
                            return
                        confirmed, reason = True, ""
                    self.broker.answer(rid, action, "slack_text", confirmed=confirmed, reason=reason)
            except (ValueError, StopIteration, KeyError, TypeError):
                return

    def run(self):
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        logger = logging.getLogger("mcpulse.slack")
        logger.disabled = True
        self.client = SocketModeClient(app_token=self.config["app_token"], web_client=WebClient(token=self.config["bot_token"], logger=logger), logger=logger)
        self.client.socket_mode_request_listeners.append(self.receive)
        try:
            self.client.connect()
            super().run()
        except Exception:
            self.broker.channel_health["slack"] = "unavailable; restart after fixing credentials"
        finally:
            self.client.close()


def configured_adapters(broker, config, directory):
    adapters = []
    for name in ("slack", "ntfy", "email", "whatsapp"):
        path = directory / (name + ".json")
        if not path.exists():
            continue
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError(name + " config must have mode 600")
        data = json.loads(path.read_text())
        validate_channel(name, data)
        if name in {"email", "whatsapp"} and not config.get("web_origin"):
            raise ValueError(name + " requires web_origin for confirmation links")
        if name == "slack":
            try:
                import slack_sdk
            except ImportError:
                raise ValueError("Install Slack support: python -m pip install '.[slack]'") from None
            adapters.append(Slack(broker, data))
        elif name == "whatsapp":
            adapters.append(WhatsApp(broker, data))
        else:
            adapters.append(Delivery(broker, data, name))
    return adapters
