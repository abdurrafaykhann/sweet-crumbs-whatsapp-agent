# Free deploy without a card: Hugging Face Space + Supabase

Hugging Face runs the app as one long-lived Docker container (background replies, the in-memory queue and locks all work in a single container); Supabase keeps the data in Postgres so nothing is lost when the Space restarts. Neither needs a card.

## 1. Database (Supabase)
Create a project (the free plan is enough). In **Project Settings > Database** copy the **Session pooler** connection string (port 5432, IPv4-friendly) and put your database password in it:
`postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres`
Free Supabase projects pause after a week without activity; unpause from the dashboard.

## 2. The Space
1. huggingface.co > **New Space** > SDK **Docker** > blank template > public. Name it e.g. `sweet-crumbs`.
2. **Settings > Variables and secrets**, add as *secrets*:
   `DATABASE_URL` (the string above), `GROQ_API_KEY`, `GEMINI_API_KEY`, `ADMIN_PASSWORD`, `DEMO_PASSWORD`, `JWT_SECRET` (16+ random characters), and later the WhatsApp ones (`WA_VERIFY_TOKEN`, `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`).
   Add as *variables*: `SEED_ON_START=1`, `ENABLE_DEV_CHAT=1`, `ENV=production`.
3. Push the code (needs a Hugging Face access token with write permission when git asks for a password):
   ```bash
   scripts/deploy_hf.sh https://huggingface.co/spaces/<user>/<space>
   ```
4. Open `https://<user>-<space>.hf.space/health` (should show `"db":"postgresql"`), then `/admin` for the dashboard and `/ui` for the chat tester.

A Space sleeps after about 48 hours without visits and wakes on the next request.
