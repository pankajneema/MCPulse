"""Python stdio MCP server: bounded JSON-RPC, tools, cancellation, no stdout logs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import sys
import threading

from . import __version__
from .client import relay
from .core import validate_questions

TOOLS = [
    {"name": "ask_human", "description": "Ask the user a question through their configured phone channels.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "multi_select": {"type": "boolean"}}, "required": ["question"], "additionalProperties": False}},
    {"name": "request_approval", "description": "Request explicit human approval before an action. Does not execute the action.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"], "additionalProperties": False}},
    {"name": "notify_human", "description": "Queue a notification to the user's configured channels.",
     "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False}},
]


def serve(config, input_stream=None, output_stream=None):
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    lock = threading.Lock()
    active = {}
    initialized = False
    ready = False
    session = "mcp-" + secrets.token_hex(8)
    executor = ThreadPoolExecutor(max_workers=4)

    def send(value):
        with lock:
            output_stream.write(json.dumps(value) + "\n")
            output_stream.flush()

    def error(rid, code, message):
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def call(rid, params, cancelled):
        try:
            name, args = params.get("name"), params.get("arguments", {})
            if not isinstance(args, dict):
                raise ValueError("Arguments must be an object")
            field = {"ask_human": "question", "request_approval": "action", "notify_human": "message"}.get(name)
            if field is None or not isinstance(args.get(field), str) or not 0 < len(args[field]) <= 4000:
                raise ValueError("Unknown tool or invalid required text")
            payload = {"hook_event_name": "MCP", "session_id": session, "cwd": str(Path.cwd()),
                       "kind": {"ask_human": "question", "request_approval": "permission", "notify_human": "notification"}[name],
                       "message": args[field]}
            if name == "ask_human":
                options = args.get("options", [])
                if not isinstance(options, list) or not all(isinstance(s, str) for s in options):
                    raise ValueError("Invalid options")
                question = {"question": args[field], "options": [{"label": label} for label in options], "multiSelect": args.get("multi_select", False)}
                validate_questions([question])
                payload["tool_input"] = {"questions": [question]}
            result = relay(config, payload, cancelled=cancelled)
            if not result:
                result = {"approved": False, "answer": "No remote answer. Ask in the terminal or choose a safe default."}
            if not cancelled.is_set():
                send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}})
        except (ValueError, TypeError, KeyError) as failure:
            if not cancelled.is_set():
                send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(failure)}], "isError": True}})
        finally:
            with lock:
                active.pop(rid, None)

    try:
        while True:
            line = input_stream.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                error(None, -32600, "Message too large")
                break
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError()
            except ValueError:
                error(None, -32700, "Invalid JSON-RPC")
                continue
            rid, method, params = message.get("id"), message.get("method"), message.get("params", {})
            if not isinstance(params, dict) or rid is not None and (type(rid) not in {int, str}):
                error(None, -32600, "Invalid request")
                continue
            if method == "notifications/initialized" and initialized:
                ready = True
                continue
            if method == "notifications/cancelled":
                with lock:
                    cancellation = active.get(params.get("requestId")) if type(params.get("requestId")) in {int, str} else None
                if cancellation:
                    cancellation.set()
                continue
            if rid is None:
                continue
            if method == "initialize" and not initialized:
                initialized = True
                send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mcpulse", "version": __version__}}})
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": rid, "result": {}})
            elif not ready:
                error(rid, -32002, "Initialize first")
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                with lock:
                    available = len(active) < 4 and rid not in active
                    if available:
                        cancellation = active[rid] = threading.Event()
                if not available:
                    error(rid, -32000, "Too many active calls or duplicate id")
                else:
                    executor.submit(call, rid, params, cancellation)
            else:
                error(rid, -32601, "Method not found")
    finally:
        with lock:
            for cancelled in active.values():
                cancelled.set()
        executor.shutdown(wait=True, cancel_futures=True)
