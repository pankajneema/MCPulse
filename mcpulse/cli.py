"""Python-only command line, setup, services, hook bridge, and MCP entry point."""
import argparse
import csv
import getpass
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time

from . import __version__, hooks
from .client import api, relay
from .config import home, initialize, key, load, private_write
from .core import Broker, preview
from .presence import Presence, idle_seconds
from .server import Server
from .telegram import Telegram, pair
from .replies import pin_hash
from . import policy, service


def token_for(directory):
    token = os.environ.get("MCPULSE_TELEGRAM_TOKEN")
    path = directory / "telegram-token"
    if token is None and path.exists():
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError("Telegram token file must have mode 600")
        token = path.read_text().strip()
    return token


def bridge(directory):
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "run_mcpulse.py"), "--home", str(directory)]


def start(config, directory):
    if os.name != "nt":
        os.umask(0o077)
    broker = Broker(str(directory / "audit.sqlite3"), config["presence"], config["permission_timeout"], config=config, directory=directory)
    if config["presence"] == "auto":
        broker.presence = "present"
    try:
        server = Server(broker, key(config), config["port"])
    except Exception:
        broker.close(preserve=True)
        raise
    adapters, workers = [], []
    web_server = None
    stopped = threading.Event()
    presence = Presence(broker, config)
    def monitor():
        while not stopped.wait(1):
            try:
                presence.tick()
                broker.daily_digest(config.get("daily_digest_hour"))
            except (OSError, ValueError, subprocess.SubprocessError):
                broker.channel_health["presence"] = "sensor unavailable; use away/present"
    try:
        presence.tick()
        if config.get("telegram_user_id"):
            token = token_for(directory)
            if not token:
                raise ValueError("Telegram token missing; run channel pair")
            adapters.append(Telegram(broker, token, config["telegram_user_id"]))
        from .channels import configured_adapters
        adapters.extend(configured_adapters(broker, config, directory))
        if config.get("web_origin"):
            from .web import WebServer
            from .channels import WhatsApp
            web_server = WebServer(broker, config, next((a for a in adapters if isinstance(a, WhatsApp)), None))
            web_thread = threading.Thread(target=web_server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
            web_thread.start()
        for adapter in adapters:
            thread = threading.Thread(target=adapter.run, daemon=True)
            thread.start()
            workers.append(thread)
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        workers.append(thread)
        print(f"MCPulse {__version__} on 127.0.0.1:{config['port']} · {config['presence']}", flush=True)
        print("Ctrl-C to stop. Use 'python -m mcpulse away' to relay requests.", flush=True)
        def stop_on_signal(*_):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, stop_on_signal)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGTERM, previous)
    finally:
        stopped.set()
        if web_server:
            web_server.shutdown()
            web_server.server_close()
        for adapter in adapters:
            adapter.stopped.set()
        for worker in workers:
            worker.join(timeout=20)
        presence.close()
        broker.close(preserve=server.preserve_on_shutdown)
        server.server_close()


def save_config(directory, config):
    private_write(directory / "config.json", json.dumps(config, indent=2) + "\n")


def doctor(config, directory, settings=None):
    settings = (settings or Path.home() / ".claude" / "settings.json").expanduser().resolve()
    findings = [{"check": "configuration", "ok": True, "path": str(directory / "config.json")}]
    try:
        status = api(config, "/api/status", timeout=2)
        findings.append({"check": "daemon", "ok": True, "detail": status["presence"]})
        findings.extend({"check": name, "ok": state == "connected", "detail": state} for name, state in status.get("channels", {}).items())
    except (ConnectionError, ValueError):
        findings.append({"check": "daemon", "ok": False, "detail": "Daemon unavailable or configuration does not match the running daemon", "fix": "Start or restart MCPulse using this configuration"})
    hook_check = hooks.check_installation(settings, config, bridge(directory))
    findings.append({"check": "Claude hooks", "settings": str(settings), **hook_check,
                     "fix": "Restart Claude after hook changes" if hook_check["ok"] else "Run hooks install --settings with this settings path"})
    findings.append({"check": "idle sensor", "ok": idle_seconds() is not None, "optional": True,
                     "fix": "Use explicit away/present if your desktop does not expose idle time"})
    return findings


def parser():
    root = argparse.ArgumentParser(description="MCPulse — Python human-in-the-loop relay")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup")
    setup.add_argument("--telegram-user", type=int)
    setup.add_argument("--guided", action="store_true")
    commands.add_parser("start")
    ui = commands.add_parser("ui", help="Open the local dashboard")
    ui.add_argument("--no-open", action="store_true", help="Print a one-time login URL")
    ui_preview = commands.add_parser("preview", help="Run an isolated dashboard with sample data")
    ui_preview.add_argument("--port", type=int, default=7720)
    ui_preview.add_argument("--no-open", action="store_true")
    for name in ("status", "away", "present", "auto", "stop", "restart", "test"):
        commands.add_parser(name)
    diagnostics = commands.add_parser("doctor", help="Check setup without modifying it")
    diagnostics.add_argument("--settings", type=Path, help="Claude settings file to inspect (defaults to user settings)")
    diagnostics.add_argument("--strict", action="store_true", help="Exit with status 1 when a required setup check fails")
    demo = commands.add_parser("demo")
    demo.add_argument("--kind", choices=["permission", "question", "finished"], default="permission")
    audit = commands.add_parser("audit")
    audit.add_argument("--csv", action="store_true")
    answer = commands.add_parser("answer")
    answer.add_argument("id")
    answer.add_argument("action", choices=["allow", "deny", "handback", "always", "edit", "text", "done", "stop"])
    answer.add_argument("reply", nargs="?", default="")
    answer.add_argument("--confirm", action="store_true")
    answer.add_argument("--reason", default="")
    hook = commands.add_parser("hooks")
    hook.add_argument("action", choices=["install", "uninstall", "show"])
    hook.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    hook.add_argument("--http", action="store_true", help="Use direct HTTP hooks instead of reconnecting command hooks")
    internal = commands.add_parser("hook", help="Handle an installed Claude hook")
    internal.add_argument("event", choices=hooks.EVENTS)
    mcp = commands.add_parser("mcp")
    mcp.add_argument("action", choices=["serve", "install", "uninstall", "show"])
    mcp.add_argument("--scope", choices=["user", "project", "local"], default="user")
    svc = commands.add_parser("service")
    svc.add_argument("action", choices=["install", "uninstall", "show"])
    channel = commands.add_parser("channel")
    channel.add_argument("action", choices=["pair", "list", "remove", "configure"])
    channel.add_argument("name", nargs="?", default="telegram", choices=["telegram", "slack", "ntfy", "email", "whatsapp"])
    channel.add_argument("--file", type=Path, help="Private JSON channel configuration")
    pin = commands.add_parser("pin")
    pin.add_argument("action", choices=["set", "clear"])
    rules = commands.add_parser("rules")
    rules.add_argument("action", choices=["list", "test", "forget", "trust"])
    rules.add_argument("value", nargs="?")
    config = commands.add_parser("config")
    config.add_argument("action", choices=["get", "set"])
    config.add_argument("name", nargs="?")
    config.add_argument("value", nargs="?")
    mute = commands.add_parser("mute")
    mute.add_argument("seconds", type=int, nargs="?", default=3600)
    commands.add_parser("unmute")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    if args.home:
        os.environ["MCPULSE_HOME"] = str(args.home.expanduser().resolve())
    directory = home().expanduser().resolve()
    try:
        if args.command == "setup":
            config = initialize(directory)
            if args.telegram_user is not None or args.guided:
                token = getpass.getpass("Telegram bot token (hidden): ").strip()
                if ":" not in token or any(c.isspace() for c in token):
                    raise ValueError("Invalid Telegram token")
                user_id = args.telegram_user or pair(token)
                if user_id <= 0:
                    raise ValueError("Telegram user id must be positive")
                private_write(directory / "telegram-token", token)
                config["telegram_user_id"] = user_id
                save_config(directory, config)
            print("Configuration ready. Next: python -m mcpulse start")
            return 0
        if args.command == "preview":
            from .ui import preview_server
            preview_server(args.port, open_browser=not args.no_open)
            return 0
        if args.command == "doctor":
            try:
                config = load(directory)
            except FileNotFoundError:
                result = [{"check": "configuration", "ok": False, "path": str(directory / "config.json"),
                           "detail": "Configuration is missing", "fix": "python -m mcpulse setup"}]
            except (OSError, ValueError, TypeError, AttributeError):
                result = [{"check": "configuration", "ok": False, "path": str(directory / "config.json"),
                           "detail": "Configuration is unreadable, invalid, or not private", "fix": "Repair the configuration file and its permissions"}]
            else:
                result = doctor(config, directory, args.settings)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return int(args.strict and any(not row["ok"] and not row.get("optional") for row in result))
        config = load(directory)
        if args.command == "ui":
            url = api(config, "/api/ui-login", {})["url"]
            print(url, flush=True)
            if not args.no_open:
                import webbrowser
                webbrowser.open(url)
            return 0
        if args.command == "hook":
            try:
                raw = sys.stdin.buffer.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Hook too large")
                payload = json.loads(raw)
                if payload.get("hook_event_name") != args.event:
                    raise ValueError("Event mismatch")
                result = relay(config, payload)
            except (ValueError, ConnectionError, AttributeError):
                result = {}
            print(json.dumps(result))
            return 0
        if args.command == "start":
            start(config, directory)
            return 0
        if args.command in {"stop", "restart"}:
            if args.command == "restart":
                config["presence"] = api(config, "/api/status")["presence_mode"]
            api(config, "/api/shutdown", {"preserve": args.command == "restart"})
            if args.command == "restart":
                for _ in range(100):
                    try:
                        api(config, "/api/status", timeout=1)
                    except ConnectionError:
                        break
                    time.sleep(0.2)
                start(config, directory)
            return 0
        if args.command == "hooks":
            if args.action == "install":
                hooks.install(args.settings, config, bridge=None if args.http else bridge(directory))
            elif args.action == "uninstall":
                hooks.uninstall(args.settings)
            else:
                print(preview(args.settings.read_text(), 20000))
                return 0
            print("Hooks updated. Restart Claude Code.")
            return 0
        if args.command == "mcp":
            if args.action == "serve":
                from .mcp import serve
                serve(config)
                return 0
            command = ["claude", "mcp", "add", "--transport", "stdio", "--scope", args.scope, "mcpulse", "--", *bridge(directory), "mcp", "serve"]
            if args.action == "show":
                result = {"command": bridge(directory)[0], "args": [*bridge(directory)[1:], "mcp", "serve"]}
            else:
                if args.action == "uninstall":
                    command = ["claude", "mcp", "remove", "--scope", args.scope, "mcpulse"]
                subprocess.run(command, check=True)
                return 0
        elif args.command == "service":
            result = service.manage(args.action, directory)
        elif args.command in {"status", "audit"}:
            result = api(config, "/api/" + args.command)
            if args.command == "audit" and args.csv:
                writer = csv.DictWriter(sys.stdout, fieldnames=["id", "state", "source", "at"])
                writer.writeheader()
                writer.writerows(result)
                return 0
        elif args.command in {"away", "present", "auto"}:
            result = api(config, "/api/presence", {"mode": args.command})
        elif args.command in {"mute", "unmute"}:
            result = api(config, "/api/mute", {"seconds": args.seconds if args.command == "mute" else 0})
        elif args.command == "answer":
            result = api(config, "/api/answer", {"id": args.id, "action": args.action, "confirmed": args.confirm, "reason": args.reason or args.reply})
        elif args.command == "demo":
            print("Synthetic request: no command will execute. Answer via a channel or a second terminal.", flush=True)
            payload = {"session_id": "mcpulse-demo", "cwd": str(Path.cwd()), "hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "git status"}}
            if args.kind == "question":
                payload.update(hook_event_name="PreToolUse", tool_name="AskUserQuestion", tool_input={"questions": [{"question": "Which test runner?", "options": [{"label": "unittest"}, {"label": "pytest"}]}]})
            elif args.kind == "finished":
                payload = {"session_id": "mcpulse-demo", "cwd": str(Path.cwd()), "hook_event_name": "Stop", "last_assistant_message": "Demo complete. Reply with the next instruction."}
            result = relay(config, payload)
        elif args.command == "test":
            result = api(config, "/api/submit", {"payload": {"hook_event_name": "MCP", "kind": "notification", "session_id": "test", "cwd": str(Path.cwd()), "message": "MCPulse channel test"}})
        elif args.command == "pin":
            if args.action == "set":
                pin = getpass.getpass("New PIN (at least 6 digits): ")
                if not pin.isdigit() or len(pin) < 6 or pin != getpass.getpass("Repeat PIN: "):
                    raise ValueError("PINs must match and contain at least 6 digits")
                config["pin_salt"] = secrets.token_hex(16)
                config["pin_hash"] = pin_hash(pin, config["pin_salt"])
            else:
                config.pop("pin_hash", None)
                config.pop("pin_salt", None)
            save_config(directory, config)
            result = {"pin": args.action, "restart_required": True}
        elif args.command == "channel":
            if args.action == "pair":
                if args.name != "telegram":
                    raise ValueError("Pairing applies to Telegram; other channels use configure")
                token = token_for(directory) or getpass.getpass("Telegram bot token: ").strip()
                config["telegram_user_id"] = pair(token)
                private_write(directory / "telegram-token", token)
                save_config(directory, config)
                result = {"paired": True, "restart_required": True}
            elif args.action == "configure":
                if not args.file or args.name == "telegram":
                    raise ValueError("Use --file for channel JSON; use pair for Telegram")
                from .channels import validate_channel
                data = json.loads(args.file.read_text())
                validate_channel(args.name, data)
                private_write(directory / (args.name + ".json"), json.dumps(data, indent=2))
                result = {"configured": args.name, "restart_required": True}
            elif args.action == "remove":
                if args.name == "telegram":
                    config["telegram_user_id"] = None
                    save_config(directory, config)
                    (directory / "telegram-token").unlink(missing_ok=True)
                else:
                    (directory / (args.name + ".json")).unlink(missing_ok=True)
                result = {"removed": args.name, "restart_required": True}
            else:
                result = {"telegram": bool(config.get("telegram_user_id")), **{name: (directory / (name + ".json")).exists() for name in ("slack", "ntfy", "email", "whatsapp")}}
        elif args.command == "rules":
            if args.action == "list":
                result = {"remembered": api(config, "/api/rules"), "user": policy.read_rules(directory / "rules.json")}
            elif args.action == "forget":
                if not args.value:
                    raise ValueError("Provide the project path")
                result = api(config, "/api/rules/forget", {"project": str(Path(args.value).resolve())})
            elif args.action == "trust":
                if not args.value:
                    raise ValueError("Provide the project path")
                project = str(Path(args.value).resolve())
                config["trusted_projects"] = sorted(set([*config["trusted_projects"], project]))
                save_config(directory, config)
                result = {"trusted_project": project, "restart_required": True}
            else:
                if not args.value:
                    raise ValueError("Provide a command to test")
                result = policy.evaluate({"tool_name": "Bash", "tool_input": {"command": args.value}}, policy.read_rules(directory / "rules.json"))
        elif args.command == "config":
            allowed = {"port", "presence", "permission_timeout", "stop_timeout", "idle_away_seconds", "keep_awake", "context", "web_origin", "web_port", "daily_digest_hour", "audit_retention_days"}
            if args.action == "get":
                result = {k: v for k, v in config.items() if k in allowed}
                if args.name:
                    result = {args.name: result.get(args.name)}
            else:
                if args.name not in allowed or args.value is None:
                    raise ValueError("Unknown setting or missing value")
                try:
                    value = json.loads(args.value)
                except ValueError:
                    value = args.value
                previous = dict(config)
                config[args.name] = value
                save_config(directory, config)
                try:
                    load(directory)
                except ValueError:
                    save_config(directory, previous)
                    raise
                result = {"updated": args.name, "restart_required": True}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except FileNotFoundError:
        print("MCPulse: file or executable missing; check setup and paths", file=sys.stderr)
    except (ValueError, OSError, ConnectionError, RuntimeError, subprocess.SubprocessError) as error:
        print("MCPulse: " + str(error), file=sys.stderr)
    except (KeyError, TypeError, AttributeError):
        print("MCPulse: invalid configuration or request", file=sys.stderr)
    return 1
