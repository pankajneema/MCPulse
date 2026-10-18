"""Private local configuration; no credentials in command-line arguments."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile


def home() -> Path:
    return Path(os.environ.get("MCPULSE_HOME", Path.home() / ".config" / "mcpulse"))


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        directory.chmod(0o700)
    path = directory / "config.json"
    if not path.exists():
        private_write(path, json.dumps({
            "port": 7717, "secret": secrets.token_hex(32),
            "presence": "present", "permission_timeout": 14400,
            "telegram_user_id": None,
        }, indent=2) + "\n")
    return load(directory)


def load(directory: Path) -> dict:
    path = directory / "config.json"
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("Config must be private: chmod 600 " + str(path))
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    secret = config.get("secret")
    if not isinstance(secret, str) or len(secret) != 64:
        raise ValueError("Invalid installation secret")
    try:
        bytes.fromhex(secret)
    except ValueError:
        raise ValueError("Invalid installation secret") from None
    port = config.get("port")
    timeout = config.get("permission_timeout")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if type(timeout) is not int or not 1 <= timeout <= 14400:
        raise ValueError("permission_timeout must be between 1 and 14400 seconds")
    if config.get("presence") not in {"present", "away", "auto"}:
        raise ValueError("presence must be present, away, or auto")
    user = config.get("telegram_user_id")
    if user is not None and (type(user) is not int or user <= 0):
        raise ValueError("telegram_user_id must be a positive integer")
    for name, default, maximum in (("stop_timeout", 1800, 14400), ("idle_away_seconds", 120, 86400)):
        config.setdefault(name, default)
        if type(config[name]) is not int or not 1 <= config[name] <= maximum:
            raise ValueError("Invalid " + name)
    config.setdefault("trusted_projects", [])
    if not isinstance(config["trusted_projects"], list) or not all(isinstance(p, str) for p in config["trusted_projects"]):
        raise ValueError("Invalid trusted_projects")
    config.setdefault("desktop_notifications", False)
    for name in ("keep_awake", "context", "desktop_notifications"):
        if name in config and type(config[name]) is not bool:
            raise ValueError("Invalid " + name)
    if "daily_digest_hour" in config and config["daily_digest_hour"] is not None and (type(config["daily_digest_hour"]) is not int or not 0 <= config["daily_digest_hour"] <= 23):
        raise ValueError("daily_digest_hour must be null or an hour from 0 to 23")
    if "audit_retention_days" in config and (type(config["audit_retention_days"]) is not int or not 1 <= config["audit_retention_days"] <= 3650):
        raise ValueError("audit_retention_days must be between 1 and 3650")
    if "web_port" in config and (type(config["web_port"]) is not int or not 1 <= config["web_port"] <= 65535 or config["web_port"] == config["port"]):
        raise ValueError("Invalid web_port")
    if "web_origin" in config and not isinstance(config["web_origin"], str):
        raise ValueError("web_origin must be a string")
    if config.get("web_origin"):
        from urllib.parse import urlsplit
        origin = urlsplit(config["web_origin"])
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path not in {"", "/"} or origin.query or origin.fragment:
            raise ValueError("web_origin must be a bare HTTPS origin")
    return config


def key(config: dict) -> str:
    return hmac.new(bytes.fromhex(config["secret"]), b"hook", hashlib.sha256).hexdigest()
