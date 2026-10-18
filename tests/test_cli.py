import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from mcpulse.config import initialize, private_write


class CLITests(unittest.TestCase):
    def test_daemon_demo_answer_and_shutdown_in_separate_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = initialize(root)
            with socket.socket() as socket_probe:
                socket_probe.bind(("127.0.0.1", 0))
                config["port"] = socket_probe.getsockname()[1]
            config["permission_timeout"] = 10
            private_write(root / "config.json", json.dumps(config))
            env = {**os.environ, "MCPULSE_HOME": directory}
            command = [sys.executable, "-c",
                       "import faulthandler, runpy; "
                       "faulthandler.dump_traceback_later(5); "
                       "runpy.run_module('mcpulse', run_name='__main__')"]
            def run(*args):
                try:
                    return subprocess.run([*command, *args], env=env, text=True, capture_output=True, timeout=10)
                except subprocess.TimeoutExpired as error:
                    self.fail(f"CLI {args} timed out: {error.stderr!r}")
            daemon = subprocess.Popen([*command, "start"], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            demo = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    status = run("status")
                    if status.returncode == 0:
                        break
                    time.sleep(0.02)
                self.assertEqual(status.returncode, 0, status.stderr)
                dashboard = run("ui", "--no-open")
                self.assertEqual(dashboard.returncode, 0, dashboard.stderr)
                self.assertIn("/ui/login?ticket=", dashboard.stdout)
                self.assertNotIn(config["secret"], dashboard.stdout)
                self.assertEqual(run("away").returncode, 0)
                demo = subprocess.Popen([*command, "demo"], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                pending = []
                deadline = time.monotonic() + 5
                while not pending and time.monotonic() < deadline:
                    pending = json.loads(run("status").stdout)["pending"]
                    time.sleep(0.01)
                self.assertEqual(len(pending), 1)
                answer = run("answer", pending[0]["id"], "allow")
                self.assertEqual(answer.returncode, 0, answer.stderr)
                output, error = demo.communicate(timeout=5)
                self.assertEqual(demo.returncode, 0, error)
                self.assertIn('"behavior": "allow"', output)
                audit = run("audit")
                self.assertIn("ALLOWED", audit.stdout)
                self.assertEqual(run("doctor").returncode, 0)
            finally:
                if demo and demo.poll() is None:
                    demo.kill()
                    demo.communicate()
                daemon.terminate()
                stdout, stderr = daemon.communicate(timeout=20)
            if os.name != "nt":
                self.assertEqual(daemon.returncode, 0, stderr)
                self.assertEqual((root / "audit.sqlite3").stat().st_mode & 0o777, 0o600)
            self.assertNotIn(config["secret"], stdout + stderr)
