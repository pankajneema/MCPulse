# Changelog

## 0.3.0a1

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
