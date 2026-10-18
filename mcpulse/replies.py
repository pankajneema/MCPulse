"""Shared text answer grammar. Adapters authenticate the sender before calling."""
import hashlib
import hmac
import re


def parse(text, pending, reply_id=None):
    text = text.strip()
    if not text or len(text) > 4000:
        raise ValueError("Reply must contain 1–4000 characters")
    words = text.split(maxsplit=2)
    aliases = {"y": "allow", "n": "deny", "allow": "allow", "deny": "deny", "always": "always", "stop": "stop", "more": "more", "done": "done", "handback": "handback"}
    ids = {p["id"] for p in pending}
    if words[0] in aliases and len(words) >= 2 and words[1] in ids:
        return words[1], aliases[words[0]], words[2] if len(words) == 3 else ""
    if words[0] in ids:
        tail = text[len(words[0]):].strip()
        if tail.startswith("edit "):
            return words[0], "edit", tail[5:]
        return words[0], "text", tail
    target = reply_id if reply_id in ids else next(iter(ids)) if len(ids) == 1 else None
    if target is None:
        raise ValueError("Include a request id, or reply to its card")
    if text in aliases:
        return target, aliases[text], ""
    return target, "text", text


def pin_hash(pin, salt):
    return hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def valid_pin(pin, config):
    stored = config.get("pin_hash")
    salt = config.get("pin_salt")
    return bool(stored and salt and hmac.compare_digest(pin_hash(pin, salt), stored))
