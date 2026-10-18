"""Native idle detection and keep-awake; unavailable sensors fail toward present."""
import ctypes
import os
import re
import shutil
import subprocess
import sys
import time


def idle_seconds():
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=2)
            match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout)
            return int(match[1]) / 1e9 if match else None
        if sys.platform == "win32":
            class LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]
            value = LastInput()
            value.cbSize = ctypes.sizeof(value)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(value)):
                return ((ctypes.windll.kernel32.GetTickCount() - value.dwTime) & 0xffffffff) / 1000
        if os.environ.get("DISPLAY") and shutil.which("xprintidle"):
            result = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=2)
            return int(result.stdout.strip()) / 1000
    except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
        pass
    return None


class Presence:
    def __init__(self, broker, config):
        self.broker, self.config = broker, config
        self.inhibitor = None

    def tick(self):
        if self.broker.presence_mode == "auto":
            idle = idle_seconds()
            mode = "away" if idle is not None and idle >= self.config.get("idle_away_seconds", 120) else "present"
            if mode == "present" or mode != self.broker.presence:
                self.broker.detect_presence(mode)
        self.broker.tick()
        status = self.broker.status()
        active = bool(status["pending"] or status["sessions"])
        if self.config.get("keep_awake", False) and active and self.inhibitor is None:
            if sys.platform == "darwin":
                self.inhibitor = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
                self.inhibitor = subprocess.Popen(["systemd-inhibit", "--what=sleep", "--who=MCPulse", "--why=Agent session active", "--mode=block", sys.executable, "-c", "import time; time.sleep(86400)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == "win32":
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                self.inhibitor = True
        if self.inhibitor is not None and self.inhibitor is not True and self.inhibitor.poll() is not None:
            self.inhibitor = None
        if not active:
            self.close()

    def close(self):
        if self.inhibitor is True and sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        elif self.inhibitor is not None:
            self.inhibitor.terminate()
            self.inhibitor.wait(timeout=3)
        self.inhibitor = None
