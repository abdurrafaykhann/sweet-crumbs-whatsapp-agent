# Deploy

The app is a stateless container (FastAPI) plus **PostgreSQL** (orders, sessions, messages) and **Redis** (message de-duplication, rate limits, per-customer locks).
It needs a host that runs a long-lived container; Vercel's serverless functions are a poor fit for the background reply tasks.

## Option A: everything on your machine (needs Docker Desktop running)
```bash
docker compose up --build        # app + postgres + redis, demo at http://localhost:8000/demo
```

## Option B: Render (Blueprint in `render.yaml`)
1. Push this folder to a GitHub repository (`.env` is git-ignored; never commit keys).
2. Render > **New > Blueprint** > pick the repo. It creates the web service, a Postgres database and a Redis (Key Value) instance and wires `DATABASE_URL` / `REDIS_URL`.
3. Fill the prompted secrets: `GROQ_API_KEY`, `WA_VERIFY_TOKEN`, `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`.
4. Open `https://<service>.onrender.com/health`; it should show `"db":"postgres","kv":"redis"`.
5. Use `https://<service>.onrender.com/webhook` as the WhatsApp callback URL (WHATSAPP_SETUP.md).

Free-tier caveats to check on Render's current pricing page: free web services sleep when idle (the first message after a pause is slow, and Meta may retry), and free databases can expire. For a real shop use a paid always-on plan.

## Option C: Railway / Fly.io / any VPS
Use the `Dockerfile` and set the same environment variables; add a Postgres and Redis plugin or container and set `DATABASE_URL` and `REDIS_URL`.

## Environment variables
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db`; unset = local SQLite file |
| `REDIS_URL` | `redis://host:6379/0`; unset = in-memory (single instance only) |
| `GROQ_API_KEY` | free LLM (or `LLM_BASE_URL` + `LLM_MODEL` + `LLM_API_KEY` for any OpenAI-compatible API) |
| `WA_VERIFY_TOKEN`, `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET` | WhatsApp Cloud API |
| `ADMIN_TOKEN` | protects the team endpoints (payment confirm, order status); send as `x-admin-token` |
| `DEMO_MODE=1` | enables the "Reset demo" button on `/demo` (turn off for a real shop) |
| `RATE_LIMIT_PER_MIN` | messages per customer per minute (default 20) |

## Operating notes
- **Admin dashboard `/` and `/api/stats` have no login yet.** Put them behind your host's access control or add auth before sharing the URL publicly; they show customer numbers and chats.
- The Groq free tier is rate limited (about 8k tokens/min when tested), fine for a demo, not for real traffic. Switch to a paid tier or another provider by changing three env vars.
