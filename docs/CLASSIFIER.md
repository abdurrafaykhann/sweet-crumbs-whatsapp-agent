# Intent + mood tags and angry-customer flag

Optional. Needs the Roman Urdu classifier service from the `roman-urdu-classifier` project (`POST /classify`).
With `CLASSIFIER_URL` empty, nothing in this document is active and the bot behaves exactly as before.

## What it does
1. Every inbound customer text message is sent to the classifier (2 s timeout) after it is logged.
2. The result is stored in `message_tags` (a new table: `create_all` adds it, no migration for existing databases).
3. The dashboard shows an intent chip and a mood chip (😊 😐 🙁 😡) in the inbox list and next to each customer message.
4. If the service says the customer is confidently `angry` (`flag_for_human`) and `ANGRY_AUTOFLAG=1`, the chat is handed to staff:
   a high-priority escalation `angry_customer` is opened (same path as `escalate_system`), the customer gets one short apology in
   their language, and the bot stays silent until staff hand back. A second angry message does not create a second escalation.

## Safety
- Classifier slow, down, wrong status or unknown labels: the message is answered as if the feature did not exist.
- `ANGRY_AUTOFLAG=0` is the kill switch: tags are still shown, nothing is escalated.
- The threshold lives in the classifier service (`ANGRY_FLAG_THRESHOLD`, default 0.6). Tune it on real messages (precision first:
  a false flag silences the bot for a customer who did not need a person).
- Only the customer's own message text leaves this app (no phone number, name or order data).

## Run
```bash
# classifier service (in the roman-urdu-classifier repo)
MODEL_PATH=checkpoints/encoder-s13 CLASSIFIER_API_KEY=change-me uvicorn ruc.serve.api:create_app --factory --port 8100
# this app
CLASSIFIER_URL=http://localhost:8100 CLASSIFIER_API_KEY=change-me python -m app.run
```

## Tests
`tests/test_classifier.py` (16 tests): tagging, escalation, no duplicate escalation, kill switch, five kinds of service failure,
Urdu script, truncation, and the dashboard API fields.
