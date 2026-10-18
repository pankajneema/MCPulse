# 001 — Python-only foundation

Date: 2026-10-18. Accepted: the user's Python-only instruction supersedes the TypeScript/Bun stack in the original brief.

Use Python 3.11+, argparse, http.server, urllib, sqlite3, threading, dataclasses, and unittest. No runtime dependencies. Setuptools is used only for optional package installation. Keep a single package until components justify independent distributions.

The first runnable slice is permission hook → authenticated local daemon → Telegram/private local answer → hook response. No auto-approval is implemented. Threaded requests are bounded at 80 connections and 64 pending approvals; SQLite and lifecycle transitions share a lock. This is a single-machine alpha, not a public HTTP service.

## Evidence and remaining work

- Python 3.14.7 and Claude Code 2.1.296 are installed on the development Mac. We did not start a Claude session or change its settings.
- The official [Claude hooks reference](https://code.claude.com/docs/en/hooks) documents `hookSpecificOutput.decision` for PermissionRequest and non-blocking HTTP connection errors. Responses and generated hooks follow that contract. Tests use synthetic payloads, not recordings.
- The official [Telegram Bot API](https://core.telegram.org/bots/api) supplies polling, callback buttons, and message edits. Tests replace the transport; a real bot/phone smoke test is pending.
- The Tether prototype was not present in the selected empty repository. This is a new implementation; its tests are not the claimed 24 prototype tests.
- Pending: real hook timeout ceiling and terminal behavior while waiting; hook payload recordings on each platform; AskUserQuestion behavior; MCP timeout tests; live Telegram delivery; restart persistence; Windows credential ACLs; service installers; security review. No Phase 0 or MVP exit gate is claimed complete.
- Cloud/name reservations, purchases, accounts, publishing, and operating-system service installation have not been performed.

The next useful slice is verified live PermissionRequest/SessionEnd behavior with a user-supplied Telegram bot, followed by question relay and transcript context. Preserve conservative approval rules while adding richer cards.
