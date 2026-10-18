# Channel setup

All application code is Python. Telegram, SMTP, ntfy and WhatsApp use the standard library. Slack uses the official Python `slack-sdk`; install the optional extra with `python -m pip install '.[slack]'`.

Run `python -m mcpulse channel list` to inspect configuration. Changes take effect after a daemon restart. Configuration examples contain placeholders, not live credentials. Copy examples to a private file outside the repository, replace placeholders, and import with `channel configure CHANNEL --file /path/to/private.json`. The imported copy is mode 0600 on POSIX. Keep the source private too.

## Telegram

Create a bot with BotFather, open its private chat, and stop any other getUpdates client or webhook for it. Run `setup --guided` or `channel pair telegram`, paste the token at the hidden prompt, and send the displayed one-time code to that bot. The code expires after five minutes; only a matching private sender/chat pair can register. Restart, switch `away`, and run `demo`.

Buttons support allow, exact remembered approval, deny, hand-back, stop, and more context. High-risk approvals require a second tap. Question cards advance one question at a time; single-choice questions have buttons. Reply with `1,3` for multi-select, or your own text. Reply to a finished card with the next instruction.

Text commands: `/status`, `/pending` (re-send cards), `/away`, `/present`, `/auto`, `/mute 1h`, `/unmute`, `/audit`, `/rules`, `/help`. Replies: `y ID`, `n ID reason`, `always ID`, `ID edit command`, `ID answer`, `more ID`, `stop ID`. With multiple pending requests, include an id or reply to a card. High-risk typed approval requires `y ID PIN`; configure the PIN locally with `pin set` (at least six digits). Failed PIN attempts are throttled. Never send bot credentials as chat messages.

## Slack

Create your private app from `examples/slack-manifest.json`. Enable Socket Mode, generate an app token with `connections:write`, install the app, and copy the bot token. Open a DM with the bot and obtain your Slack member id and that DM's channel id. Put them in a private copy of `examples/slack.json`; import it with `channel configure slack --file ...`.

The adapter accepts only the configured member in the configured channel, validates the original message, and uses a second button for high-risk approvals. Text answers use the same id syntax as Telegram; high-risk typed approval needs the PIN. Multiple-question text replies use a JSON object mapping each full question to its literal answer text. Live Slack app permissions and real delivery still require an account smoke test.

## Signed web confirmations

Email, ntfy action links, and WhatsApp link replies need an HTTPS origin routed to the **separate confirmation listener**, default `127.0.0.1:7718`. Never route the authenticated daemon API on port 7717 to the internet.

Configure your own reverse proxy/tunnel, then:

```sh
python -m mcpulse config set web_origin https://your-host.example
python -m mcpulse pin set
```

Restart the daemon. Signed links expire within 15 minutes or when their request expires. GET only renders a review page. A POST with its matching confirmation field records an answer once; high-risk allow also requires the local approval PIN. Low/medium-risk links are bearer capabilities: anyone holding one can approve. Do not forward them. The listener supplies no account login, cloud hosting, PWA, or multi-machine relay.

## ntfy

Choose an authenticated private topic and import `examples/ntfy.json`. Without `web_origin` this sends notifications only; with it, actions open confirmation pages. Actions are browser views, not approval HTTP requests. `after_seconds` delays this channel for escalation.

## Email

Import `examples/email.json` with your provider's SMTP submission settings. This implementation requires STARTTLS (normally port 587) and authenticated SMTP; implicit TLS-only port 465 is not supported. Messages contain review links; email previews and GET scanners cannot approve. `web_origin` is required. Email credentials remain in the private local channel file.

## WhatsApp Cloud API

Use a Meta app, WhatsApp Business phone-number id, recipient number, token, app secret, and verification secret. Replace the example API version with a version enabled for your app. Configure the Meta webhook at `https://your-host.example/whatsapp` and use the same verification secret. Import the private JSON configuration.

Inbound bodies are verified with the Meta HMAC signature, the configured business phone id and sender, timestamp freshness, and message-id replay checks. Text replies use the same request-id syntax. High-risk typed allow requires the PIN. Outbound messages include confirmation links. Without a template, Meta permits delivery only in its active customer-service window. To send outside that window, configure `template` and `language` for an approved template whose body has one text parameter; the adapter places the request text and links in that parameter. Template approval and costs remain external account setup, and the real provider limits must be verified with your approved template.

## Escalation and quiet hours

Optional Slack/ntfy/email/WhatsApp configuration supports `after_seconds` (0–86400). A later adapter sends only while the request is still open. `quiet_hours: [23, 7]` suppresses that channel from 23:00 until 07:00 in the machine's local timezone. Telegram delivers immediately. First answer wins across all channels. An ambiguous network send failure can create duplicate messages; closed request ids remain unanswerable.

## Delivery status and retries

The local Channels view distinguishes **not configured**, **unverified**, **connected**, and **needs attention**. Optional adapters start unverified until a send or message update succeeds in the current run; an empty queue, delayed delivery, or a stored message reference is not a new connectivity check. Telegram also checks its polling connection. These are the latest adapter observations, not continuous network probes or proof of phone delivery/read status. Strict diagnostics report unverified channels as not yet healthy.

A failed optional-channel send does not block later cards in the queue. Successful sends retain their stored references so only failures retry. Slack closed-message updates also retry independently. Transport failures report generic status without credentials or provider exception text. If a provider accepted a message but its acknowledgement was lost, a retry can still produce a duplicate notification; request decisions remain first-answer-wins.

Telegram, Slack, and other channel card text includes the sender-reported agent and Session ID. These identify the request for review; they are not verified agent credentials. Closed requests release in-memory sent-card/question-draft tracking.
