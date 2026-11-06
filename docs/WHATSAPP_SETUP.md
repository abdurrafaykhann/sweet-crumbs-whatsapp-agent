# Connect a real WhatsApp number (free test number)

You do these steps in your own Meta account; nothing here can be done for you. Total time: about 30 minutes.

## 1. Create the app
1. Go to developers.facebook.com, log in, **My Apps > Create App** > use case **Other / Business** > app type **Business**.
2. On the app dashboard add the **WhatsApp** product. Meta creates a free **test phone number** and a test business account.
3. Open **WhatsApp > API Setup**. Note down:
   - **Phone number ID** (not the phone number itself) -> `WA_PHONE_NUMBER_ID`
   - **Temporary access token** (valid 24 h; fine for the first test) -> `WA_ACCESS_TOKEN`
4. In the same page, under **To**, add your own phone as a recipient and enter the code WhatsApp sends you (the test number can only message up to 5 verified recipients).
5. **App settings > Basic > App secret** (click Show) -> `WA_APP_SECRET`. This lets the app reject forged webhook calls.

## 2. Give Meta a public HTTPS URL
The app must be reachable from the internet. Either deploy it (see DEPLOY.md) or, for a quick test from your laptop, run a tunnel:
```bash
uvicorn app.main:app --port 8000
cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
```
Use the printed `https://...` address. Meta requires HTTPS.

## 3. Set the webhook
1. Pick any secret string for `WA_VERIFY_TOKEN` (it only has to match on both sides) and put all four values in `.env` (local) or your host's environment settings.
2. **WhatsApp > Configuration > Webhook > Edit**: Callback URL `https://YOUR-HOST/webhook`, Verify token = your `WA_VERIFY_TOKEN`. Click **Verify and save** (the app answers the handshake automatically).
3. Under **Webhook fields**, **Subscribe** to `messages`.

## 4. Test
Send "Assalam o alaikum" from your verified phone to the test number. You should get Aisha's reply within a few seconds, and the conversation appears on the dashboard at `/`.

If nothing arrives: check `/health`, the host logs (look for `WhatsApp send failed`), that the token has not expired, and that your phone is in the recipient list.

## 5. Before showing it to a real shop
- **Permanent token**: Business Settings > Users > **System Users** > create one, assign the app, generate a token with `whatsapp_business_messaging` and `whatsapp_business_management`. Replace the 24 h token.
- **Real number**: add the shop's own number (it must not already be registered on the WhatsApp app) and complete business verification if Meta asks.
- **Service window**: you can reply freely only within 24 hours of the customer's last message. Messages you start yourself (for example "your order is ready") need an approved message template.
- **Privacy**: tell customers an assistant replies, and keep the human handoff active. Do not demo with other people's real chats.
