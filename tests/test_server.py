from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from mcpulse.core import Broker
from mcpulse.server import Server
from tests.test_core import payload


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(":memory:", "away", timeout=2)
        self.server = Server(self.broker, "test-key", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.broker.close()
        self.server.server_close()

    def request(self, path, body=None, key="test-key", headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.url + path, data=data, headers={
            "x-mcpulse-key": key, "Content-Type": "application/json", **(headers or {})})
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_auth_on_all_routes(self):
        for path, body in (("/api/status", None), ("/api/audit", None),
                           ("/api/answer", {}), ("/hook/PermissionRequest", payload()),
                           ("/hook/SessionEnd", {}), ("/api/presence", {})):
            for key in ("", "wrong"):
                with self.subTest(path=path, key=key):
                    self.assertEqual(self.request(path, body, key)[0], 401)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_browser_origin_rejected(self):
        self.assertEqual(self.request("/api/status", headers={"Origin": "https://evil.example"})[0], 401)

    def test_permission_round_trip_with_real_http(self):
        with ThreadPoolExecutor() as pool:
            waiting = pool.submit(self.request, "/hook/PermissionRequest", payload())
            deadline = time.monotonic() + 1
            while not self.broker.status()["pending"] and time.monotonic() < deadline:
                time.sleep(0.005)
            status, body = self.request("/api/status")
            self.assertEqual(status, 200)
            request_id = body["pending"][0]["id"]
            self.assertEqual(self.request("/api/answer", {"id": request_id, "action": "allow"})[0], 200)
            status, response = waiting.result()
        self.assertEqual(status, 200)
        self.assertEqual(response["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertEqual(self.request("/api/answer", {"id": request_id, "action": "allow"})[0], 400)

    def test_invalid_data_never_grants(self):
        for body in ([], {}, {**payload(), "tool_input": None}):
            self.assertEqual(self.request("/hook/PermissionRequest", body)[0], 400)
        self.assertEqual(self.request("/api/answer", {"id": [], "action": "allow"})[0], 400)
        self.assertEqual(self.request("/api/presence", {"mode": []})[0], 400)
        self.assertEqual(self.request("/unknown", {})[0], 404)
        self.assertEqual(self.request("/hook/PermissionRequest", {"large": "x" * 70000})[0], 413)

    def test_presence_route_hands_back(self):
        request = self.broker.open(payload())
        self.assertEqual(self.request("/api/presence", {"mode": "present"})[0], 200)
        self.assertEqual(self.broker.wait(request), {})

    def test_session_end_route(self):
        request = self.broker.open(payload())
        self.assertEqual(self.request("/hook/SessionEnd", {"hook_event_name": "SessionEnd", "session_id": "test-session"})[0], 200)
        self.assertEqual(request.state, "CANCELLED")


class StartupTests(unittest.TestCase):
    def test_loopback_listeners_do_not_require_reverse_dns(self):
        from mcpulse.web import WebServer
        broker = Broker(":memory:")
        self.addCleanup(broker.close)
        with patch("socket.getfqdn", side_effect=AssertionError("Reverse DNS must not block loopback startup")):
            for server in (Server(broker, "test-key", 0), WebServer(broker, {"web_port": 0})):
                try:
                    self.assertEqual(server.server_name, "127.0.0.1")
                    self.assertGreater(server.server_port, 0)
                finally:
                    server.server_close()
