# Changelog

## 0.3.0a1

- Added `desktop status` for read-only backend diagnostics and `desktop test` for an explicit sample popup, both usable without setup or a running daemon.

- Added opt-in desktop popup notifications for macOS, Windows and graphical Linux sessions, with metadata-only alerts, shared delivery deduplication/retries, configuration validation and dashboard status. Native OS calls are covered by mocks; real desktop verification remains pending.

- Isolated optional-channel send and Slack update failures so one rejected message cannot block later requests; retained successful-send deduplication.
- Distinguished unverified channel setup from successful provider contact, added agent/session labels to channel cards, and cleaned up closed request tracking.

- Added persistent result receipts with agent/session metadata, explicit closed/expired states, safe duplicate form submissions, status-first retry guidance, and preserved validation input.
- Added a preview-first setup and recovery guide.

- Show agent names and explicit Session IDs in request details, lists, and sessions; retain MCP client names.

- Added read-only, project-aware setup diagnostics, stale/duplicate hook detection, and strict diagnostic exit codes.

- Fixed loopback startup dependency on reverse DNS, bounded CLI startup polling, and portable service-path CI checks.

- Added a Python-rendered local dashboard with request review, search/filtering, native question forms, follow-ups, sessions, channels, presence, and activity.
- Added `ui` with short-lived single-use login tickets, bounded browser sessions, CSRF checks, Host/Origin validation, and PIN-confirmed high-risk approvals.
- Added `preview` for disposable sample data without configuring integrations or sending messages.
- Restyled signed phone confirmation pages and corrected the referrer policy so browser form submissions retain a usable Origin.
- Kept JavaScript/build tooling out of the runtime; updates use explicit refresh.

## 0.2.0a1

- Added question and completion hooks, session/error notifications, and stdio MCP tools.
- Added SQLite request recovery and a reconnecting, idempotent command hook bridge.
- Added contextual cards, exact remembered approvals, bounded user/project regex policies, edited-command checks, and PIN approvals.
- Added Telegram pairing, text commands, sequential questions, and richer controls.
- Added optional Slack Socket Mode, ntfy, STARTTLS email, WhatsApp, and signed web confirmation routes.
- Added native idle detection, keep-awake hooks, user service definitions, audit CSV, retention, and daily digests.
- Kept hosted cloud/team features explicitly outstanding and separated mock verification from live provider validation.

## 0.1.0a1

- Initial Python permission relay, Telegram buttons, local CLI, audit, and hook installer.
