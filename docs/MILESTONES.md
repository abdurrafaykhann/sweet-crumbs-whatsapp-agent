# Milestones

Rebuild of the prototype against [`data/sweet-crumbs-bakery-demo.md`](../data/sweet-crumbs-bakery-demo.md). Tests run after each milestone; one commit per milestone.

| | Milestone | Status |
|---|---|---|
| M0 | Repo setup: git baseline, spec in `data/`, decisions, dependency spike, clean `.venv` | **done** |
| M1 | Data: SQLAlchemy models, seed script from the md (menu, zones, branches, deals, 8 customers, 9 orders), Python business rules + unit tests (zones, fees, minimum, notice periods, advance, Jummah, same-day cutoff), atomic inventory. See [DATA_AND_RULES.md](DATA_AND_RULES.md) | **done** (63 tests, SQLite + PostgreSQL) |
| M2 | Tools + LangGraph agent core: spec tools, per-phone memory, structured order state, short prompt, `get_info`, confirm-in-later-turn guard, keyword bot removed. See [AGENT.md](AGENT.md) | **done** (127 tests, SQLite + PostgreSQL; live-tested on Gemini) |
| M3 | LLM layer: Groq primary, Gemini fallback, backoff with jitter, provider and token logging, per-customer queue, never-no-reply fallback. See [LLM.md](LLM.md) | **done** (159 tests; failover verified live) |
| M4 | Human handoff: summary for the owner, bot silent while escalated, take over / hand back, payment screenshots to a human. See [HANDOFF.md](HANDOFF.md) | **done** (170 tests) |
| M5 | Webhook against the new core, terminal chat simulator, web chat page | **done** (177 tests; web chat verified in a browser) |
| M6 | Dashboard backend: login, orders (filters, status + WhatsApp message), inbox, escalations, menu/stock, customers, analytics, live events. See [DASHBOARD.md](DASHBOARD.md) | **done** (SQLite + PostgreSQL) |
| M7 | Dashboard UI: React + Vite + Tailwind, mobile friendly, served at `/admin`. See [DASHBOARD.md](DASHBOARD.md) | **done** (198 tests; checked in the browser at desktop and phone width) |
| M8 | Evals: 35 cases x 5 rewordings, LLM judge (different provider), caching, per-category runs, multi-turn and adversarial cases | todo |
| M9 | README, Mermaid architecture, results table (Groq vs Gemini), "what I'd improve next" | todo |
