# MCPulse setup and approval walkthrough

Python 3.11+ is required. Run these commands from the repository root. On Windows, replace `python3` with `py -3`. The core runs directly from source without installing packages. Optional Slack dependencies are described in the [README](../README.md#install-and-test).

## 1. Preview without installing integrations

```sh
python3 -m mcpulse preview
```

Keep this terminal open. The browser opens a disposable demo on port 7720. It does not create your user configuration, install agent hooks/services, execute sample commands, or send messages. Use `preview --port 7721` if the port is busy. `preview --no-open` prints the private login link instead of opening it.

Open **Requests**, then review the sample question. Confirm the Project, Agent and Session ID. Choose an option or write your own answer; written text replaces choices for that question. Send reply to see **Reply saved**, with the same agent/session and a Request ID. Reopening the same link shows the final receipt. Refreshing or submitting again cannot change the first decision.

Try the sample permission with **Allow once**, **Deny request**, or **Return to terminal**. These only change demo state. The completion sample supports follow-up instructions or **Done**. Restart preview to reset all samples. Ctrl-C stops it.

For code development, this preview and the tests are enough. The remaining setup steps are optional and change local configuration when you choose to run them.

### Optional desktop check

`python3 -m mcpulse desktop status` checks whether the native notification tool is available without changing settings. `python3 -m mcpulse desktop test` explicitly submits one sample popup; it needs no daemon or setup and does not enable notifications for later requests. OS permissions and Focus settings can still suppress the popup. See [desktop notifications](../README.md#desktop-popups).

## 2. Run a local workspace

```sh
python3 -m mcpulse setup
python3 -m mcpulse start
```

`setup` creates private configuration in `~/.config/mcpulse` by default. `start` stays in the foreground; keep its terminal open. From another terminal in the repository:

```sh
python3 -m mcpulse away
python3 -m mcpulse ui
```

Use the same configuration directory in every terminal. To isolate a workspace, put `--home /absolute/path` before each command, including `setup`, `start`, `away`, and `ui`.

Create a sample request in a third terminal:

```sh
python3 -m mcpulse demo --kind question
```

This waits for a reply. Refresh the dashboard, open the request and answer it. The demo terminal prints the resulting response. Try `demo --kind permission` and `demo --kind finished` for the other flows. These commands create requests, not actual tool executions. Configured channels can receive these requests, so use `preview` for a fully isolated demo.

## 3. Connect Claude Code only when ready

With the local flow working, install hooks and optionally the MCP tools:

```sh
python3 -m mcpulse hooks install
python3 -m mcpulse mcp install
python3 -m mcpulse doctor --strict
```

These installation commands modify Claude configuration. Restart Claude Code afterward. For project-local hooks, use `hooks install --settings /absolute/project/.claude/settings.local.json` and pass that same path to `doctor --settings`. The default command bridge depends on this Python executable and repository location; reinstall hooks if either moves.

The MCP tools are `ask_human`, `request_approval`, and `notify_human`. Agent labels are sender-reported display metadata. Displaying Claude Cowork or OpenCode does not install a dedicated adapter. MCP Session IDs identify an MCPulse connection, not necessarily the client's conversation ID.

To remove integrations, use `hooks uninstall` (with the same `--settings` path if applicable) and `mcp uninstall` (with the same scope used at installation). Unrelated settings are preserved. Services are separate and are never needed for the preview.

For phone channels, follow [channel setup](channels/setup.md). The local dashboard URL works on this computer only. It is not the remote phone interface.

## 4. Read the result

| Result | Meaning |
|---|---|
| Reply saved | Stored for the waiting agent; delivery is not acknowledged by this receipt. |
| Approval saved | Approval stored; this does not prove the action ran. |
| Request denied | No approval was granted. |
| Returned to terminal | Remote review closed; continue in the original agent session. |
| Request expired | Deadline passed without an approval; continue in the original session. |
| Request cancelled | Session ended or client cancelled the request. |
| Marked done | Completion/notification was acknowledged. |
| Request unavailable | Unknown ID or receipt older than its retention window. |

Receipts keep only the sanitized project, agent, session and request ID, together with final state, for up to 24 hours. Detailed inputs are removed on completion. Stored hook responses may still contain your answer during that retry window; protect the state directory. See [security and retention](../SECURITY.md).

High-risk browser approvals require an approval PIN and an explicit review checkbox. Configure the PIN with `python3 -m mcpulse pin set`, then restart the daemon. PINs are never restored into validation-error forms. Recheck a request before approving it.

## 5. Recover from a problem

- **Missing answer:** the form explains what is missing and retains your other written answers/choices. Complete it and submit again.
- **Connection lost after sending:** open the same request link, or use its Request ID in Activity. A closed receipt means the first decision is already final; an open form means you can review and retry. A failed page load alone does not prove the reply failed.
- **Server unavailable:** restore the local daemon, then reopen the request. If the browser displays its own network-error page, use its reload button after the daemon is available. Python-rendered pages cannot show an offline screen while the server is down.
- **Dashboard locked after restart:** run `python3 -m mcpulse ui` again to get a fresh login. For disposable preview, restart `preview`; old sample IDs no longer exist.
- **Expired login link:** login links are single-use and expire after 60 seconds. Generate a new one. Browser sessions last up to eight hours or until lock/restart.
- **Unclear configuration:** run `doctor`, or `doctor --strict` for exit status 1 on required failures. It reads configuration and the selected settings file without installing anything. It does not validate merged Claude settings, agent login, or end-to-end provider delivery.
- **Need normal terminal prompts:** use `python3 -m mcpulse present`. Pending Claude hook requests return to the terminal. Explicit MCP requests have their own lifecycle.

Pending requests survive a preserved daemon restart with their original deadlines. Normal shutdown hands waiting requests back. Browser login must be renewed after restart; never approve a new request merely to compensate for an uncertain old result.

## 6. Development checks

```sh
python3 -m unittest discover -v
python3 -m compileall -q mcpulse tests
```

Tests use temporary directories, loopback servers and mocked provider transports. Real agent/provider verification and operating-system service testing remain separate from these checks. See [implementation status](implementation-status.md).
