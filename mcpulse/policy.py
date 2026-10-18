"""Conservative policy and exact remembered approvals; no shell execution."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

READ_TOOLS = {"Read", "Glob", "Grep", "LS", "WebSearch"}
COMPOUND = re.compile(r"[;&|<>`\n\r]|\$[({]|[\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
HIGH = re.compile(r"\b(sudo|deploy|publish|push|reset|clean|checkout|switch|rm|mv|chmod|chown|curl|wget|ssh|eval|exec|docker|kubectl|terraform|DROP|DELETE)\b", re.I)


def classify(payload):
    tool, data = payload["tool_name"], payload["tool_input"]
    command = data.get("command", "")
    blocked = tool == "Bash" and bool(re.search(
        r'''\brm\s+(?:-[A-Za-z]+\s+)*["']?(?:/|~|\$HOME|\$\{HOME\})["']?(?:\s|$)|:\(\)\s*\{''', command))
    if tool in READ_TOOLS:
        return "low", blocked
    if tool == "Bash":
        if COMPOUND.search(command) or HIGH.search(command):
            return "high", blocked
        try:
            words = shlex.split(command)
        except ValueError:
            return "high", blocked
        if words in (["pwd"], ["git", "status"], ["git", "diff"], ["git", "log"]):
            return "low", blocked
        if words[:2] in (["npm", "test"], ["npm", "run"], ["python", "-m"], ["python3", "-m"]):
            return "medium", blocked
    return "high", blocked


def signature(payload):
    """Remember the exact tool and complete input, never a broad executable prefix."""
    risk, blocked = classify(payload)
    if risk == "high" or blocked:
        return None
    raw = json.dumps([payload["tool_name"], payload["tool_input"]], sort_keys=True, ensure_ascii=False)
    return raw if len(raw) <= 4096 else None


def read_rules(path):
    if not path.exists():
        return []
    if path.stat().st_size > 65536:
        raise ValueError("Rules file is too large")
    rules = json.loads(path.read_text())
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("Rules must be an array of at most 100 rules")
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("action") not in {"allow", "deny", "ask"}:
            raise ValueError("Invalid rule action")
        for name in ("tool", "match"):
            if not isinstance(rule.get(name, ".*"), str) or len(rule.get(name, ".*")) > 256:
                raise ValueError("Invalid rule pattern")
            try:
                re.compile(rule.get(name, ".*"))
            except re.error:
                raise ValueError("Invalid rule regular expression") from None
    return rules


def evaluate(payload, user_rules=(), project_rules=(), remembered=False):
    risk, blocked = classify(payload)
    if blocked:
        return {"action": "deny", "risk": "high", "note": "Built-in destructive-command rule"}
    data = payload["tool_input"]
    target = data.get("command", data.get("file_path", data.get("url", json.dumps(data, sort_keys=True))))
    if not isinstance(target, str):
        target = json.dumps(target)
    rules = [*project_rules, *user_rules]
    if rules:
        try:
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--match"],
                input=json.dumps({"rules": rules, "tool": payload["tool_name"], "target": target[:65536]}),
                capture_output=True, text=True, timeout=0.5, check=True)
            matches = [rules[index] for index in json.loads(completed.stdout)]
        except (subprocess.SubprocessError, ValueError):
            raise ValueError("Policy evaluation failed or exceeded its time budget") from None
    else:
        matches = []
    deny = next((r for r in matches if r["action"] == "deny"), None)
    if deny:
        return {"action": "deny", "risk": risk, "note": deny.get("note", "Policy deny")}
    if remembered and risk != "high":
        return {"action": "allow", "risk": risk, "note": "Exact remembered approval"}
    if matches:
        rule = matches[0]
        # A glob must never silently authorize arbitrary shell execution.
        action = rule["action"] if risk != "high" else "ask"
        return {"action": action, "risk": risk, "note": rule.get("note", "Configured rule")}
    return {"action": "ask", "risk": risk, "note": "Human decision required"}


if __name__ == "__main__":
    data = json.load(sys.stdin)
    print(json.dumps([index for index, rule in enumerate(data["rules"])
                     if re.search(rule.get("tool", ".*"), data["tool"])
                     and re.search(rule.get("match", ".*"), data["target"])]))
