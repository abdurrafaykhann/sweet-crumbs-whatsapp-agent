## Intent and mood tags, angry-customer auto-flag

Adds an optional connection to the Roman Urdu message classifier (separate project). Off unless `CLASSIFIER_URL` is set.

- `app/classifier.py`: client with a 2 s timeout, strict label validation, kill switch (`ANGRY_AUTOFLAG=0`). Never raises into the chat flow.
- `message_tags` table (new, so no migration), written in `inbound.process_incoming` after the message is logged.
- Confident `angry` -> `agent.escalate_system(... "angry_customer", priority high)` + one apology; the bot stays silent until staff hand back.
- `/api/admin/conversations` and `/conversations/{phone}` return `tag`; `Inbox.jsx` shows intent and mood chips and an "auto-flagged" badge. Dashboard rebuilt.
- Docs in `docs/CLASSIFIER.md`, env vars in `.env.example`.

Tests: 16 new, full suite 225 passed. Not covered: a live classifier service (the HTTP call is faked); the angry threshold has not been tuned on real messages.
