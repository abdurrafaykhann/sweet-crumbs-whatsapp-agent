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
