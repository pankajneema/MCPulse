"""Bounded transcript parsing. Never execute repository-controlled commands."""
import json
from pathlib import Path
import subprocess


def extract(payload, sanitize):
    result = {"task": "", "last_message": "", "steps": [], "branch": ""}
    path = payload.get("transcript_path")
    if isinstance(path, str):
        file = Path(path).expanduser()
        # Hooks may only read JSONL transcripts beneath Claude's own directory.
        try:
            if file.resolve().is_relative_to((Path.home() / ".claude").resolve()) and file.suffix == ".jsonl":
                with file.open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 131072))
                    lines = stream.read(131072).decode("utf-8", "replace").splitlines()
                for line in lines[-100:]:
                    try:
                        entry = json.loads(line)
                        message = entry.get("message", {})
                        content = message.get("content", "")
                        role = message.get("role", entry.get("type"))
                        if isinstance(content, str):
                            text = content
                        else:
                            text = "\n".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "tool_use":
                                    result["steps"].append(sanitize(item.get("name", "Tool"), 100))
                        if role == "user" and text:
                            result["task"] = sanitize(text, 600)
                        elif role == "assistant" and text:
                            result["last_message"] = sanitize(text, 600)
                    except (ValueError, TypeError, AttributeError):
                        continue
        except (OSError, ValueError):
            pass
    try:
        branch = subprocess.run(["git", "-C", payload["cwd"], "symbolic-ref", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=1, check=False)
        if branch.returncode == 0:
            result["branch"] = sanitize(branch.stdout.strip(), 100)
    except (OSError, subprocess.TimeoutExpired):
        pass
    result["steps"] = result["steps"][-3:]
    return result
