"""Signed, expiring confirmation pages on a separate loopback port."""
import base64
import hashlib
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .replies import valid_pin
from .core import RequestError
from .ui import standalone, card_details


def issue(secret, request_id, action, expires):
    data = json.dumps([request_id, action, int(expires)], separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(data).decode().rstrip("=")
    signature = hmac.new(bytes.fromhex(secret), b"link:" + encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def verify(secret, token):
    try:
        if len(token) > 512:
            raise ValueError()
        encoded, signature = token.split(".")
        expected = hmac.new(bytes.fromhex(secret), b"link:" + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        rid, action, expires = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if action not in {"allow", "deny", "handback", "text", "done"} or type(expires) is not int or time.time() >= expires:
            raise ValueError()
        return rid, action
    except (ValueError, TypeError):
        raise RequestError("Invalid or expired link") from None


def links(broker, card, origin):
    with broker.lock:
        request = broker.pending.get(card["id"])
        if not request:
            return {}
        expires = time.time() + min(900, request.deadline - time.monotonic())
    actions = ["allow", "deny", "handback"] if card["kind"] == "permission" else ["text", "done"]
    if not card["can_allow"]:
        actions = ["deny", "handback"]
    return {action: origin.rstrip("/") + "/r/" + issue(broker.config["secret"], card["id"], action, expires) for action in actions}


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, broker, config, whatsapp=None):
        self.broker, self.config, self.whatsapp = broker, config, whatsapp
        self.failures = {}
        self.rate_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(("127.0.0.1", config.get("web_port", 7718)), WebHandler)

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, *_):
        pass


class WebHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def send(self, status, content, content_type="text/html; charset=utf-8"):
        data = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def page(self, title, body):
        return standalone(title, '<section class="panel"><div class="panel-body">' + body + '</div></section>')

    def target(self):
        if not self.path.startswith("/r/"):
            raise RequestError("Unknown page")
        token = self.path[3:]
        rid, action = verify(self.server.config["secret"], token)
        with self.server.broker.lock:
            request = self.server.broker.pending.get(rid)
            if request is None or time.monotonic() >= request.deadline:
                raise RequestError("Request closed or expired")
            return token, rid, action, dict(request.card)

    def do_GET(self):
        try:
            if self.path.startswith("/whatsapp?") and self.server.whatsapp:
                query = parse_qs(urlsplit(self.path).query)
                config = self.server.whatsapp.config
                if query.get("hub.mode") == ["subscribe"] and secrets.compare_digest(query.get("hub.verify_token", [""])[0], config["verify_token"]):
                    return self.send(200, query.get("hub.challenge", [""])[0], "text/plain")
                return self.send(403, "Forbidden")
            token, rid, action, card = self.target()
            details = card_details(card)
            form = '<form method="post"><input type="hidden" name="csrf" value="' + html.escape(token) + '">'
            if action == "text":
                form += '<label>Your answer<textarea name="reply" maxlength="4000" required rows="4"></textarea></label>'
            if action == "allow" and card["risk"] == "high":
                form += '<label>Approval PIN<input name="pin" type="password" inputmode="numeric" autocomplete="off" required></label>'
            form += '<button type="submit">Confirm ' + html.escape(action) + '</button></form>'
            self.send(200, self.page("Confirm " + action, details + form))
        except RequestError as error:
            self.send(410, self.page("Unavailable", html.escape(str(error))))

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536 or self.headers.get("Transfer-Encoding"):
                return self.send(413, "Invalid body")
            raw = self.rfile.read(length)
            if self.path == "/whatsapp" and self.server.whatsapp:
                self.server.whatsapp.receive(raw, self.headers.get("X-Hub-Signature-256", ""))
                return self.send(200, "ok", "text/plain")
            token, rid, action, card = self.target()
            origin = self.headers.get("Origin")
            expected = self.server.config.get("web_origin", "")
            if origin and origin.rstrip("/") != expected.rstrip("/"):
                return self.send(403, "Origin rejected")
            data = parse_qs(raw.decode(), max_num_fields=5)
            if not secrets.compare_digest(data.get("csrf", [""])[0], token):
                return self.send(403, "Confirmation required")
            confirmed = False
            if action == "allow" and card["risk"] == "high":
                with self.server.rate_lock:
                    attempts = [stamp for stamp in self.server.failures.get(rid, []) if time.time() - stamp < 300]
                    if len(attempts) >= 5 or not valid_pin(data.get("pin", [""])[0], self.server.config):
                        self.server.failures[rid] = [*attempts, time.time()]
                        return self.send(403, self.page("PIN required", "Enter a valid configured approval PIN. After five failed attempts, wait five minutes."))
                confirmed = True
            state = self.server.broker.answer(rid, action, "web", confirmed=confirmed, reason=data.get("reply", [""])[0])
            self.send(200, self.page("Answer recorded", html.escape(state)))
        except (ValueError, UnicodeError, TypeError, AttributeError, KeyError) as error:
            self.send(400, self.page("Cannot answer", html.escape(str(error))))
