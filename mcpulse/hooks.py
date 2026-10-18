"""Explicit hook installation, preserving unrelated settings and a byte-exact backup."""

import json
from pathlib import Path
from urllib.parse import urlparse

from .config import key, private_write

EVENTS = ("PermissionRequest", "PreToolUse", "Stop", "Notification", "PermissionDenied", "StopFailure", "SessionStart", "SessionEnd", "UserPromptSubmit")


def owned(hook: dict) -> bool:
    if hook.get("type") == "command":
        return hook.get("statusMessage") == "MCPulse: waiting for your answer" and "run_mcpulse.py" in hook.get("command", "")
    url = urlparse(hook.get("url", ""))
    return (hook.get("type") == "http" and url.hostname == "127.0.0.1"
            and url.path in {"/hook/" + event for event in EVENTS}
            and "x-mcpulse-key" in hook.get("headers", {})
            and hook.get("statusMessage") == "MCPulse: waiting for your answer")


def remove(settings: dict) -> None:
    hooks = settings.get("hooks", {})
    for event in EVENTS:
        groups = []
        for group in hooks.get(event, []):
            kept = [hook for hook in group.get("hooks", []) if not owned(hook)]
            if kept or not group.get("hooks"):
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)


def group_for(event, config, bridge=None):
    hook = {"type": "http", "url": f"http://127.0.0.1:{config['port']}/hook/{event}",
            "timeout": config["permission_timeout"] + 60 if event == "PermissionRequest" else 10,
            "headers": {"x-mcpulse-key": key(config)},
            "statusMessage": "MCPulse: waiting for your answer"}
    if event in {"PermissionRequest", "PreToolUse"}:
        hook["timeout"] = config["permission_timeout"] + 60
    elif event == "Stop":
        hook["timeout"] = config.get("stop_timeout", 1800) + 60
    if bridge:
        import shlex, subprocess, os
        args = [*bridge, "hook", event]
        hook = {"type": "command", "command": subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args),
                "timeout": hook["timeout"], "statusMessage": hook["statusMessage"]}
    group = {"hooks": [hook]}
    if event == "PreToolUse":
        group["matcher"] = "AskUserQuestion"
    return group


def install(path: Path, config: dict, *, bridge=None) -> None:
    backup = path.with_name(path.name + ".mcpulse-backup.json")
    original = path.read_text(encoding="utf-8") if path.exists() else None
    settings = json.loads(original) if original is not None else {}
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        raise ValueError("Invalid Claude settings")
    state = json.loads(backup.read_text(encoding="utf-8")) if backup.exists() else {"original": original}
    remove(settings)
    hooks = settings.setdefault("hooks", {})
    for event in EVENTS:
        hooks.setdefault(event, []).append(group_for(event, config, bridge))
    rendered = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    state["installed"] = rendered
    private_write(backup, json.dumps(state))
    private_write(path, rendered)


def uninstall(path: Path) -> None:
    backup = path.with_name(path.name + ".mcpulse-backup.json")
    if not backup.exists():
        raise ValueError("No MCPulse backup found; settings left untouched")
    state = json.loads(backup.read_text(encoding="utf-8"))
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == state["installed"]:
            if state["original"] is None:
                path.unlink()
            else:
                private_write(path, state["original"])
        else:
            settings = json.loads(current)
            remove(settings)
            private_write(path, json.dumps(settings, indent=2) + "\n")
    backup.unlink()


def check_installation(path, config, bridge):
    """Check event coverage and connection targets without exposing hook secrets."""
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        configured = settings.get("hooks", {})
        if not isinstance(configured, dict):
            raise ValueError()
        if settings.get("disableAllHooks"):
            return {"ok": False, "detail": "Hooks are disabled in this settings file"}
        missing, mismatched = [], []
        for event in EVENTS:
            groups = configured.get(event, [])
            if not isinstance(groups, list):
                raise ValueError()
            installed = []
            for group in groups:
                entries = group.get("hooks", [])
                if not isinstance(entries, list):
                    raise ValueError()
                for hook in entries:
                    if owned(hook):
                        installed.append((group, hook))
            if not installed:
                missing.append(event)
                continue
            if len(installed) != 1:
                mismatched.append(event)
                continue
            group, hook = installed[0]
            expected = group_for(event, config, bridge if hook.get("type") == "command" else None)
            expected_hook = expected["hooks"][0]
            if (group.get("matcher", "") != expected.get("matcher", "")
                    or group.get("if") or hook.get("if") or hook.get("async") or hook.get("asyncRewake")
                    or any(hook.get(k) != v for k, v in expected_hook.items())):
                mismatched.append(event)
        ok = not missing and not mismatched
        return {"ok": ok, "detail": "Installed for this configuration" if ok else "Missing, duplicate, or stale hooks",
                "missing_events": missing, "mismatched_events": mismatched}
    except FileNotFoundError:
        return {"ok": False, "detail": "Settings file does not exist"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ok": False, "detail": "Settings file is unreadable or malformed"}
