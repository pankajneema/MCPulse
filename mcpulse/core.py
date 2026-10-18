"""Request lifecycle. One lock makes competing answers and hand-back atomic."""

from dataclasses import dataclass, field
import json
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
import hashlib

from .policy import classify, signature, evaluate, read_rules
from .context import extract


class RequestError(ValueError):
    pass


def preview(text: str, limit: int = 1200) -> str:
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                  "[REDACTED PRIVATE KEY]", text, flags=re.S)
    text = re.sub(r"(?i)\b(bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r'''(?ix)(["']?(?:[\w-]*(?:token|password|secret|api[_-]?key|x-mcpulse-key|authorization))["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''',
                  r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{16,}|github_pat_[\w_]{16,}|AKIA[A-Z0-9]{16}|\d{6,}:[\w-]{25,})\b",
                  "[REDACTED]", text)
    text = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    text = "".join("⍰" if unicodedata.category(c) in {"Cf", "Cc", "Cs"} and c not in "\n\t" else c for c in text)
    if len(text) > limit:
        half = (limit - 50) // 2
        text = text[:half] + f"\n… {len(text) - 2 * half} characters hidden …\n" + text[-half:]
    return text


def validate_hook(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RequestError("Expected an object")
    for name in ("session_id", "cwd", "tool_name"):
        value = payload.get(name)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise RequestError("Missing or invalid " + name)
    if payload.get("hook_event_name") != "PermissionRequest":
        raise RequestError("Expected PermissionRequest")
    if not isinstance(payload.get("tool_input"), dict):
        raise RequestError("tool_input must be an object")
    if payload["tool_name"] == "Bash" and not isinstance(payload["tool_input"].get("command"), str):
        raise RequestError("Bash command must be a string")
    return payload


def assess(payload: dict) -> tuple[str, bool]:
    return classify(payload)


def hook_response(action: str, reason: str = "") -> dict:
    if action == "handback":
        return {}
    decision = {"behavior": action}
    if action == "deny":
        decision["message"] = reason or "Denied by the user through MCPulse."
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


@dataclass
class Pending:
    id: str
    session: str
    card: dict
    deadline: float
    payload: dict = field(default_factory=dict)
    event: threading.Event = field(default_factory=threading.Event)
    response: dict = field(default_factory=dict)
    state: str = "OPEN"


class Broker:
    def __init__(self, database: str, presence: str = "present", timeout: float = 14400, *, config=None, directory=None):
        self.lock = threading.RLock()
        self.pending = {}
        self.presence = presence
        self.presence_mode = presence
        self.timeout = timeout
        self.config = config or {}
        self.directory = Path(directory) if directory else None
        self.closed = False
        self.muted_until = 0
        self.channel_health = {}
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS audit (id TEXT, state TEXT, source TEXT, at REAL);
            CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT,
                card TEXT, expires REAL, state TEXT, response TEXT);
            CREATE TABLE IF NOT EXISTS remembered (project TEXT, signature TEXT, PRIMARY KEY(project, signature));
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS inbox (channel TEXT, nonce TEXT, at REAL, PRIMARY KEY(channel,nonce));
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS messages (channel TEXT, request TEXT, reference TEXT, PRIMARY KEY(channel, request));
        ''')
        for rid, raw, card, expires in self.db.execute("SELECT id,payload,card,expires FROM requests WHERE state='OPEN'"):
            payload = json.loads(raw)
            request = Pending(rid, payload["session_id"], json.loads(card),
                              time.monotonic() + max(0, expires - time.time()), payload)
            self.pending[rid] = request
        self.db.commit()
        self.tick()
        if self.presence == "present":
            self.detect_presence("present")

    def _audit(self, request_id, state, source):
        self.db.execute("INSERT INTO audit VALUES (?,?,?,?)", (request_id, state, source, time.time()))

    def _policy(self, payload):
        user, project = [], []
        if self.directory:
            user = read_rules(self.directory / "rules.json")
            cwd = str(Path(payload["cwd"]).resolve())
            if cwd in self.config.get("trusted_projects", []):
                project = read_rules(Path(cwd) / ".mcpulse" / "rules.json")
        sig = signature(payload)
        remembered = bool(sig and self.db.execute("SELECT 1 FROM remembered WHERE project=? AND signature=?",
                                                  (payload["cwd"], sig)).fetchone())
        return evaluate(payload, user, project, remembered)

    def _response(self, request, action, reason="", edited=None):
        event = request.payload["hook_event_name"]
        if action == "handback":
            return {}
        if action == "stop" and event not in {"PermissionRequest", "MCP"}:
            return {"continue": False, "stopReason": "Stopped by the user through MCPulse."}
        if event == "PermissionRequest":
            result = hook_response("allow" if action in {"allow", "always", "edit"} else "deny", reason)
            if action == "stop":
                result["hookSpecificOutput"]["decision"]["interrupt"] = True
            if edited:
                result["hookSpecificOutput"]["decision"]["updatedInput"] = edited
            return result
        if event == "PreToolUse":
            if action == "text":
                questions = request.payload["tool_input"]["questions"]
                answers = parse_answers(questions, reason)
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                        "updatedInput": {**request.payload["tool_input"], "answers": answers}}}
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": reason or "User declined to answer through MCPulse."}}
        if event == "Stop":
            return {"decision": "block", "reason": "Instruction from the user via MCPulse: " + reason} if action == "text" else {}
        if event == "MCP":
            if action == "text" and request.card["kind"] == "question":
                questions = request.payload.get("tool_input", {}).get("questions", [])
                if questions:
                    reason = json.dumps(parse_answers(questions, reason))
            return {"action": action, "answer": reason, "approved": action in {"allow", "always"}}
        return {}

    def open(self, payload, request_id=None):
        payload = validate_event(payload)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.closed:
                return {}
            if request_id:
                if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{16,64}", request_id):
                    raise RequestError("Invalid request id")
                existing = self.db.execute("SELECT fingerprint,response FROM requests WHERE id=?", (request_id,)).fetchone()
                if existing:
                    if existing[0] != fingerprint:
                        raise RequestError("Request id reused with different content")
                    return self.pending.get(request_id, json.loads(existing[1]))
            event = payload["hook_event_name"]
            request_id = request_id or secrets.token_hex(8)
            risk, action = "low", "ask"
            if event == "PermissionRequest":
                policy = self._policy(payload)
                risk, action = policy["risk"], policy["action"]
                if action == "deny":
                    with self.db:
                        self._audit(request_id, "DENIED", "policy")
                    return hook_response("deny", policy["note"])
            if event != "MCP" and (self.presence == "present" or event == "Stop" and payload.get("stop_hook_active")):
                return {}
            if len(self.pending) >= 64:
                return {}
            if payload.get("kind") == "notification" and sum(p.card["kind"] == "notification" for p in self.pending.values()) >= 16:
                return {}
            original = json.dumps(payload.get("tool_input", {"message": payload.get("last_assistant_message", payload.get("message", ""))}), ensure_ascii=False, indent=2)
            is_question = event == "PreToolUse" or payload.get("kind") == "question"
            rendered = preview(original, 8000 if is_question else 1200)
            kind = {"PermissionRequest": "permission", "PreToolUse": "question", "Stop": "finished"}.get(event, payload.get("kind", "notification"))
            context = extract(payload, preview) if self.config.get("context", True) else {}
            card = {"id": request_id, "project": preview(payload["cwd"], 300),
                    "agent": agent_name(payload), "session": preview(payload["session_id"], 100), "tool": preview(payload.get("tool_name", kind), 100),
                    "input": rendered, "risk": risk, "kind": kind, "context": context,
                    "can_allow": original == rendered and all(preview(payload[k], cap) == payload[k] for k, cap in (("cwd", 300), ("session_id", 100))),
                    "can_remember": event == "PermissionRequest" and signature(payload) is not None,
                    "created": time.time()}
            if event == "MCP" and kind == "permission":
                card["risk"] = "high"
            if kind == "question":
                questions = payload.get("tool_input", {}).get("questions", [])
                card["questions"] = [{"question": preview(q["question"], 2000), "multiSelect": q.get("multiSelect", False), "options": [{"label": preview(o["label"], 100)} for o in q.get("options", [])]} for q in questions]
            if preview(payload.get("tool_name", kind), 100) != payload.get("tool_name", kind):
                card["can_allow"] = False
            if kind == "question":
                if any(preview(o["label"], 100) != o["label"] for q in payload.get("tool_input", {}).get("questions", []) for o in q.get("options", [])):
                    card["can_allow"] = False
            duration = self.config.get("stop_timeout", 1800) if event == "Stop" else self.timeout
            request = Pending(request_id, payload["session_id"], card, time.monotonic() + duration, payload)
            with self.db:
                self.db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)", (request_id, fingerprint, json.dumps(payload), json.dumps(card), time.time() + duration, "OPEN", "{}"))
                self._audit(request_id, "OPEN", "hook" if event != "MCP" else "mcp")
            self.pending[request_id] = request
            if action == "allow" and card["can_allow"]:
                self._finish(request, "allow", "policy")
                return request.response
            return request

    def _finish(self, request, action, source, reason="", state="", edited=None):
        response = self._response(request, action, reason, edited)
        state = state or {"allow": "ALLOWED", "always": "ALLOWED", "edit": "ALLOWED", "deny": "DENIED",
                          "handback": "HANDED_BACK", "stop": "STOPPED", "text": "ANSWERED", "done": "DONE"}[action]
        with self.db:
            if action == "always":
                self.db.execute("INSERT OR IGNORE INTO remembered VALUES (?,?)", (request.payload["cwd"], signature(request.payload)))
            self._audit(request.id, state, source)
            # Keep only receipt metadata after completion; receipts/replies expire after one day.
            receipt = {k: request.card[k] for k in ("id", "project", "session", "agent") if k in request.card}
            self.db.execute("UPDATE requests SET payload='{}',card=?,state=?,response=?,expires=? WHERE id=?",
                            (json.dumps(receipt), state, json.dumps(response), time.time() + 86400, request.id))
        request.state, request.response = state, response
        self.pending.pop(request.id, None)
        request.event.set()

    def answer(self, request_id, action, source, *, confirmed=False, reason=""):
        if action not in {"allow", "deny", "handback", "always", "edit", "text", "done", "stop"}:
            raise RequestError("Unknown answer")
        if not isinstance(reason, str) or len(reason) > 4000:
            raise RequestError("Reply must be text up to 4000 characters")
        with self.lock:
            request = self.pending.get(request_id)
            if request is None:
                raise RequestError("Request is closed or unknown")
            if time.monotonic() >= request.deadline:
                self._finish(request, "deny", "timeout", "MCPulse timed out; continue other safe work.", "TIMED_OUT")
                raise RequestError("Request has expired")
            kind = request.card["kind"]
            if kind == "permission" and action in {"text", "done"}:
                action = "deny"
            if kind != "permission" and action in {"allow", "always", "edit"}:
                raise RequestError("This request needs a text answer")
            if kind == "question" and action == "text" and not request.card["can_allow"]:
                raise RequestError("Question preview changed; use the terminal")
            edited = None
            if action in {"allow", "always", "edit"}:
                if not request.card["can_allow"]:
                    raise RequestError("Preview was masked or shortened; return to the terminal")
                if request.card["risk"] == "high" and confirmed is not True:
                    raise RequestError("High-risk approval requires explicit confirmation")
                if action == "always" and not request.card["can_remember"]:
                    raise RequestError("This request cannot be remembered")
                if request.payload["hook_event_name"] == "PermissionRequest" and self._policy(request.payload)["action"] == "deny":
                    raise RequestError("A policy now denies this action")
                if action == "edit":
                    if request.payload.get("tool_name") != "Bash" or not reason.strip():
                        raise RequestError("Only Bash commands can be edited")
                    edited = {**request.payload["tool_input"], "command": reason}
                    proposal = {**request.payload, "tool_input": edited}
                    if self._policy(proposal)["action"] == "deny" or preview(reason) != reason:
                        raise RequestError("Edited command denied or masked")
                    if classify(proposal)[0] == "high" and confirmed is not True:
                        raise RequestError("Edited command requires confirmation")
            self._finish(request, action, source, reason, edited=edited)
            return request.state

    def wait(self, request):
        request.event.wait(max(0, request.deadline - time.monotonic()))
        with self.lock:
            if request.id in self.pending:
                self._finish(request, "deny", "timeout", "MCPulse timed out; continue other safe work.", "TIMED_OUT")
            return request.response

    def request_status(self, request_id):
        """Dashboard metadata without exposing stored answers or raw payloads."""
        with self.lock:
            self.tick()
            row = self.db.execute("SELECT state,card FROM requests WHERE id=?", (request_id,)).fetchone()
            return {"state": row[0], "card": json.loads(row[1])} if row else None

    def result(self, request_id):
        with self.lock:
            self.tick()
            row = self.db.execute("SELECT state,response FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise RequestError("Unknown request")
            return {"state": row[0], "response": json.loads(row[1])}

    def tick(self):
        with self.lock:
            for request in list(self.pending.values()):
                if time.monotonic() >= request.deadline:
                    self._finish(request, "deny", "timeout", "MCPulse timed out; continue other safe work.", "TIMED_OUT")
            with self.db:
                self.db.execute("DELETE FROM requests WHERE state!='OPEN' AND expires<?", (time.time(),))
                self.db.execute("DELETE FROM messages WHERE request NOT IN (SELECT id FROM requests)")
                self.db.execute("DELETE FROM inbox WHERE at<?", (time.time() - 7 * 86400,))
                self.db.execute("DELETE FROM audit WHERE at<?", (time.time() - self.config.get("audit_retention_days", 30) * 86400,))

    def set_presence(self, mode):
        if mode not in {"present", "away", "auto"}:
            raise RequestError("Use present, away, or auto")
        with self.lock:
            self.presence_mode = mode
            if mode != "auto":
                self.detect_presence(mode)

    def detect_presence(self, mode):
        with self.lock:
            self.presence = mode
            if mode == "present":
                for request in list(self.pending.values()):
                    if request.payload["hook_event_name"] != "MCP":
                        self._finish(request, "handback", "presence")

    def cancel_session(self, session):
        with self.lock:
            for request in list(self.pending.values()):
                if request.session == session:
                    self._finish(request, "deny", "session", "Session ended.", "CANCELLED")
            with self.db:
                self.db.execute("DELETE FROM sessions WHERE id=?", (session,))

    def session_event(self, payload):
        with self.lock:
            sid = payload["session_id"]
            old = self.db.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
            data = json.loads(old[0]) if old else {"id": preview(sid, 100), "started": time.time()}
            data.update(agent=agent_name(payload), project=preview(payload["cwd"], 300), updated=time.time(), state=payload["hook_event_name"])
            if "prompt" in payload:
                data["task"] = preview(str(payload["prompt"]), 600)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)", (sid, json.dumps(data)))
                self.db.execute("DELETE FROM sessions WHERE id IN (SELECT id FROM sessions ORDER BY json_extract(data,'$.updated') DESC LIMIT -1 OFFSET 100)")

    def status(self):
        with self.lock:
            return {"presence": self.presence, "presence_mode": self.presence_mode,
                    "pending": [dict(p.card) for p in self.pending.values()],
                    "sessions": [json.loads(row[0]) for row in self.db.execute("SELECT data FROM sessions")],
                    "channels": dict(self.channel_health), "muted_until": self.muted_until}

    def audit(self):
        with self.lock:
            return [dict(zip(("id", "state", "source", "at"), row)) for row in self.db.execute("SELECT id,state,source,at FROM audit ORDER BY rowid DESC LIMIT 1000")]

    def rules(self, forget=None):
        with self.lock:
            if forget is not None:
                with self.db:
                    self.db.execute("DELETE FROM remembered WHERE project=?", (forget,))
            return [{"project": preview(p, 300), "signature": preview(s, 1200)} for p, s in self.db.execute("SELECT project,signature FROM remembered")]

    def message_ref(self, channel, request, reference=None):
        with self.lock:
            if reference is not None:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)", (channel, request, json.dumps(reference)))
            row = self.db.execute("SELECT reference FROM messages WHERE channel=? AND request=?", (channel, request)).fetchone()
            return json.loads(row[0]) if row else None

    def consume_update(self, channel, nonce):
        if not isinstance(nonce, (str, int)) or not str(nonce) or len(str(nonce)) > 200:
            return False
        with self.lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO inbox VALUES (?,?,?)", (channel, str(nonce), time.time())).rowcount == 1

    def daily_digest(self, hour):
        if type(hour) is not int or time.localtime().tm_hour != hour:
            return
        today = time.strftime("%Y-%m-%d")
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key='digest_date'").fetchone()
            if row and row[0] == today:
                return
            counts = dict(self.db.execute("SELECT state,count(*) FROM audit WHERE at>? AND state!='OPEN' GROUP BY state", (time.time() - 86400,)))
            message = "MCPulse daily digest: " + (", ".join(f"{state.lower()}: {count}" for state, count in counts.items()) or "No decisions in the last 24 hours.")
            queued = self.open({"hook_event_name": "MCP", "session_id": "daily-digest", "agent": "MCPulse", "cwd": str(self.directory or Path.cwd()), "kind": "notification", "message": message})
            if isinstance(queued, Pending):
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('digest_date',?)", (today,))

    def close(self, preserve=False):
        with self.lock:
            if self.closed:
                return
            if not preserve:
                for request in list(self.pending.values()):
                    self._finish(request, "handback", "shutdown")
            self.closed = True
            self.db.close()


def agent_name(payload):
    """Display metadata only; the sender's name does not establish trust."""
    name = payload.get("agent", "MCP client" if payload.get("hook_event_name") == "MCP" else "Claude CLI").strip()
    aliases = {"claude-cli": "Claude CLI", "claude-code": "Claude CLI", "claude code": "Claude CLI",
               "claude-cowork": "Claude Cowork", "claude cowork": "Claude Cowork", "opencode": "OpenCode"}
    return preview(aliases.get(name.lower(), name), 100)


def validate_event(payload):
    if not isinstance(payload, dict):
        raise RequestError("Expected an object")
    if "agent" in payload and (not isinstance(payload["agent"], str) or not payload["agent"].strip() or len(payload["agent"]) > 100):
        raise RequestError("Invalid agent name")
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        return validate_hook(payload)
    if event == "SessionEnd" and "cwd" not in payload:
        payload = {**payload, "cwd": "."}
    for name in ("session_id", "cwd"):
        if not isinstance(payload.get(name), str) or not 0 < len(payload[name]) <= 4096:
            raise RequestError("Invalid " + name)
    if event == "PreToolUse":
        if payload.get("tool_name") != "AskUserQuestion" or not isinstance(payload.get("tool_input"), dict):
            raise RequestError("Expected AskUserQuestion")
        validate_questions(payload["tool_input"].get("questions"))
    elif event == "MCP":
        if payload.get("kind") not in {"question", "permission", "notification"}:
            raise RequestError("Invalid MCP request kind")
    elif event not in {"Stop", "SessionStart", "SessionEnd", "UserPromptSubmit", "Notification", "PermissionDenied", "StopFailure"}:
        raise RequestError("Unsupported hook event")
    return payload


def validate_questions(questions):
    if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
        raise RequestError("Expected 1–4 questions")
    seen = set()
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("question"), str) or not question["question"] or len(question["question"]) > 2000:
            raise RequestError("Invalid question")
        if question["question"] in seen:
            raise RequestError("Duplicate question")
        seen.add(question["question"])
        options = question.get("options", [])
        if not isinstance(options, list) or len(options) > 8:
            raise RequestError("Invalid options")
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not option["label"]:
                raise RequestError("Invalid option")
        if type(question.get("multiSelect", False)) is not bool:
            raise RequestError("Invalid multiSelect")


def parse_answers(questions, reply):
    literal = False
    try:
        supplied = json.loads(reply)
    except ValueError:
        supplied = None
    if isinstance(supplied, dict) and set(supplied) == {q["question"] for q in questions}:
        values = [supplied[q["question"]] for q in questions]
        literal = True
    elif len(questions) == 1:
        values = [reply.strip()]
    else:
        raise RequestError("For multiple questions, send a JSON object mapping every question to the answer text")
    result = {}
    for question, value in zip(questions, values):
        if not isinstance(value, str) or not value.strip():
            raise RequestError("Each question needs an answer")
        options = question.get("options", [])
        if not literal and re.fullmatch(r"\d+(?:\s*,\s*\d+)*", value):
            indices = [int(n) - 1 for n in value.split(",")]
            if len(set(indices)) != len(indices) or any(n < 0 or n >= len(options) for n in indices) or len(indices) > 1 and not question.get("multiSelect"):
                raise RequestError("Invalid option selection")
            value = ", ".join(options[n]["label"] for n in indices)
        result[question["question"]] = value
    return result
