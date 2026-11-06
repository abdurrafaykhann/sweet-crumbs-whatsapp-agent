# Decisions log

Source of truth for business data and bot behaviour: [`data/sweet-crumbs-bakery-demo.md`](../data/sweet-crumbs-bakery-demo.md). Fictional business; today in the demo is **Friday, 6 November 2026**.

## Decisions (made at the start of the spec rebuild, M0)
| # | Decision | Why |
|---|---|---|
| D1 | Adopt **SQLAlchemy 2.0** (Alembic for migrations) and **LangGraph + LangChain** | The dashboard, handoff state, customers, token tracking and per-phone memory need real models and a checkpointer. SQLite for dev, Postgres via `DATABASE_URL`. |
| D2 | **No keyword or regex intent matching at runtime.** The keyword offline bot and the keyword language detector leave the running path in M2. A tiny scripted fake LLM stays for unit tests only. | The spec requires understanding meaning, not matching words. |
| D3 | **Replace the old demo data completely** with the md's data, loaded by a seed script. | One source of truth. |
| D4 | Git history from the prototype baseline; **one commit per milestone**. | Clean, reviewable portfolio history. |
| D5 | **Free tiers only: Groq primary, Gemini fallback.** Evals run per category over several days with caching; the judge runs on Gemini lite. | Measured limits below make a single-day full run impossible. |
| D6 | **Loyalty assumptions** (the spec is silent): points redeem in blocks of 100 (= Rs 500 off); the birthday 10% (one cake, birthday month) does **not** stack with deals or points. | Simple, explainable, easy to change later. To be confirmed. |
| D7 | Demo clock: `DEMO_NOW` env var fixes "today" for repeatable tests and demos. | Date-dependent rules (Friday Jummah, same-day cutoff, notice periods). |

## Adjustments made during M1
- **Alembic is deferred.** The schema is created with `metadata.create_all` for now; migrations are introduced once the models stop changing (M4, when handoff and conversation tables land).
- **New default database file:** `sweetcrumbs.db` (the prototype's `agent.db` has an incompatible schema). The legacy modules (`app/db.py`, `app/tools.py`, `app/agent.py`, `app/shop.py`) keep working and are removed in M2 together with the old demo data.
- **Jummah slots:** a 1-hour delivery slot that overlaps 1:00-2:30 pm on a Friday is refused, so the first clean slot after the break is 3 pm. ASAP orders placed during the break go out after 2:30 pm.
- **Serving guide gaps** are resolved by rounding up (16-17 guests get a 3 lb cake); 4 lb is offered as a custom cake only.
- **Seed-time spec checks:** wording changes, missing deal schedules, zone disagreements and demo-order arithmetic stop the seed with an explicit error.

## Adjustments made during M2
- **Order draft lives in the database, not in the chat.** `drafts` holds the structured order per phone; every model call re-injects a compact "ORDER IN PROGRESS" block, so trimming the chat history (last 12 messages) cannot lose anything.
- **Confirm guard:** the draft carries `version`, `summary_version` and `summary_turn`. `confirm_order` needs a summary of the *current* version created in an *earlier* customer turn; any change creates a new version.
- **`draft_order` vs `update_draft`:** `draft_order` starts a fresh draft only when the existing one is from an earlier customer message; within one message the two merge (LangGraph runs a step's tool calls concurrently), and draft changes are serialised per phone.
- **`get_info(topic)`** searches the text of the fact topics, not just their names ("halal" finds the business profile). It was added because the spec's tool list had no business-info tool.
- **One open escalation per customer:** later escalations add their details to the open one (the cancel tool escalates by itself and the model may also call `escalate_to_human`).
- **Stock is released only if this system reserved it:** the seeded demo orders carry no reservation record, so cancelling them does not add stock that was never deducted.
- **Assumption D8:** an ASAP pickup is ready in about 30 minutes (the spec gives no pickup time).
- **Status on confirm:** `scheduled` when the slot is on a later day, `confirmed` for today, `awaiting_advance` when an advance is due.
- **Custom/theme, wedding and bulk orders are never placed by the bot:** the draft reports `needs_human`, details are collected, then `escalate_to_human`.
- The keyword offline bot, the keyword language detector and the old demo data are gone (D2, D3); the prototype remains in git history at `d2328d1`.

## Adjustments made during M3
- **Tool calls are not replayed across providers.** A request may move from Groq to Gemini mid-conversation; Gemini 3 rejects foreign function calls (missing its own thought signature). Foreign tool exchanges are flattened to a text note of what was looked up. Found live, covered by tests.
- **Cooldowns are per process.** A provider whose daily quota is gone is skipped for the time it asked; with several app instances each learns separately (a shared cooldown in Redis is listed under "what I'd improve next").
- **Quick follow-up messages are coalesced** into one model call (free-tier friendly) instead of one call each; each raw message is still logged.
- **While a chat is escalated the bot is silent** (also when the assistant itself gave up): staff hand it back.
- Retry after the "one moment" message resumes from the checkpoint, so the customer's message is never added to the conversation twice.

## Spec gaps found while reading (to resolve during the build)
- No tool for business info (hours, branches, deals, policies, allergens, storage). Plan: add `get_info(topic)` so the prompt stays short.
- Serving guide has a hole between 15 and 18 guests; the "15 to 20 people, take 3 lb" answer conflicts with "3 lb serves 18 to 22".
- Points redemption and discount stacking rules (see D6).

## M0 spike results (LangChain `init_chat_model` + tools, measured)
| Check | Groq `openai/gpt-oss-120b` | Gemini `gemini-flash-lite-latest` |
|---|---|---|
| Tool call + result round trip | works | works |
| Token usage reported (`usage_metadata`) | yes | yes |
| Reply shape | plain string | **list of content blocks** (with a `signature` extra), so M3 needs a text normaliser |
| Key variable | `GROQ_API_KEY` | `GEMINI_API_KEY` alone is enough |

## Free-tier limits observed (Nov 2026)
- **Groq `gpt-oss-120b`:** 8,000 tokens/min, 1,000 requests/day, **200,000 tokens/day**. The old 3,200-token prompt exhausted the daily budget in about 50 messages.
- **Gemini full-size flash (`gemini-3.5-flash`):** **20 requests/day** per model on the free tier (unusable for development).
- **Gemini lite (`gemini-flash-lite-latest`):** 24 consecutive requests with no limit hit; `gemini-3.1-flash-lite` is limited to 15 requests/min.
- Model names change often (`gemini-2.5-flash` was already retired for new accounts): models are configured in `.env`, never hard-coded.
- Consequence: short prompt, menu through tools, trimmed history, structured order state, caching in evals.

## Environment
Python 3.12 virtual environment in `.venv` (`pip install -r requirements-dev.txt`). The system default `python` on the dev machine is 3.13 and does not have the dependencies.
