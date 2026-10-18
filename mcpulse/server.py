"""Loopback-only authenticated HTTP API and Claude Code hooks."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
import json
import threading

from .core import Broker, RequestError, Pending, validate_event, preview, agent_name

MAX_BODY = 65536


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # These listeners use a numeric loopback address; reverse DNS is unnecessary
        # and can block startup when the host resolver is unavailable.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Server(LoopbackHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, broker: Broker, key: str, port: int = 7717):
        self.broker = broker
        self.key = key
        from .ui import Dashboard
        self.dashboard = Dashboard(self)
        self.preserve_on_shutdown = False
        self.slots = threading.BoundedSemaphore(80)
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never print payloads, authorization headers, or credentials.
        pass


class Handler(BaseHTTPRequestHandler):
    server: Server

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def send_json(self, status: int, body: object):
        data = json.dumps(body, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (OSError, TimeoutError):
            pass

    def authenticated(self) -> bool:
        # Deny browser-origin requests even when a caller knows the key.
        supplied = self.headers.get("x-mcpulse-key", "").encode("utf-8")
        if self.headers.get("Origin") or not hmac.compare_digest(supplied, self.server.key.encode()):
            self.send_json(401, {"error": "Unauthorized"})
            return False
        return True

    def do_GET(self):
        if self.server.dashboard.handle(self):
            return
        if not self.authenticated():
            return
        if self.path == "/api/status":
            self.send_json(200, self.server.broker.status())
        elif self.path.startswith("/api/result/"):
            try:
                self.send_json(200, self.server.broker.result(self.path.rsplit("/", 1)[-1]))
            except RequestError as error:
                self.send_json(404, {"error": str(error)})
        elif self.path == "/api/rules":
            self.send_json(200, self.server.broker.rules())
        elif self.path == "/api/audit":
            self.send_json(200, self.server.broker.audit())
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.server.dashboard.handle(self):
            return
        if not self.authenticated():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                self.send_json(413, {"error": "Invalid body size"})
                return
            if self.headers.get_content_type() != "application/json":
                self.send_json(415, {"error": "Expected application/json"})
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise RequestError("Expected an object")
            broker = self.server.broker
            if self.path == "/api/ui-login":
                self.send_json(200, {"url": self.server.dashboard.login_url()})
            elif self.path.startswith("/hook/"):
                event = self.path.rsplit("/", 1)[-1]
                if body.get("hook_event_name") != event:
                    raise RequestError("Hook path and event do not match")
                response = dispatch(broker, body)
                self.send_json(200, broker.wait(response) if isinstance(response, Pending) else response)
            elif self.path == "/api/submit":
                response = dispatch(broker, body.get("payload"), body.get("id"))
                self.send_json(200, {"id": response.id} if isinstance(response, Pending) else {"response": response})
            elif self.path == "/api/cancel":
                request_id = body.get("id")
                if not isinstance(request_id, str):
                    raise RequestError("Invalid id")
                try:
                    broker.answer(request_id, "handback", "client")
                except RequestError:
                    pass
                self.send_json(200, {})
            elif self.path == "/api/shutdown":
                self.server.preserve_on_shutdown = body.get("preserve") is True
                self.send_json(200, {})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif self.path == "/api/rules/forget":
                if not isinstance(body.get("project"), str):
                    raise RequestError("Invalid project")
                self.send_json(200, broker.rules(body["project"]))
            elif self.path == "/api/mute":
                seconds = body.get("seconds", 0)
                if type(seconds) is not int or not 0 <= seconds <= 86400:
                    raise RequestError("Mute must be 0–86400 seconds")
                import time
                broker.muted_until = time.time() + seconds
                self.send_json(200, {})
            elif self.path == "/api/presence":
                mode = body.get("mode")
                if not isinstance(mode, str):
                    raise RequestError("Missing presence mode")
                broker.set_presence(mode)
                self.send_json(200, broker.status())
            elif self.path == "/api/answer":
                if not isinstance(body.get("id"), str) or not isinstance(body.get("action"), str):
                    raise RequestError("Invalid answer")
                state = broker.answer(body["id"], body["action"], "local",
                                      confirmed=body.get("confirmed") is True,
                                      reason=body.get("reason", ""))
                self.send_json(200, {"state": state})
            else:
                self.send_json(404, {"error": "Not found"})
        except RequestError as error:
            self.send_json(400, {"error": str(error)})
        except (ValueError, UnicodeError, RecursionError):
            self.send_json(400, {"error": "Invalid request, expired answer, or confirmation required"})
        except (OSError, TimeoutError):
            self.send_json(408, {"error": "Request timed out"})
        except Exception:
            self.send_json(503, {"error": "MCPulse unavailable; use the terminal"})


def dispatch(broker, payload, request_id=None):
    payload = validate_event(payload)
    event = payload["hook_event_name"]
    if event == "SessionEnd":
        broker.cancel_session(payload["session_id"])
        return {}
    if event in {"SessionStart", "UserPromptSubmit"}:
        broker.session_event(payload)
        return {}
    if event in {"Notification", "PermissionDenied", "StopFailure"}:
        import time
        if broker.presence == "present" or time.time() < broker.muted_until:
            return {}
        notification = {**payload, "agent": agent_name(payload), "hook_event_name": "MCP", "kind": "notification",
                        "message": str(payload.get("message", payload.get("error", "Permission denied")))}
        broker.open(notification, request_id)
        return {}
    if event == "MCP" and payload.get("kind") == "notification":
        broker.open(payload, request_id)
        return {"delivered": False, "queued": True}
    return broker.open(payload, request_id)
