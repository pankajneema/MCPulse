"""Local API client and reconnecting hook bridge."""
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from .config import key


def api(config, path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    request = Request(f"http://127.0.0.1:{config['port']}{path}", data=data,
                      headers={"x-mcpulse-key": key(config), "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            message = json.load(error).get("error", "Request rejected")
        except (ValueError, AttributeError):
            message = "Request rejected"
        raise ValueError(f"{message} (HTTP {error.code})") from None
    except (URLError, TimeoutError, OSError):
        raise ConnectionError("Daemon unavailable; run python -m mcpulse start") from None


def relay(config, payload, *, request_id=None, cancelled=None):
    request_id = request_id or secrets.token_hex(16)
    try:
        submitted = api(config, "/api/submit", {"id": request_id, "payload": payload})
    except (ConnectionError, ValueError):
        return {}  # No daemon: Claude retains its ordinary permission flow.
    if "response" in submitted:
        return submitted["response"]
    duration = config.get("stop_timeout", 1800) if payload.get("hook_event_name") == "Stop" else config["permission_timeout"]
    deadline = time.monotonic() + duration + 10
    disconnected = None
    try:
        while time.monotonic() < deadline and not (cancelled and cancelled.is_set()):
            try:
                result = api(config, "/api/result/" + request_id)
                disconnected = None
                if result["state"] != "OPEN":
                    return result["response"]
            except ConnectionError:
                disconnected = disconnected or time.monotonic()
                if time.monotonic() - disconnected > 30:
                    break
            except ValueError:
                break
            if cancelled:
                cancelled.wait(0.3)
            else:
                time.sleep(0.3)
    finally:
        try:
            api(config, "/api/cancel", {"id": request_id})
        except (ConnectionError, ValueError):
            pass
    return {}
