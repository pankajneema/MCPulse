# MCPulse

**Claude Code needs you → your phone buzzes → you answer → Claude keeps working.**

MCPulse is a small tool that runs next to Claude Code on your computer. When Claude stops to ask for something (a permission, a question, "what next?"), MCPulse sends it to your phone on Telegram, Slack, WhatsApp, email or push. It shows full context and buttons. Your answer goes straight back into the running session.

> Status: design + development plan, v1.0 (Oct 2026). A working prototype exists (codename "Tether", 24 passing end-to-end tests). This document is the source of truth for building the real product.

---

## Table of contents

1. [The problem](#1-the-problem)
2. [The idea in one picture](#2-the-idea-in-one-picture)
3. [Goals and non-goals](#3-goals-and-non-goals)
4. [Who it is for](#4-who-it-is-for)
5. [What the user experiences](#5-what-the-user-experiences)
6. [Features](#6-features)
7. [Architecture](#7-architecture)
8. [How it connects to Claude Code](#8-how-it-connects-to-claude-code)
9. [CLI and login](#9-cli-and-login)
10. [Channels](#10-channels)
11. [Policy engine](#11-policy-engine)
12. [Security model](#12-security-model)
13. [Data and privacy](#13-data-and-privacy)
14. [Problems users hit and how MCPulse handles them](#14-problems-users-hit-and-how-mcpulse-handles-them)
15. [Configuration reference](#15-configuration-reference)
16. [Tech stack](#16-tech-stack)
17. [Repository layout](#17-repository-layout)
18. [Phases and roadmap](#18-phases-and-roadmap)
19. [Development plan (week by week)](#19-development-plan-week-by-week)
20. [Testing strategy](#20-testing-strategy)
21. [Release, packaging and distribution](#21-release-packaging-and-distribution)
22. [Operations (cloud relay)](#22-operations-cloud-relay)
23. [Pricing and business model](#23-pricing-and-business-model)
24. [Success metrics](#24-success-metrics)
25. [Risks and mitigations](#25-risks-and-mitigations)
26. [Open questions to verify](#26-open-questions-to-verify)
27. [Glossary](#27-glossary)
28. [References](#28-references)

---

## 1. The problem

You give Claude Code a task and walk away. Ten minutes later it asks *"Allow `npm install supertest`?"* — and waits. You come back after 4 hours, and it has done nothing for 3 hours 50 minutes.

The same thing happens when:

- Claude asks a multiple-choice question (`AskUserQuestion`).
- Claude finishes, and you would have given it the next step right away.
- The session hits a rate limit or an API error and stops.
- A sleeping laptop silently stops a "running" session.
- You run 3–5 sessions and can't tell which one is blocked.

What exists today is not enough:

| Option | What it does | Gap |
|---|---|---|
| Claude Code hooks | Code can allow/deny permissions, react to stop/notification events | Building blocks only: no phone, no routing, no policy |
| Claude Code Channels (research preview) | Official Telegram/Discord/iMessage bridge with permission relay | Preview flag; no Slack/WhatsApp/email; no escalation, policy, audit or multi-session view; Team/Enterprise must enable it |
| Desktop sound/notification hooks | Beep when Claude waits | Only works when you're at the computer |
| OSS tools (e.g. Claude-Code-Remote) | "Done" notifications, new commands via tmux | Not built around permission decisions; keystroke injection |
| `--dangerously-skip-permissions` | Never asks | Unsafe — the reason permissions exist |

**The gap:** one secure tool that decides what it can by policy, routes the rest to whichever channel you answer first, shows enough context to decide from a phone, and fails safe.

---

## 2. The idea in one picture

```
 ┌──────────────── your computer ────────────────┐            ┌──────── your phone ────────┐
 │                                                │            │                            │
 │  Claude Code ──hooks──▶ mcpulsed (daemon) ─────┼──────────▶ │  Telegram / Slack /        │
 │      ▲                    │  policy             │  card with │  WhatsApp / push / email   │
 │      │                    │  presence           │  buttons   │                            │
 │      │                    │  broker             │            │  [Allow] [Always] [Deny]   │
 │      │                    │  SQLite audit       │ ◀──────────┼─ [Other…] [More] [Stop]   │
 │      └──── decision ◀─────┘                     │   answer   │                            │
 │                                                │            └────────────────────────────┘
 │  mcpulse-mcp (per session): ask_human,         │
 │  request_approval, notify_human                │      optional: MCPulse Cloud relay
 └────────────────────────────────────────────────┘      (Cloudflare) for webhooks, PWA,
                                                         multi-machine, no tunnel setup
```

1. Claude Code asks for permission → a **hook** sends the request to the local daemon.
2. The **policy engine** auto-allows safe things, hard-blocks dangerous ones, and marks risk.
3. If a human is needed and you're **away**, the **broker** sends a card to your channels, escalating over time.
4. The first valid answer from any channel wins. The decision goes back down the still-open hook. Claude continues.
5. If you're at the keyboard, MCPulse stays out of the way and the normal terminal prompt appears.

---

## 3. Goals and non-goals

### Goals

| # | Goal | Measurable target |
|---|---|---|
| G1 | No session stays blocked because you stepped away | Median "request → answer" under 3 min for away users |
| G2 | Decide from a phone without opening a laptop | ≥ 90% of requests answered from the card alone (no "More context" needed) |
| G3 | Rich answers, not only yes/no | Allow, always-allow, deny with reason, edit command, answer questions, give next instruction, stop |
| G4 | Invisible when you're at the desk | Zero phone pings while you're actively typing |
| G5 | Safe by default | Dangerous commands blocked or double-confirmed; secrets masked; every decision audited |
| G6 | Fail open to normal behaviour | If MCPulse is down, Claude Code works exactly as without it |
| G7 | 5-minute setup for a normal user | `npx`/`brew` install → `mcpulse setup` → test card on phone in ≤ 5 min |

### Non-goals (v1)

- Running Claude itself in the cloud (MCPulse controls *your* sessions, wherever they run).
- A native mobile app (chat apps + a PWA cover it).
- Replacing Claude Code's own permission system — MCPulse sits on top of it.
- Supporting every agent on day one (Codex / Gemini CLI adapters come in Phase 4).

---

## 4. Who it is for

| Persona | Situation | What they need most |
|---|---|---|
| **Solo developer** (primary) | Laptop, 1–3 sessions, leaves for meetings, lunch, commute | Telegram cards, presence, keep-awake |
| **Power user** | 3–10 sessions, worktrees, a home server or VM | `/status`, multi-machine, policy rules, escalation |
| **Small team lead** | Shared repos, Slack-first company | Slack, audit log, per-project rules, approver roles |
| **Non-technical "vibe coder"** | Uses Claude Code with guidance, scared of the terminal | Guided `mcpulse setup`, plain-language cards, safe defaults, no tunnels |

---

## 5. What the user experiences

### 5.1 First run (target: under 5 minutes)

```
$ npm i -g @mcpulse/cli          # or: brew install mcpulse / curl -fsSL https://mcpulse.dev/install | sh
$ mcpulse setup
  ? How do you want to use MCPulse?
    › Local only (nothing leaves your machine except to your chat app)
      MCPulse Cloud (sign in — no tunnel needed, works with WhatsApp/email/web)
  ? Pick your first channel:  Telegram (recommended)
  → Open @BotFather, create a bot, paste the token: ********
  → Send this to your bot: /pair 7F3A9C
  ✔ Paired with @pankaj
  ✔ Daemon installed as a background service (starts at login)
  ✔ Claude Code hooks installed (~/.claude/settings.json, backup saved)
  ✔ MCP server registered (ask_human, request_approval, notify_human)
  → Sending a test card…  ✔ You tapped "Yes 👍" on Telegram
  Done. Use claude as usual — walk away and MCPulse will reach you.
```

### 5.2 A permission card

```
🔐 janus · kpqrs
📁 ~/code/janus  ⎇ feat/login
🧵 running 42 min
🎯 You asked: Fix the login redirect bug and add a test
🔧 So far: Read auth/middleware.ts → Edited auth/middleware.ts → Ran: npm test
💬 Claude: Fixed. Now I need to install supertest to write the test.

Run: Install supertest
$ npm i -D supertest

[✅ Allow] [♾️ Always "npm i"] [❌ Deny]
[🛑 Stop Claude] [📜 More context]
```

Every card answers the questions you have on a phone: **which project and folder, which branch, what did I ask, what has Claude done, why is it asking, and what exactly am I approving.**

### 5.3 A question card

```
❓ janus · mtwxe
📁 ~/code/janus  ⎇ feat/login
🎯 You asked: Fix the login redirect bug and add a test

Test lib: Which test library should I use?
1. supertest
   HTTP assertions against the Express app
2. msw
   Mock the network layer

[1. supertest] [2. msw] [✏️ Other…] [📜 More] [🛑 Stop]
```

- Tap an option, or **Other…** to type your own. Claude is told: *"this is the user's own answer, not one of your options."*
- Multi-select questions: reply `mtwxe 1,3`.
- Several questions in one call arrive as "Question 1 of 3", "Question 2 of 3"…

### 5.4 A "finished" card

```
✅ janus finished (18 min)
📁 ~/code/janus  ⎇ feat/login
🎯 You asked: Fix the login redirect bug and add a test

Claude: Fixed the redirect loop in auth/middleware.ts and added
3 tests in auth.test.ts. All 48 tests pass.

Reply with what to do next, or tap Done.     [👍 Done] [📜 More]
```

Reply *"now open a PR"* and Claude continues with that instruction.

### 5.5 Every way to reply

| Reply | Meaning |
|---|---|
| Tap a button | That option. High-risk **Allow** asks for a second "Yes, really" tap |
| `y kpqrs` / `n kpqrs` / `always kpqrs` | Allow / deny / allow and remember |
| `y kpqrs 4821` | Allow a high-risk request with your PIN |
| `kpqrs use the staging DB instead` | Deny and tell Claude why (Claude reads it and adapts) |
| `kpqrs edit npm test -- --ci` | Allow, but run this command instead |
| `kpqrs 2` / `kpqrs 1,3` | Pick option 2 / several options |
| `more kpqrs` | Full context: whole task, all steps, Claude's full last message |
| `stop kpqrs` | Stop that Claude session |
| Reply to the message | Answers that request (no id needed) |
| Plain text when only one request is open | Answers it |

### 5.6 Chat commands

| Command | What it does |
|---|---|
| `/status` | All sessions (project, branch, state, idle time) and everything waiting on you |
| `/pending` | Re-send open requests |
| `/away` `/present` `/auto` | Who answers prompts: phone, terminal, or decide by keyboard idle time |
| `/mute 1h` `/unmute` | Pause "done/waiting" alerts; approvals still come |
| `/audit` | Last 10 decisions: what, who, channel, how long you took |
| `/to <project> <message>` | Send a message into a live session (channel mode) |
| `/rules` | Show active policy rules for a project |
| `/help` | Everything above |

---

## 6. Features

Legend: **P1** = Phase 1 (MVP) … **P5** = later.

### 6.1 Core loop

| Feature | Phase | Description |
|---|---|---|
| Permission relay | P1 | `PermissionRequest` hook → card → allow / always / deny / deny-with-reason / edit / stop |
| Question relay | P1 | `AskUserQuestion` → options as buttons + Other + multi-select + numbers |
| Keep going after Stop | P1 | "Finished" card; your reply becomes the next instruction |
| First answer wins | P1 | All other channels' cards update to "→ Allowed · via Telegram (2 min)" |
| Fail open | P1 | Daemon down / error / timeout → normal terminal behaviour (or safe deny on timeout) |
| Claude-initiated asks | P1 | MCP tools `ask_human`, `request_approval`, `notify_human` |

### 6.2 Context (why the card is decidable from a phone)

| Feature | Phase | Description |
|---|---|---|
| Project · folder · branch | P1 | From hook `cwd`, transcript `gitBranch`, or `git rev-parse` |
| Your task | P1 | Latest real prompt from the transcript |
| Steps so far | P1 | Last 3 tool calls since that prompt (6 in "More"), loops shown as "Ran: npm test ×4" |
| Claude's last words | P1 | Its own reason for asking |
| Subagent marker | P1 | "asked by subagent *test-writer*" |
| Diff preview for edits | P2 | Unified diff, syntax-trimmed, for `Edit`/`Write` |
| Risk explainer | P3 | Optional one-line LLM explanation: "This deletes the `dist/` folder" |
| Screenshot of a web page | P4 | For browser-tool requests |

### 6.3 Smart routing

| Feature | Phase | Description |
|---|---|---|
| Presence (auto/away/present) | P1 | OS keyboard idle time; away after 120 s; unknown → away |
| Hand-back | P1 | You come back while a card is open → prompt returns to the terminal within 10 s |
| Escalation ladder | P1 | e.g. Telegram 0 s → WhatsApp 5 min → email 15 min; per-rung `minRisk` |
| Quiet hours | P1 | 23:30–07:30 only silent channels; optional high-risk exception |
| Mute | P1 | `/mute 2h` for alerts, never for approvals |
| Phone-busy detection | P4 | Calendar "in a meeting" → delay non-urgent alerts |

### 6.4 Safety

| Feature | Phase | Description |
|---|---|---|
| Policy rules | P1 | Ordered regex rules → allow / deny / ask + risk |
| High-risk double confirm + PIN | P1 | Confirm tap in chat, PIN on text/web replies |
| Safe "always allow" | P1 | Remembers `npm run build` (not all of `npm run`); never for chained commands or high risk |
| Secret masking | P1 | API keys, tokens, `password=`, private keys → `[REDACTED]` before sending |
| Anti-spoofing | P1 | Hidden Unicode shown as ⍰; long commands keep their end visible |
| Audit log | P1 | Every decision with who, channel, wait time; `mcpulse audit export` |
| Per-project rules | P2 | `.mcpulse/rules.json` in the repo (reviewed in PRs) |
| Approver roles | P4 | Team: who can approve high-risk in which repo |

### 6.5 Reliability

| Feature | Phase | Description |
|---|---|---|
| Keep-awake | P1 | `caffeinate` / `systemd-inhibit` while a session works or waits |
| Send retries | P1 | Backoff retries on network errors while the request is open |
| Background service | P1 | launchd / systemd user service / Windows Task Scheduler |
| `mcpulse doctor` | P1 | Finds unpaired bot, stale hook key, daemon down, `disableAllHooks`, no idle detection |
| Rate-limit / quota alerts | P1 | `StopFailure` and quota notifications → "limit resets at 14:30" |
| Daily digest | P2 | Morning summary: approvals, avg wait, stalled sessions |
| Auto-update | P2 | Signed releases, `mcpulse update` |

### 6.6 Multi-everything

| Feature | Phase | Description |
|---|---|---|
| Multi-session `/status` | P1 | All sessions on this machine |
| Channel mode (official relay) | P2 | Parallel prompt in terminal + phone via Claude Code Channels |
| Multi-machine | P3 | Laptop + server + VM under one account (Cloud) |
| Web dashboard / PWA | P3 | Live list of sessions and requests, push notifications, installable on phone |
| Other agents | P4 | Codex CLI, Gemini CLI, OpenCode adapters behind the same broker |
| Voice replies | P4 | Voice note → transcribed instruction |

---

## 7. Architecture

### 7.1 Components

| Component | Runs where | Responsibility |
|---|---|---|
| **`mcpulse` CLI** | Your machine | `setup`, `login`, `start`, `status`, `doctor`, `audit`, `rules`, `update` |
| **`mcpulsed` daemon** | Your machine (background service) | Hook endpoints, policy, presence, broker, channel adapters, SQLite, local API |
| **`mcpulse-mcp`** | Spawned by Claude Code per session (stdio) | `ask_human`, `request_approval`, `notify_human`; optional channel relay |
| **Channel adapters** | Inside the daemon | Telegram, Slack, WhatsApp, Email, ntfy, Web Push |
| **MCPulse Cloud relay** (optional) | Cloudflare Workers + Durable Objects | Auth, device registry, webhook ingress, PWA, multi-machine routing |
| **PWA dashboard** (optional) | Browser / phone home screen | See and answer requests; Web Push |

### 7.2 Inside the daemon

```
              ┌──────────────────────── mcpulsed ────────────────────────┐
 hooks ──────▶│ Hook API (127.0.0.1, key-checked)                        │
 mcp ────────▶│ Local API (CLI + MCP)                                    │
              │        │                                                 │
              │        ▼                                                 │
              │  Policy engine ──deny/allow──▶ immediate decision        │
              │        │ ask                                             │
              │        ▼                                                 │
              │  Presence ──present──▶ "no decision" (terminal dialog)   │
              │        │ away                                            │
              │        ▼                                                 │
              │  Context builder (transcript tail, git, session table)   │
              │        ▼                                                 │
              │  Broker: open request ─▶ escalation ladder ─▶ adapters   │
              │        ▲          first valid answer          │          │
              │        └──────────────────────────────────────┘          │
              │  SQLite: sessions · requests · audit · remembered rules   │
              └──────────────────────────────────────────────────────────┘
```

### 7.3 Request lifecycle

```
OPEN ──send to rung 0──▶ SENT ──(no answer, rung delay)──▶ ESCALATED ──▶ …
  │                         │
  │                         ├─ valid answer ───────────▶ ANSWERED ──▶ hook returns decision
  │                         ├─ you came back ──────────▶ HANDED_BACK ─▶ hook returns nothing
  │                         ├─ session ended ──────────▶ CANCELLED
  │                         └─ timeout (4 h default) ──▶ TIMED_OUT ──▶ deny-with-message
  └─ policy allow/deny ───────────────────────────────▶ AUTO (audited, no card)
```

Every terminal state edits all sent cards ("→ Denied · via Slack · 3 min"), writes an audit row, and resolves the waiting hook exactly once.

### 7.4 Local mode vs Cloud mode

| | Local mode (free, default) | Cloud mode (account) |
|---|---|---|
| Login | None | `mcpulse login` (device flow) |
| Telegram / Slack | Direct from daemon (long poll / Socket Mode) | Same — direct from daemon |
| WhatsApp / email links / ntfy buttons | Need your own tunnel (Cloudflare Tunnel / Tailscale) | Work out of the box via relay |
| PWA + Web Push | — | Yes |
| Multi-machine | — | Yes |
| What leaves your machine | Only the card text, to the chat app you chose | Card text to chat apps; to the relay only what's needed to route (see §13) |

The daemon always talks **outbound** to the relay (WebSocket). Nothing on your machine is ever exposed to the internet.

---

## 8. How it connects to Claude Code

### 8.1 Three integration paths

1. **Hooks mode (default, works today).** `mcpulse setup` adds HTTP hooks to `~/.claude/settings.json`. Each hook carries a per-install key header. MCPulse holds the hook open while it waits for you. Hook timeouts are set 60 s above MCPulse's own wait, so MCPulse always answers first.
2. **Channel mode (opt-in, research preview).** `mcpulse-mcp` declares `claude/channel` + `claude/channel/permission`. The prompt shows in the terminal **and** on your phone in parallel; first answer wins. It also enables `/to <project> <msg>`. Use either this or the PermissionRequest hook for a session, not both.
3. **MCP tools (always on).** `ask_human`, `request_approval`, `notify_human` let Claude reach you on its own — for decisions it would otherwise stop on, and before irreversible actions.

### 8.2 Event mapping

| Claude Code event | MCPulse does (when you're away) | What goes back |
|---|---|---|
| `PermissionRequest` | Policy → card with context, preview, risk | `allow`, `allow` + remember, `deny` + reason, `allow` + edited input, or stop |
| `PreToolUse` (`AskUserQuestion`) | Question card(s) | Your answers, returned as the deny reason so Claude continues |
| `Stop` | Finished card, waits 30 min | Your reply → `decision: block` + `reason` = next instruction |
| `Notification` | Alerts: waiting for input, form/link needed at terminal, quota events | — |
| `PermissionDenied` | "Auto mode blocked X" alert | — |
| `StopFailure` | "Stopped: rate_limit / auth / overloaded" | — |
| `SessionStart` / `SessionEnd` / `UserPromptSubmit` | Session table, turn timing | — |

### 8.3 Hook settings written by `mcpulse setup`

```json
{
  "hooks": {
    "PermissionRequest": [{ "matcher": "*", "hooks": [{
      "type": "http",
      "url": "http://127.0.0.1:7717/hook/PermissionRequest",
      "timeout": 14460,
      "headers": { "x-mcpulse-key": "<per-install key>" },
      "statusMessage": "Waiting for your approval (MCPulse → phone)…"
    }]}],
    "PreToolUse": [{ "matcher": "AskUserQuestion", "hooks": [{ "type": "http", "url": "http://127.0.0.1:7717/hook/PreToolUse", "timeout": 14460, "headers": { "x-mcpulse-key": "…" } }]}],
    "Stop":       [{ "hooks": [{ "type": "http", "url": "http://127.0.0.1:7717/hook/Stop", "timeout": 1860, "headers": { "x-mcpulse-key": "…" } }]}],
    "Notification": [{ "hooks": [{ "type": "http", "url": "…/hook/Notification", "timeout": 30, "headers": { "x-mcpulse-key": "…" } }]}],
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "mcpulse", "args": ["hook", "SessionStart"], "timeout": 10 }]}]
  }
}
```

Rules for the installer:

- Always back up the settings file first; `mcpulse uninstall` restores cleanly.
- Never touch hooks it didn't create (identified by URL/command signature).
- Re-running is idempotent. A new key or port automatically replaces the old hooks.
- Offer user-level (`~/.claude/settings.json`) or project-local (`.claude/settings.local.json`).

---

## 9. CLI and login

The CLI follows the same pattern as TokenFin and `gh`: **local-first, optional account, browser-based device login, tokens in the OS keychain.**

### 9.1 Commands

```
mcpulse setup                 guided first run (mode, channel, pairing, service, hooks, MCP, test)
mcpulse login                 sign in to MCPulse Cloud (device flow, opens browser)
mcpulse logout                revoke this device's token and remove it from the keychain
mcpulse whoami                account, plan, this device's name and id

mcpulse start | stop | restart        run/stop the daemon (foreground with --fg)
mcpulse service install|uninstall     background service (launchd / systemd --user / Task Scheduler)
mcpulse status                sessions + pending requests + presence + channels
mcpulse away | present | auto presence mode
mcpulse answer <id> <allow|deny|always|text…>    answer from another terminal
mcpulse test [--channel telegram]     send a test card
mcpulse doctor                diagnose setup problems, print exact fixes

mcpulse channel add <telegram|slack|whatsapp|email|ntfy>
mcpulse channel remove <name>
mcpulse channel pair <name>   print a fresh one-time pairing code
mcpulse channel list

mcpulse hooks install [--project] | uninstall [--project] | show
mcpulse mcp install | uninstall       register mcpulse-mcp with Claude Code

mcpulse rules list | test "<command>" | edit
mcpulse audit [--since 7d] [--json] | audit export --csv
mcpulse devices list | revoke <id>    (Cloud) machines on your account
mcpulse pin set | clear       PIN for high-risk approvals
mcpulse config get|set <key> <value>
mcpulse update                signed self-update
mcpulse version
```

Conventions: `--json` on every read command, exit code 0/1/2 (ok/error/needs action), `NO_COLOR` respected, never prints secrets.

### 9.2 Login flow (OAuth 2.0 Device Authorization Grant, RFC 8628, with PKCE)

```
$ mcpulse login
  ! First copy your one-time code: WDJB-MJHT
  Press Enter to open https://app.mcpulse.dev/device in your browser…
  ✔ Logged in as pankaj@… (plan: Free)
  ✔ This device registered as "pankaj-mbp" (id dev_8f2…)
  ✔ Token stored in macOS Keychain
```

Steps:

1. CLI → `POST /oauth/device/code` with `client_id=mcpulse-cli`, `scope=device:link relay:connect`, PKCE `code_challenge` (S256).
2. Server returns `device_code`, `user_code` (8 chars, no ambiguous letters), `verification_uri`, `expires_in=600`, `interval=5`.
3. User opens the URL, signs in (Google / GitHub / email magic link), **sees the device name, OS and IP**, and approves.
4. CLI polls `POST /oauth/token` every `interval` seconds; handles `authorization_pending`, `slow_down`, `expired_token`, `access_denied`.
5. Server returns:
   - **access token**: short-lived (15 min), audience-bound to the relay.
   - **refresh token**: 30 days, **rotating** (each use issues a new one; reuse of an old one revokes the whole family).
6. CLI generates a **device keypair (Ed25519)**, registers the public key, and stores the private key + refresh token in the **OS keychain** (macOS Keychain, Windows Credential Manager, libsecret on Linux). Fallback for headless Linux: `~/.config/mcpulse/credentials` with mode `0600`, encrypted with a key derived from a machine secret, with a printed warning.
7. Daemon connects to the relay over WebSocket, authenticating with the access token **and** a signature from the device key (proof of possession). A stolen access token alone is useless.

Headless servers: `mcpulse login --no-browser` prints the URL and code to open on any other device.

CI / automation: `mcpulse login --with-token < token.txt` using a scoped, expiring **personal access token** created in the dashboard (scopes: `relay:connect`, read-only `audit:read`).

### 9.3 Local-mode auth (no account)

- Daemon binds **127.0.0.1 only**.
- Hooks, CLI and MCP send `x-mcpulse-key` = HMAC-SHA256(install secret, "hook"). Constant-time compare.
- Install secret: 32 random bytes, in the keychain (or `0600` config file).
- Chat senders are allowlisted by **user id**, bootstrapped with a single-use pairing code that rotates after use.

---

## 10. Channels

### 10.1 Comparison

| Channel | Inbound path | Needs a public URL? | Buttons | Edit message after answer | Best for |
|---|---|---|---|---|---|
| **Telegram** | Long polling | No | Inline keyboard | Yes | Default for individuals |
| **Slack** | Socket Mode (WebSocket) | No | Block Kit | Yes | Teams, work hours |
| **WhatsApp** (Cloud API) | Meta webhook | Yes (or Cloud relay) | Reply buttons (≤3) / list (≤10) | No (sends a follow-up) | India / people who live in WhatsApp |
| **Email** (Resend / SES / SMTP) | Signed links to confirm page | Yes (or Cloud relay) | Links | No | Last-resort escalation |
| **ntfy** | Signed action URLs | Phone must reach it (or Cloud relay) | Up to 3 actions | No | Free, self-hostable push |
| **Web Push / PWA** | Cloud relay | No (Cloud) | In-page | Yes | Cloud users, no chat app |
| Discord | Gateway WebSocket | No | Components | Yes | P3 |
| Microsoft Teams | Bot Framework | Yes (Cloud) | Adaptive Cards | Yes | P4 |

### 10.2 Channel setup notes

- **Telegram:** create a bot with @BotFather → token → `mcpulse channel add telegram` → DM the bot `/pair CODE`. Use a private chat. Disable group joins (`/setjoingroups` → Disable).
- **Slack:** create an app from the provided manifest (`mcpulse channel add slack --manifest` prints it): Socket Mode on, `chat:write`, `im:history`, `channels:history`, Interactivity on. DM the app `pair CODE`.
- **WhatsApp:** Meta Business app + phone number; webhook = `https://relay.mcpulse.dev/wa/<device>` (Cloud) or your tunnel URL. **24-hour rule:** free-form messages are only allowed within 24 h of your last message to the bot. MCPulse sends an approved **utility template** as the opener when the window is closed, then the full card after you reply.
- **Email:** verified sender domain (SPF, DKIM, DMARC). Links open a confirm page; GET never approves.
- **ntfy:** long random topic (≥ 24 chars) or self-hosted server with an access token.

### 10.3 Adapter contract (for contributors)

```ts
interface Adapter {
  name: string;
  start(broker: Broker): Promise<void>;            // connect, register commands
  send(req: Pending): Promise<MessageRef>;          // post a card, return a ref
  update?(ref: MessageRef, req: Pending, outcome: string): Promise<void>; // edit after answer
  notify(text: string, opts?: { project?: string }): Promise<void>;       // one-way alert
  health(): Promise<{ ok: boolean; detail?: string }>;                    // for doctor
}
```

Rules every adapter follows: gate on **sender id**, never chat id. Answers go through the shared reply parser. Never log message bodies at info level. Retry sends with backoff.

---

## 11. Policy engine

Rules are evaluated top to bottom; first match wins. `tool` and `match` are regexes; `match` runs against the command (Bash), file path (file tools), URL (web tools) or the JSON input.

```json
[
  { "tool": "^Bash$", "match": "rm\\s+-rf\\s+(/|~|\\$HOME)(\\s|$)", "action": "deny", "risk": "high", "note": "wipe root/home" },
  { "tool": "^Bash$", "match": ":\\(\\)\\s*\\{", "action": "deny", "risk": "high", "note": "fork bomb" },
  { "tool": "^Bash$", "match": "(curl|wget)[^|]*\\|\\s*(ba|z)?sh", "action": "ask", "risk": "high", "note": "pipe to shell" },
  { "tool": "^Bash$", "match": "\\b(git\\s+push|--force|reset\\s+--hard|sudo|terraform\\s+(apply|destroy)|kubectl\\s+(delete|apply)|DROP\\s+(TABLE|DATABASE)|npm\\s+publish|deploy)\\b", "action": "ask", "risk": "high" },
  { "tool": "^(Read|Glob|Grep|LS|WebSearch)$", "action": "allow", "risk": "low" },
  { "tool": "^Bash$", "match": "^\\s*(ls|pwd|cat|head|tail|wc|git\\s+(status|diff|log|show|branch))\\b[^;&|>]*$", "action": "allow", "risk": "low" },
  { "action": "ask", "risk": "medium" }
]
```

| Layer | Location | Who edits |
|---|---|---|
| Built-in defaults | Shipped with MCPulse | Maintainers |
| User rules | `~/.config/mcpulse/rules.json` | You |
| Project rules | `<repo>/.mcpulse/rules.json` | Your team, via PR review |
| Remembered ("Always") | SQLite, per project + signature | Created by your taps; `mcpulse rules forget` |

Precedence: project **deny** > user **deny** > built-in **deny** > remembered > project allow/ask > user > built-in. A project file can never turn a built-in deny into an allow.

**"Always allow" signatures:**

- `npm test --watch` → `npm test`
- `npm run build --prod` → `npm run build` (never all of `npm run`)
- `npx prisma migrate dev` → `npx prisma migrate`
- File edits → the directory
- Never offered for: high risk, chained / piped / redirected / substituted commands (`&&`, `||`, `;`, `|`, `` ` ``, `$(`, `>`, `<`, newline)

`mcpulse rules test "git push --force origin main"` prints which rule matches and why.

---

## 12. Security model

**Core principle:** anyone who can answer through MCPulse can run commands on your machine. So every inbound path is authenticated, high-risk actions need a second factor, and nothing dangerous can be approved by a single tap from an unverified source.

### 12.1 Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | Stranger messages your bot and approves | Sender allowlist by platform user id; single-use pairing code; unknown senders ignored silently |
| T2 | Someone in a group chat approves | Gate on sender id, never chat id; Telegram group joins disabled by default |
| T3 | Local malware calls the daemon | 127.0.0.1 bind; HMAC key header; key in keychain; constant-time compare; rate limiting |
| T4 | Email scanner / link preview clicks "Allow" | Signed, expiring (≤ request TTL), single-use tokens bound to one request + option; GET shows a confirm page, POST approves |
| T5 | Phone stolen / chat account hijacked | High-risk needs confirm tap **and** PIN on text/web; never "always"; hard-deny rules apply to everyone; `mcpulse devices revoke` |
| T6 | Secrets leak into chat history | Redaction before send (API keys, tokens, `password=`, private keys, bearer headers); 1,200-char preview cap; optional "ultra-private" mode with no command text, PWA only |
| T7 | Disguised command (bidi / zero-width chars, hidden tail) | Hidden chars shown as ⍰; long text keeps head **and** tail with "N characters hidden" in the middle |
| T8 | Forged webhooks | WhatsApp `X-Hub-Signature-256`, Slack signing secret, Telegram secret token header (webhook mode) |
| T9 | Prompt injection via your chat replies | Only allowlisted senders reach Claude; text framed as "instruction from the user"; length limits |
| T10 | Stolen Cloud access token | 15-min lifetime + device-key proof of possession; rotating refresh with reuse detection |
| T11 | Relay compromise | Relay can't approve anything alone: the daemon verifies answers carry a valid user signature (PWA) or came from an allowlisted channel identity; see §13 for E2E |
| T12 | Replay of an old answer | Request ids are single-use; answers for closed ids are dropped |
| T13 | MCPulse itself fails | Fail open to the normal terminal dialog; timeout = deny with a message telling Claude to continue other work |
| T14 | Supply-chain attack on the CLI | Signed releases (Sigstore/cosign), npm provenance, lockfile pinned, minimal deps, Dependabot + `npm audit` in CI, reproducible builds |
| T15 | Over-broad "Always" | Narrow signatures, never for compound/high-risk, list + forget commands, shown in `/rules` |

### 12.2 Security checklist (must pass before each release)

- [ ] Daemon binds only to 127.0.0.1; port scan from another host finds nothing.
- [ ] Every `/hook/*` and `/api/*` route rejects missing/wrong key (401) — tested.
- [ ] Signed link: tampered, expired, reused and wrong-option tokens all rejected — tested.
- [ ] No secret appears in logs at any level (grep test on log output with planted secrets).
- [ ] Config and credential files are `0600`; directory `0700`.
- [ ] Redaction test corpus (≥ 50 secret formats) passes.
- [ ] Unicode spoofing corpus passes.
- [ ] High-risk allow without confirm/PIN is impossible on every channel — tested.
- [ ] Dependency audit clean; release artifacts signed; SBOM published.
- [ ] Threat model reviewed for any new channel or endpoint.

### 12.3 Responsible disclosure

`SECURITY.md` with a private reporting address, 90-day disclosure window, and a hall of fame.

---

## 13. Data and privacy

| Data | Local mode | Cloud mode |
|---|---|---|
| Card text (command, context) | Daemon → your chat app only | Same for Telegram/Slack (sent directly by the daemon). For PWA/Web Push: **end-to-end encrypted** with your device key; relay stores ciphertext only |
| WhatsApp / email | Your tunnel | Relay forwards; stores only request id + delivery status, **never** card text |
| Audit log | SQLite on your machine | Stays local; optional encrypted backup |
| Session transcripts | Read locally (tail only), never uploaded | Same |
| Account data | — | Email, device names, public keys, plan |
| Retention | You control | Relay delivery records: 7 days; account until deletion |

User rights: `mcpulse audit export`, account export and deletion from the dashboard (DPDP Act 2023 / GDPR friendly). No analytics without opt-in; opt-in telemetry is counts only (requests/day, answer latency), never content.

---

## 14. Problems users hit and how MCPulse handles them

Collected from Claude Code GitHub issues, community guides and prototype testing.

### 14.1 Normal users

| Problem | What happens today | MCPulse fix |
|---|---|---|
| Walked away, session waited hours | Prompt sits in terminal | Card on phone, escalation, safe timeout |
| Laptop went to sleep | Session silently stops | Keep-awake while working/waiting; card warns "lid closed on battery still sleeps" |
| Can't tell which session is asking | 5 terminal tabs | Project · folder · branch · task on every card; `/status` |
| Afraid to approve from a tiny screen | Approve blindly or not at all | Context lines, preview, risk tag, "More context", PIN for high risk |
| Too many notifications | Notification fatigue | Policy auto-allows safe actions; presence; mute; quiet hours |
| Claude's question has no good option | Only fixed choices | **Other…** with free text, flagged as your own answer |
| Hit the usage limit overnight | Stops, nobody knows | Quota alerts with reset time; "nudge to resume" |
| Setup is confusing | Gives up | `mcpulse setup` wizard + `doctor` with exact fix commands |
| Missed a WhatsApp card | 24-hour window closed | Template opener + escalation to another channel |
| Approved on two devices at once | Confusion | First answer wins; others show "→ already answered" |

### 14.2 Developers

| Problem | What happens today | MCPulse fix |
|---|---|---|
| Notification hook doesn't fire for permission prompts (VS Code, some Windows setups) | No alert | Uses `PermissionRequest` hook (decision path), not `Notification` |
| `idle_prompt` only fires after ~60 s idle | Late alerts | Relies on `PermissionRequest` / `PreToolUse` / `Stop`, which fire immediately |
| No hook fires for `AskUserQuestion` | Question unseen | `PreToolUse` matcher on `AskUserQuestion` |
| Hook mis-typed path silently disables it | Prompts stay local | `doctor` checks hooks, keys, ports, `disableAllHooks` |
| Re-created config → stale key in hooks | 401s, silent fallback | `doctor` detects; `setup` reinstalls idempotently |
| Chained command hides danger behind a safe prefix | `npm test && curl … \| sh` | Compound commands never "always"-allowed; full command shown, tail kept |
| Hidden Unicode in commands | Looks harmless | ⍰ markers |
| Secrets in commands/edits | Leak to chat | Redaction before send |
| Subagent asks permission | Unclear who asks | "asked by subagent X" |
| Auto mode classifier denies silently | Session stuck | `PermissionDenied` alert |
| Network blips (Wi-Fi switch, VPN) | Lost card | Send retries with backoff |
| Daemon crash | Stuck? | Hooks fail open; service restarts daemon; open requests re-sent on restart (P2: persisted) |
| Hook timeout ceilings | Request dies mid-wait | Configurable wait; hook timeout always 60 s above it; verify ceiling (see §26) |
| MCP tool call timeouts for long `ask_human` waits | Tool errors | Document `MCP_TOOL_TIMEOUT`; tool returns "no answer, continue with safe default" |
| Running in SSH / headless server | No idle detection | `away` mode by default on headless; doctor explains |
| Team/Enterprise blocks Channels | Channel mode unavailable | Hooks mode works without Channels |
| Worktrees of the same repo | Same project name | Card shows full path and branch |

---

## 15. Configuration reference

`~/.config/mcpulse/config.json` (macOS: `~/Library/Application Support/mcpulse/`; Windows: `%APPDATA%\mcpulse\`). Secrets live in the keychain; the file holds references.

```json
{
  "port": 7717,
  "mode": "local",
  "presence": { "mode": "auto", "idleAwaySec": 120 },
  "timeouts": {
    "permissionSec": 14400,
    "questionSec": 14400,
    "stopWaitSec": 1800,
    "onTimeout": "deny",
    "notifyLongTurnSec": 180
  },
  "escalation": [
    { "channel": "telegram", "afterSec": 0 },
    { "channel": "push", "afterSec": 0 },
    { "channel": "whatsapp", "afterSec": 300 },
    { "channel": "email", "afterSec": 900 },
    { "channel": "slack", "afterSec": 0, "minRisk": "high" }
  ],
  "quietHours": { "start": "23:30", "end": "07:30", "channels": ["email"], "exceptHighRisk": false },
  "keepAwake": true,
  "redactSecrets": true,
  "previewChars": 1200,
  "context": { "enabled": true, "steps": 3, "includeClaudeText": true },
  "pinRequiredFor": "high",
  "channels": {
    "telegram": { "token": "keychain:mcpulse/telegram", "allowedUserIds": [123456789] },
    "slack": { "botToken": "keychain:mcpulse/slack-bot", "appToken": "keychain:mcpulse/slack-app", "channel": "U0ABC", "allowedUserIds": ["U0ABC"] },
    "whatsapp": { "token": "keychain:mcpulse/wa", "phoneNumberId": "…", "to": "+91…", "template": "mcpulse_waiting" },
    "email": { "provider": "resend", "apiKey": "keychain:mcpulse/resend", "from": "MCPulse <alerts@yourdomain>", "to": "you@…" },
    "ntfy": { "server": "https://ntfy.sh", "topic": "keychain:mcpulse/ntfy-topic" }
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `presence.mode` | `auto` | `auto` (idle time), `away` (always phone), `present` (never phone) |
| `presence.idleAwaySec` | 120 | Idle seconds before you count as away |
| `timeouts.permissionSec` | 14400 (4 h) | Max wait for a permission answer |
| `timeouts.onTimeout` | `deny` | `deny` (tell Claude to continue other work) or `fallthrough` (terminal dialog) |
| `timeouts.stopWaitSec` | 1800 | How long the "finished" card waits for a next instruction (0 = notify only) |
| `escalation[]` | see above | Channel rungs with delays and optional `minRisk` |
| `quietHours` | 23:30–07:30 | Window where only listed channels are used |
| `keepAwake` | true | Prevent idle sleep while working/waiting |
| `pinRequiredFor` | `high` | Risk level that needs PIN on typed/web approvals |

Environment overrides: `MCPULSE_HOME`, `MCPULSE_PORT`, `MCPULSE_LOG_LEVEL`, `MCPULSE_NO_KEYCHAIN=1` (CI only).

---

## 16. Tech stack

| Part | Choice | Why |
|---|---|---|
| CLI + daemon | **TypeScript on Bun**, compiled to single binaries (`bun build --compile`) | Fast startup, built-in SQLite/HTTP/test, one binary per OS — no Node install needed for users |
| MCP server | `@modelcontextprotocol/sdk` | Official SDK; channel capabilities |
| Validation | `zod` | Hook payloads change between Claude Code versions; validate leniently |
| Local DB | SQLite (WAL) via `bun:sqlite` | Zero setup, durable audit |
| Keychain | `@napi-rs/keyring` (or platform CLIs) | macOS / Windows / libsecret |
| Cloud relay | **Cloudflare Workers + Durable Objects + D1 + Queues** | Same stack as TokenFin; one DO per account holds live WebSockets; cheap and global |
| Auth | Own OAuth 2.0 device flow on Workers (or Better Auth / WorkOS) + Google/GitHub/email magic link | Matches TokenFin CLI login pattern |
| Email | Resend (default), SES/SMTP adapters | Simple API, good deliverability |
| PWA | Next.js or SvelteKit static on Cloudflare Pages + Web Push (VAPID) | Installable on phone, no app store |
| Payments | Razorpay (India) + Stripe / Lemon Squeezy (global) | INR + international |
| CI/CD | GitHub Actions; release-please; cosign; npm provenance | Signed, repeatable releases |
| Observability | Daemon: local log file with rotation; Relay: Workers Logs + Sentry | Debuggable without collecting content |

---

## 17. Repository layout

Monorepo (`pnpm` / Bun workspaces):

```
mcpulse/
├─ packages/
│  ├─ core/            policy, redaction, context builder, reply parser, broker, types (pure, fully unit-tested)
│  ├─ daemon/          HTTP server, hook handlers, presence, keep-awake, SQLite store, adapters host
│  ├─ adapters/        telegram/ slack/ whatsapp/ email/ ntfy/ webpush/ discord/
│  ├─ cli/             commands, setup wizard, doctor, keychain, service installers
│  ├─ mcp/             mcpulse-mcp stdio server (tools + channel relay)
│  └─ protocol/        shared zod schemas for daemon ↔ relay ↔ PWA messages
├─ cloud/
│  ├─ relay/           Workers + Durable Objects: device sockets, webhook ingress, routing
│  ├─ auth/            device flow, tokens, sessions
│  └─ web/             PWA dashboard + device approval page
├─ test/
│  ├─ e2e/             real daemon + fake channel + fake Claude Code hook client
│  ├─ fixtures/        transcript samples per Claude Code version, secret corpus, unicode corpus
│  └─ contract/        recorded hook payloads from real Claude Code versions
├─ docs/               user guide, channel guides, rules cookbook, security, FAQ
├─ examples/           rules files, service files, Slack app manifest
├─ SECURITY.md  CONTRIBUTING.md  CHANGELOG.md  LICENSE (Apache-2.0 for local; relay source-available)
└─ .github/workflows/  ci.yml  release.yml  codeql.yml
```

---

## 18. Phases and roadmap

| Phase | Name | Duration | Outcome | Exit criteria |
|---|---|---|---|---|
| **0** | Spike & foundation | Week 1 | Verified assumptions, repo, CI | All §26 questions answered; CI green; prototype ported into monorepo |
| **1** | MVP — Local, Telegram | Weeks 2–4 | A solo dev can install and never miss a prompt | 10 real users use it daily for a week; zero "approved the wrong thing" incidents; setup ≤ 5 min |
| **2** | Polish & more channels | Weeks 5–7 | Slack, ntfy, email, WhatsApp (via tunnel); per-project rules; digest; persisted requests; signed binaries | Public beta on GitHub + npm; 100 installs; docs site live |
| **3** | MCPulse Cloud | Weeks 8–11 | `mcpulse login`, relay, PWA + Web Push, multi-machine, WhatsApp/email without tunnels | 50 Cloud accounts; relay p99 delivery < 2 s; security review passed |
| **4** | Teams & more agents | Weeks 12–16 | Team workspaces, approver roles, shared audit, Slack-first; Codex / Gemini CLI adapters; voice replies | 5 paying teams |
| **5** | Scale | Month 5+ | Discord, MS Teams, risk explainer, marketplace listing, enterprise SSO | Revenue covers infra + one contributor |

---

## 19. Development plan (week by week)

Each task has an owner (default: you), a definition of done (DoD), and lands behind tests.

### Phase 0 — Week 1: Spike & foundation

| # | Task | DoD |
|---|---|---|
| 0.1 | Reserve names: npm scope `@mcpulse` (plain `mcpulse` is taken on npm), GitHub org, domain `mcpulse.dev`, Telegram bot handle, X/LinkedIn handle | All reserved; note in README |
| 0.2 | Monorepo scaffold (Bun workspaces, TS strict, lint, format, test) | `bun test` and `tsc` green in CI |
| 0.3 | Port prototype (Tether) into `core` / `daemon` / `adapters` / `cli` / `mcp` | 24 existing tests pass in new layout |
| 0.4 | Contract capture: record real hook payloads from current Claude Code (macOS + Linux + Windows) | Fixtures in `test/contract/` |
| 0.5 | Verify open questions (§26): max hook timeout, terminal behaviour while hook waits, AskUserQuestion answer path, MCP tool timeout | Written answers in `docs/decisions/` |
| 0.6 | Threat model v1 + `SECURITY.md` | Reviewed checklist |

### Phase 1 — Weeks 2–4: MVP (local + Telegram)

**Week 2 — engine**

| # | Task | DoD |
|---|---|---|
| 1.1 | Broker with lifecycle states (§7.3), escalation, quiet hours, first-answer-wins | Unit + e2e tests for every state transition |
| 1.2 | Policy engine + rule layers + safe signatures + `rules test` | Table-driven tests (≥ 60 cases) |
| 1.3 | Redaction + unicode sanitizer + head/tail clipping | Corpora pass |
| 1.4 | Context builder (transcript tail, git, sessions) tolerant to format changes | Fixtures from 3 Claude Code versions parse; never throws |
| 1.5 | Presence (macOS / Windows / X11 / Wayland fallback) + hand-back + keep-awake | Manual test matrix filled |

**Week 3 — surfaces**

| # | Task | DoD |
|---|---|---|
| 1.6 | Hook handlers: PermissionRequest, PreToolUse(AskUserQuestion), Stop, Notification, PermissionDenied, StopFailure, session events | e2e tests with recorded payloads |
| 1.7 | Telegram adapter: pairing, buttons, confirm tap, reply-to, More/Other, edit-after-answer, commands | Manual run on a real bot; e2e with Telegram API mock |
| 1.8 | MCP server: `ask_human`, `request_approval`, `notify_human` | MCP Inspector test + e2e |
| 1.9 | Local auth: key header, keychain storage, 0600 files | Security checklist items 1–5 pass |

**Week 4 — install & ship**

| # | Task | DoD |
|---|---|---|
| 1.10 | `mcpulse setup` wizard (local mode) | New Mac + new Ubuntu VM: install → test card in ≤ 5 min |
| 1.11 | Service installers (launchd, systemd --user, Windows Task Scheduler) | Survives reboot on all three |
| 1.12 | Hook installer/uninstaller (backup, idempotent, project-local option) | Round-trip leaves settings byte-identical |
| 1.13 | `mcpulse doctor` | Detects each failure in §14.2 on purpose-broken setups |
| 1.14 | Binaries via `bun build --compile` for macOS (arm64/x64), Linux (x64/arm64), Windows (x64) | Downloads from GitHub Releases run without Bun installed |
| 1.15 | README, quick start, 60-second demo video | Reviewed by 2 people who've never seen the project |
| 1.16 | Private alpha with 10 developers | Feedback log; top 5 issues fixed |

### Phase 2 — Weeks 5–7: Polish & channels

| # | Task | DoD |
|---|---|---|
| 2.1 | Slack adapter + app manifest | Real workspace test |
| 2.2 | ntfy + email (Resend) + confirm page | Signed-link test suite |
| 2.3 | WhatsApp Cloud API adapter (tunnel mode) with template opener | Real number test; 24-h window handled |
| 2.4 | Persist open requests; re-send after daemon restart | Kill -9 during wait → card still answerable |
| 2.5 | Per-project `.mcpulse/rules.json` + precedence | Tests for precedence matrix |
| 2.6 | Diff previews for Edit/Write | Card length limits respected per channel |
| 2.7 | Daily digest + `/audit` + `audit export` | CSV opens in Sheets |
| 2.8 | Signed releases (cosign), npm provenance, Homebrew tap, install script with checksum | Verified install on clean machines |
| 2.9 | Docs site (channel guides, rules cookbook, FAQ, troubleshooting) | Search works; every doctor message links to a doc |
| 2.10 | Public beta launch: GitHub, npm, Show HN, r/ClaudeAI, X, LinkedIn, Indian dev communities | 100 installs, 20 GitHub stars/week |

### Phase 3 — Weeks 8–11: MCPulse Cloud

| # | Task | DoD |
|---|---|---|
| 3.1 | Auth service: device flow + PKCE, Google/GitHub/magic link, rotating refresh, PATs | OAuth conformance tests; reuse-detection test |
| 3.2 | `mcpulse login / logout / whoami / devices` + keychain + device keypair | Works headless with `--no-browser` |
| 3.3 | Relay: Durable Object per account, daemon WebSocket (outbound), heartbeats, reconnect | 1,000 simulated daemons; reconnect < 5 s |
| 3.4 | Webhook ingress (WhatsApp, email links, ntfy actions) → route to device | No card text stored (verified by DB inspection) |
| 3.5 | PWA: sessions list, request cards, answer, Web Push, E2E decryption with device-shared key | Installable on Android + iOS; Lighthouse PWA pass |
| 3.6 | Multi-machine `/status` and routing | Laptop + server under one account |
| 3.7 | Billing (Razorpay + Stripe/Lemon Squeezy), plan limits | Test-mode purchase → plan upgrade |
| 3.8 | External security review / pentest of relay + auth | All high/critical fixed |
| 3.9 | Status page, Sentry, alerting, runbooks (§22) | Synthetic check every minute |

### Phase 4 — Weeks 12–16: Teams & agents

| # | Task | DoD |
|---|---|---|
| 4.1 | Team workspaces, invites, roles (owner / approver / viewer) | Role matrix tests |
| 4.2 | Approver rules ("high-risk in `payments/*` needs a lead") | Policy tests |
| 4.3 | Shared audit with retention settings | Export + delete |
| 4.4 | Agent adapter interface; Codex CLI + Gemini CLI adapters | Same e2e suite passes per agent |
| 4.5 | Voice replies (Telegram/WhatsApp voice note → transcription) | 90% accuracy on test set (Hinglish included) |
| 4.6 | Discord adapter | Real server test |

### Working rhythm

- **Daily:** 1 task card at a time, PR per task, CI must be green, self-review checklist.
- **Weekly:** Friday demo video to yourself/testers; update CHANGELOG; re-prioritize from feedback.
- **Definition of done (every PR):** tests added, docs updated, no secrets in logs, `doctor` updated if a new failure mode was introduced.

---

## 20. Testing strategy

| Layer | What | Tooling | Target |
|---|---|---|---|
| Unit | Policy, signatures, redaction, sanitizer, reply parser, context extractor, quiet hours | `bun test` table-driven | ≥ 90% line coverage in `core` |
| Contract | Recorded hook payloads + transcripts from each supported Claude Code version | Fixtures + schema tests | New Claude Code release → re-record within 48 h |
| E2E (local) | Real daemon over HTTP, fake adapter, fake hook client: allow / always / edit / PIN / deny-with-reason / present / questions / multi-select / Other / stop / timeout / hand-back / signed links / MCP ask / mute | `bun test` | Runs in < 10 s in CI |
| Adapter | Each channel against an API mock; manual smoke against real accounts before release | Mock servers + checklist | Every release |
| Security | Secret corpus, unicode corpus, auth bypass attempts, token tamper/replay, fuzzing of reply parser | Custom + `fast-check` | Part of CI |
| Platform | macOS arm64/x64, Ubuntu, Fedora, Windows 11, WSL2, headless server | GitHub Actions matrix + manual | Every release |
| Load (Cloud) | 1,000 daemons, 10 req/s burst per account | k6 | p99 delivery < 2 s |
| Chaos | Kill daemon mid-wait, drop network, sleep/wake, relay restart | Scripts | No lost decisions, no double decisions |
| Dogfooding | Use MCPulse on MCPulse development every day | — | From week 3 |

Already done in the prototype: 24 e2e tests covering the core loop, context, questions, stop, spoofing, mute and HTML escaping.

---

## 21. Release, packaging and distribution

| Channel | Command | Notes |
|---|---|---|
| npm | `npm i -g @mcpulse/cli` | Provenance enabled; postinstall fetches the right binary (checksum-verified) |
| Homebrew | `brew install mcpulse/tap/mcpulse` | macOS + Linux |
| Script | `curl -fsSL https://mcpulse.dev/install \| sh` | Verifies SHA-256 + cosign signature; prints what it will do first |
| Scoop / winget | `scoop install mcpulse` | Windows |
| GitHub Releases | Binaries + SBOM + signatures | Source of truth |
| Claude Code plugin | `/plugin install mcpulse@…` | Bundles hooks + MCP config (P2) |

Versioning: SemVer; release-please; CHANGELOG generated from conventional commits. Each release lists the Claude Code versions it was tested against.

Update: `mcpulse update` verifies signature before replacing the binary; daemon restarts gracefully (open requests persisted).

---

## 22. Operations (cloud relay)

| Area | Plan |
|---|---|
| SLOs | Relay availability 99.9%; card delivery p99 < 2 s; login success > 99% |
| Monitoring | Synthetic test card every minute per region; Sentry for errors; Workers analytics |
| Alerting | PagerDuty/ntfy to you on SLO burn, auth error spikes, webhook signature failures |
| Runbooks | Relay down → daemons fall back to direct channels (Telegram/Slack keep working); auth outage → local mode keeps working; Meta webhook failures; key rotation |
| Backups | D1 daily export; tested restore monthly |
| Secrets | Cloudflare secrets; rotation every 90 days; no secrets in code |
| Incident process | Status page, postmortem within 5 days, user notice if any data exposure |
| Abuse | Rate limits per account/device; block sending to non-verified numbers/emails |

---

## 23. Pricing and business model

| Plan | Price | Includes |
|---|---|---|
| **Local (open source)** | Free forever | Daemon, CLI, MCP, Telegram/Slack/ntfy/email/WhatsApp via your own tunnel, policy, audit |
| **Cloud Pro** | ₹299 / $5 per month | Login, relay (no tunnel), PWA + Web Push, multi-machine (3), WhatsApp/email without setup, 30-day cloud history |
| **Team** | ₹799 / $12 per user per month | Workspaces, roles, approver rules, shared audit, Slack-first, priority support |
| **Enterprise** | Custom | SSO/SAML, self-hosted relay, audit export to SIEM, DPA |

Open-core logic: everything a solo developer needs on one machine is free and local. Money comes from convenience (no tunnels, PWA, multi-machine) and teams (roles, audit). Costs are low on Cloudflare (Workers + DO + D1); WhatsApp template messages are the main variable cost — pass through or cap per plan.

---

## 24. Success metrics

| Metric | Phase 1 target | Phase 3 target |
|---|---|---|
| Time from install to first test card | ≤ 5 min (median) | ≤ 3 min |
| Requests answered remotely / requests sent | ≥ 70% | ≥ 80% |
| Median time to answer (away) | ≤ 5 min | ≤ 3 min |
| Requests needing "More context" | ≤ 15% | ≤ 10% |
| Auto-decided by policy (no ping) | ≥ 40% of permission requests | ≥ 60% |
| Wrong-approval incidents reported | 0 | 0 |
| Weekly active installs | 50 | 1,000 |
| Cloud conversion | — | 5% of active installs |
| Daemon crash rate | < 1 per 1,000 hours | < 1 per 10,000 hours |

Measured from the local audit log (and opt-in counts only telemetry).

---

## 25. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Anthropic ships a full official equivalent (Channels grows Slack/WhatsApp/policy) | Medium | High | Be the best multi-channel + policy + team layer; support Channels as a transport; add other agents (P4) |
| Claude Code hook/transcript format changes | High | Medium | Contract tests per version; lenient parsing; fail open; fast patch releases |
| Hook timeout ceiling lower than needed | Medium | Medium | Verify in Phase 0; shorter waits + "re-ask" pattern if capped |
| Someone approves something harmful from a hijacked account | Low | High | PIN + confirm, hard-deny rules, sender allowlist, device revoke |
| WhatsApp policy/cost changes | Medium | Low | WhatsApp is never the only channel; template fallback |
| Name/trademark conflict ("mcpulse" npm taken) | Medium | Low | Use `@mcpulse` scope and `mcpulse.dev`; check trademarks before launch |
| Solo-founder bandwidth | High | High | Strict phase gates; Phase 1 alone is useful; open-source contributors for adapters |
| Users don't trust a relay with commands | Medium | Medium | Local mode is complete and free; E2E for PWA; relay never stores card text |

---

## 26. Open questions to verify

Answer these in Phase 0 with a 30-minute experiment each; record results in `docs/decisions/`.

- [ ] Longest hook `timeout` Claude Code honours in practice (plan assumes 4 h + 60 s).
- [ ] Does the terminal show its own permission dialog while a `PermissionRequest` hook is still waiting, or only the hook's status message? (Affects hand-back UX.)
- [ ] Can `AskUserQuestion` answers be returned via `updatedInput` instead of the deny-reason route?
- [ ] `MCP_TOOL_TIMEOUT` needed for `ask_human` waits of 30+ minutes.
- [ ] Behaviour of hooks in the VS Code / JetBrains extensions and the Desktop app vs the terminal CLI.
- [ ] Channel mode availability on Team/Enterprise plans for target users.
- [ ] WhatsApp utility template approval time and cost in India.
- [ ] npm org `@mcpulse` availability; domain availability; trademark search.

---

## 27. Glossary

| Term | Meaning |
|---|---|
| **Hook** | A command or HTTP endpoint Claude Code calls at a lifecycle event (permission, stop, …) |
| **Card** | The message MCPulse sends to your phone for one request |
| **Request id** | 5 lowercase letters (no `l`) identifying a card, e.g. `kpqrs` |
| **Presence** | Whether you're at the keyboard (decides terminal vs phone) |
| **Hand-back** | Returning an open request to the terminal when you come back |
| **Escalation ladder** | Ordered channels with delays |
| **Policy** | Rules that auto-allow, auto-deny or ask |
| **Signature** | The narrow pattern "Always" remembers (e.g. `npm run build`) |
| **Relay** | Optional MCPulse Cloud service that routes webhooks and the PWA to your daemon |
| **Device flow** | Login where the CLI shows a code and you approve in a browser |
| **Fail open** | If MCPulse breaks, Claude Code behaves as if MCPulse weren't installed |

---

## 28. References

- Claude Code hooks reference — https://code.claude.com/docs/en/hooks
- Claude Code Channels — https://code.claude.com/docs/en/channels
- Channels reference (permission relay) — https://code.claude.com/docs/en/channels-reference
- OAuth 2.0 Device Authorization Grant (RFC 8628) — https://datatracker.ietf.org/doc/html/rfc8628
- PKCE (RFC 7636) — https://datatracker.ietf.org/doc/html/rfc7636
- OAuth 2.0 Security Best Current Practice (RFC 9700) — https://datatracker.ietf.org/doc/html/rfc9700
- Model Context Protocol — https://modelcontextprotocol.io
- Telegram Bot API — https://core.telegram.org/bots/api
- Slack Socket Mode — https://api.slack.com/apis/socket-mode
- WhatsApp Cloud API — https://developers.facebook.com/docs/whatsapp/cloud-api
- ntfy — https://docs.ntfy.sh
- Cloudflare Durable Objects — https://developers.cloudflare.com/durable-objects/
- Prior art: Claude-Code-Remote — https://github.com/JessyTsui/Claude-Code-Remote · ccgate — https://pkg.go.dev/github.com/tak848/ccgate
- Community pain points: "Stop waiting for approval" guide — https://guides.happier.dev/how-to-fix-waiting-for-approval-in-claude-code-codex-and-opencode · Claude Code issues on notifications/permission prompts (e.g. #13024, #13922, #36885, #55875, #62292)