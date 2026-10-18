from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from mcpulse.client import relay
from mcpulse.config import initialize, key, private_write
from mcpulse.core import Broker
from mcpulse.server import Server
from tests.test_core import payload


class ReconnectTests(unittest.TestCase):
    def test_bridge_reconnects_to_recovered_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = initialize(directory)
            config.update(permission_timeout=10, context=False)
            database = str(directory / "state.db")
            first = Broker(database, "away", timeout=10, config=config)
            server = Server(first, key(config), 0)
            config["port"] = server.server_port
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            with ThreadPoolExecutor() as executor:
                result = executor.submit(relay, config, payload())
                deadline = time.monotonic() + 3
                while not first.pending and time.monotonic() < deadline:
                    time.sleep(0.01)
                rid = next(iter(first.pending))
                server.shutdown()
                thread.join()
                server.server_close()
                first.close(preserve=True)
                second = Broker(database, "away", timeout=10, config=config)
                server = Server(second, key(config), config["port"])
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
                thread.start()
                try:
                    second.answer(rid, "allow", "local")
                    self.assertEqual(result.result(timeout=5)["hookSpecificOutput"]["decision"]["behavior"], "allow")
                finally:
                    server.shutdown()
                    thread.join()
                    server.server_close()
                    second.close()


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.config = initialize(directory)
        self.config.update(permission_timeout=10, context=False)
        self.broker = Broker(":memory:", "present", timeout=10, config=self.config)
        self.server = Server(self.broker, key(self.config), 0)
        self.config["port"] = self.server.server_port
        private_write(directory / "config.json", json.dumps(self.config))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.process = subprocess.Popen([sys.executable, "-m", "mcpulse", "mcp", "serve"],
            env={**os.environ, "MCPULSE_HOME": str(directory)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.output = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.output.put(json.loads(line))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def tearDown(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.broker.close()
        self.temporary.cleanup()

    def send(self, method, params=None, rid=None):
        data = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if rid is not None:
            data["id"] = rid
        self.process.stdin.write(json.dumps(data) + "\n")
        self.process.stdin.flush()

    def initialize(self):
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}, 1)
        self.assertEqual(self.output.get(timeout=3)["result"]["protocolVersion"], "2025-06-18")
        self.send("notifications/initialized")

    def test_initialize_tools_and_approval(self):
        self.initialize()
        self.send("tools/list", rid=2)
        self.assertEqual(len(self.output.get(timeout=3)["result"]["tools"]), 3)
        self.send("tools/call", {"name": "request_approval", "arguments": {"action": "Deploy test app"}}, 3)
        deadline = time.monotonic() + 3
        while not self.broker.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        rid = next(iter(self.broker.pending))
        self.broker.answer(rid, "allow", "local", confirmed=True)
        result = self.output.get(timeout=3)
        self.assertTrue(json.loads(result["result"]["content"][0]["text"])["approved"])

    def test_cancellation_resolves_pending_request(self):
        self.initialize()
        self.send("tools/call", {"name": "ask_human", "arguments": {"question": "Which?", "options": ["one", "two"]}}, 4)
        deadline = time.monotonic() + 3
        while not self.broker.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.broker.pending)
        self.send("notifications/cancelled", {"requestId": 4})
        deadline = time.monotonic() + 3
        while self.broker.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.broker.pending)
        self.send("ping", rid=5)
        self.assertEqual(self.output.get(timeout=3)["id"], 5)

    def test_tools_before_initialize_are_rejected(self):
        self.send("tools/list", rid=6)
        self.assertEqual(self.output.get(timeout=3)["error"]["code"], -32002)
