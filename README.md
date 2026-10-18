# MCPulse

**Claude Code needs you → your phone buzzes → you answer → Claude keeps working.**

Python 3.11+ implementation of the local MCPulse workflows. Version **0.3.0a1** adds a Python-rendered local dashboard to the multi-channel development build. It is not yet the complete hosted/team product or a production-certified release. See the exact [implementation status](docs/implementation-status.md).

No Node or Bun. The core and Telegram use the Python standard library; optional Slack support uses the official Python Slack SDK.

## Start

```sh
cd /Users/mac/pnkj/MCPulse
python3 -m mcpulse setup
python3 -m mcpulse start
```

In another terminal, from this repository:

```sh
python3 -m mcpulse away
python3 -m mcpulse demo
```

The demo waits without executing a command. In another terminal, use `status` to find its id, then `answer REQUEST_ID allow`. Try `demo --kind question` and `demo --kind finished` too.

For Telegram, stop the daemon and run `python3 -m mcpulse setup --guided`. Paste the bot token at the hidden prompt, then send the displayed one-time pairing code to the bot in a **private** chat. Restart the daemon, switch it to `away`, and run the demo. Existing users can retain `setup --telegram-user NUMERIC_ID`.

## Dashboard

Try the UI immediately, without configuring channels or Claude:

```sh
python3 -m mcpulse preview
```

This opens a disposable in-memory demo at port 7720 with permission, question, and completion samples. No commands execute, no messages are sent, and no user configuration is changed. Restart the preview to reset its sample data. Use `--port 7721` if needed, or `--no-open` to print the login link without launching a browser.

For the real workspace, keep `python3 -m mcpulse start` running, then in another terminal:

```sh
python3 -m mcpulse ui
```

The dashboard provides request search/filtering; full input/context review; allow, deny, and return-to-terminal actions; native question choices and written answers; follow-up/Done; session status; channel health; presence; notification pause; and metadata activity history. High-risk approval requires both your configured PIN and explicit confirmation. Presence and mute changes apply to the running daemon. Credentials and channel configuration remain in the CLI.

The UI uses Python-rendered HTML/CSS and native browser forms, without JavaScript, Node, a frontend build, or additional runtime dependencies. Refresh to fetch new requests; it does not stream updates. The one-time login link expires after 60 seconds; an authenticated browser session lasts eight hours or until Lock/restart. If login expires, run `ui` again. The localhost dashboard is for this computer, not a remote phone endpoint or installable PWA. Phone confirmation links use the separate HTTPS listener described below.

## Install the Claude integration

After the demo works:

```sh
python3 -m mcpulse hooks install
python3 -m mcpulse mcp install
```

Restart Claude Code. Hooks cover permissions, AskUserQuestion, completion/follow-up, notifications, errors, and session events. MCP provides `ask_human`, `request_approval`, and `notify_human`.

The default hook is a Python command that submits once and polls. It can reconnect to the same persisted request after a daemon restart. `hooks install --http` selects direct HTTP hooks when persistence across the connection is unnecessary. Hook installation backs up settings, preserves unrelated hooks, and is idempotent. `hooks uninstall` restores the original bytes when there were no intervening edits; otherwise it preserves later edits and removes identifiable MCPulse hooks.

Use `--settings /absolute/project/.claude/settings.local.json` for project-local hooks. `mcp install --scope project` registers project MCP configuration through the installed Claude CLI. MCP and long hook timeouts must still be verified against your actual Claude version and environment.

## Check setup without installing anything

```sh
python3 -m mcpulse doctor
python3 -m mcpulse doctor --settings .claude/settings.local.json
python3 -m mcpulse doctor --strict --settings .claude/settings.local.json
```

`doctor` is read-only and works even before `setup`: missing or invalid configuration produces a repair hint. With a valid config, it checks the daemon and the selected Claude settings file. It detects missing/duplicate events, disabled hooks, stale command paths or HTTP credentials/ports, incorrect matchers, changed timeouts, and asynchronous permission hooks. It never prints stored credentials.

The default settings path is `~/.claude/settings.json`; pass `--settings` for project hooks. This checks that file only, not Claude's merged user/project/managed settings or account login. After installing or changing hooks, restart Claude and check its `/hooks` menu. Normal `doctor` exits successfully after producing its report; `--strict` exits 1 if a required check fails. An unavailable idle sensor is optional because explicit away/present modes work without it.

## Reply

```sh
python3 -m mcpulse status
python3 -m mcpulse answer ID allow
python3 -m mcpulse answer ID allow --confirm
python3 -m mcpulse answer ID deny --reason "Use staging"
python3 -m mcpulse answer ID always
python3 -m mcpulse answer ID edit "git diff"
python3 -m mcpulse answer ID text "My answer or next instruction"
python3 -m mcpulse answer ID done
python3 -m mcpulse answer ID handback
python3 -m mcpulse answer ID stop
```

`--confirm` is required for high-risk local approvals and high-risk edited commands. Telegram/Slack high-risk buttons use a fresh second confirmation. High-risk typed or web approvals require a PIN configured with `pin set`.

Question answers may be an option number, comma-separated indices for multi-select, or free text. Telegram advances through multiple questions. For a single atomic multi-question API/CLI answer, supply a JSON object mapping each full question to its literal answer text. This also disambiguates numeric option labels. Claude receives the original questions plus the user's answers through `updatedInput`. A reply to a finished request becomes a user instruction through the Stop hook; Done permits completion.

## Presence and services

```sh
python3 -m mcpulse present
python3 -m mcpulse away
python3 -m mcpulse auto
python3 -m mcpulse config set presence auto
python3 -m mcpulse config set keep_awake true
python3 -m mcpulse service show
python3 -m mcpulse service install
```

`present` hands waiting hook requests back to the terminal. `auto` uses native macOS/Windows idle detection, or `xprintidle` on X11. Unsupported sensors (including most Wayland/headless environments) choose present; use explicit away on a headless machine. Idle threshold defaults to 120 seconds. Explicit MCP requests can still contact you while present.

Keep-awake is opt-in: caffeinate on macOS, systemd-inhibit on Linux, and the Windows execution-state API. Availability and OS permission differ by platform. User services are generated for launchd, systemd --user, and Windows Task Scheduler. Run `service show` to inspect the exact definition; `service uninstall` removes it. Actual reboot/service validation on each OS is pending.

`start` runs in the foreground; Ctrl-C or `stop` shuts down and returns waiting hooks to Claude. `restart` preserves requests and starts the replacement in the foreground. A service manager should handle process crashes. Pending requests are persisted, expire at their original deadline, and are not silently approved after recovery. Runtime presence changes are not saved as the next startup default; set `config set presence away` or `auto` for unattended service use.

## Channels and confirmations

[Channel setup](docs/channels/setup.md) covers Telegram, Slack Socket Mode, ntfy, SMTP email, WhatsApp Cloud API, delayed escalation, and quiet hours. Configure channel files with:

```sh
python3 -m mcpulse channel configure slack --file /private/path/slack.json
python3 -m mcpulse channel list
python3 -m mcpulse test
```

`test` queues a notification; it does not prove delivery. `doctor` and `status` report daemon/channel state.

Email, WhatsApp links, and actionable ntfy notifications use a separate confirmation listener. Set `web_origin` to your own HTTPS tunnel/reverse-proxy origin pointing to loopback port **7718**. Never expose daemon API port **7717**. GET renders a review page; only a confirmed POST can answer. Links are signed, action-bound, short-lived, and single-use through the request lifecycle. The confirmation listener is not a cloud relay or PWA.

## Rules, context, and audit

By default, decisions require a human except for built-in hard-deny patterns. Exact low/medium-risk actions can be remembered per project. No executable-prefix wildcard is remembered. High-risk, compound, masked, shortened, or disguised requests cannot be remembered or silently allowed. Changed previews require the terminal.

User rules live at `MCPULSE_HOME/rules.json`. `tool` and `match` are regular expressions. Project rules at `.mcpulse/rules.json` load only after explicit `rules trust /absolute/project`; never trust a repository you do not control. Regex evaluation runs in a bounded subprocess so pathological patterns cannot hang the daemon. Denies override remembered/configured allows, and high-risk requests still require a human. See `examples/rules.json`.

```sh
python3 -m mcpulse rules test "git push --force origin main"
python3 -m mcpulse rules list
python3 -m mcpulse rules forget /absolute/project
python3 -m mcpulse audit
python3 -m mcpulse audit --csv > audit.csv
python3 -m mcpulse config set daily_digest_hour 18
```

Context reads at most the tail of a JSONL transcript beneath `~/.claude`, plus a local Git branch lookup. It shows recent task/message/steps when that evidence is available. No arbitrary transcript path outside Claude's directory is read. Redaction is best-effort.

Configuration defaults to `~/.config/mcpulse`; `MCPULSE_HOME` or `--home /path` changes it. POSIX configuration/token/state files are private; no OS keychain integration or Windows ACL hardening is claimed. Audit is metadata only, with a configurable 30-day default retention. Open request payloads are stored in private SQLite for recovery, then logically cleared on completion; completed hook responses remain for at most 24 hours to support retry. **Pending payloads and returned answers can contain secrets**; protect this directory and its backups. SQLite/WAL snapshots may retain deleted bytes. Read [SECURITY.md](SECURITY.md).

## Install and test

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[slack]'
.venv/bin/mcpulse --help
.venv/bin/python -m unittest discover -v
```

Omit `[slack]` for a core install without runtime dependencies. On Windows use `.venv\Scripts\python` and `.venv\Scripts\mcpulse`. Run directly from source with `python3 -m mcpulse` if you do not want an installation.

Tests use temporary homes, real loopback HTTP, subprocess CLI/MCP sessions, SQLite recovery, signed confirmation pages, and mocked provider transports. They do not send real messages, install services, or alter your Claude settings. Optional Slack tests use its installed SDK without connecting to a workspace.

The [original brief](docs/product-spec.md) is preserved. The user-requested Python stack supersedes its TypeScript/Bun choice. Hosted auth/relay/PWA, multi-machine accounts, teams, billing, other agent adapters, release signing, and external validation remain tracked separately; they are not represented as completed features.

### Agent labels and session IDs

Request details show the agent and Session ID. Claude hooks default to `Claude CLI`; MCP requests use the client name from initialization and an MCPulse-generated connection session ID. API callers can provide an optional `agent` string (1–100 characters), such as `claude-cli`, `claude-cowork`, or `opencode`. Names are display metadata supplied by the sender, not verified identities or proof of a dedicated integration. Cowork/OpenCode adapters are not installed or implied by these labels.
