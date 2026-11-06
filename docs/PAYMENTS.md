# Payment, delivery details and order lifecycle

All account details in `app/shop.py` (`PAYMENT`) are **fictional placeholders** (zeros). Replace them before real use. The AI can only repeat what the tool returns; it cannot invent account numbers.

## Rules (enforced by the server, not just the prompt)
| Order total | How the customer pays |
|---|---|
| under Rs 4000 | cash on delivery / pickup (confirmed immediately), or pay the full amount online |
| Rs 4000 and above (all 3 lb cakes) | **50% advance online**, balance in cash on delivery / pickup (or pay everything online) |

An order that needs money starts as **`awaiting_payment`** and is *reserved* (stock held) for 2 hours (`hold_minutes`). If nothing is paid in time it is cancelled and the stock is released.

## Lifecycle
```
customer says yes
   |-- cash order ------------------------------------------> confirmed
   '-- needs payment -> awaiting_payment
          | customer sends screenshot / transaction ID
          v
      payment_submitted     (the AI can only get an order to here)
          | team member checks the money in the dashboard
          |-- Confirm payment -> confirmed  (customer gets a WhatsApp message automatically)
          '-- Reject          -> awaiting_payment  (customer is told why, reservation extended)
confirmed -> preparing -> ready -> out_for_delivery -> delivered      (moved by the team from the dashboard)
```
**Only a human** (dashboard button, or `POST /api/orders/{id}/payment` with `{"action":"confirm"}`) can move an order to `confirmed` after an online payment. The AI is instructed never to say "confirmed" earlier, and cannot make it true.

## What the AI collects for every order
1. Which item and which size/flavour (cannot be skipped).
2. For cakes: the exact text to write on it (stored in the order note, repeated back for spelling).
3. Pickup or delivery; for delivery the full address **in a served area** (the server rejects unknown areas), landmark, receiver's name/number if it is a gift.
4. The exact day and time (checked against each item's lead time and the shop's opening hours).
5. Payment method, then the payment details if money is due.

## Other situations it handles
Changing address / time / message before baking starts (`update_order`), cancelling (unpaid: stock released; paid: the team is notified for the refund), "I paid but nothing happened", payment methods it does not accept, discounts (politely no), bulk or custom orders (escalated to the team), product photos (team), out-of-stock and not-on-menu items, "same as last time", complaints and late orders (apologises, escalates, gives the team number), shop closed right now, and rude messages.

## Team API
| Endpoint | Purpose |
|---|---|
| `POST /api/orders/{id}/payment` `{"action":"confirm"\|"reject","amount":2600,"reason":"..."}` | verify or reject a payment; messages the customer |
| `POST /api/orders/{id}/status` `{"status":"preparing"}` | move the order along the kitchen/delivery stages |
| `GET /api/team-messages?customer=...&after=0` | proactive messages (used by the demo page) |
Set `ADMIN_TOKEN` and send it as the `x-admin-token` header to protect these endpoints. The dashboard pages themselves still have no login.

## Real WhatsApp notes
- Payment screenshots arrive as `image` messages; the app records them as proof (it does not read the image; a human looks at it).
- The "payment confirmed" message is sent as a normal reply, so it only reaches the customer inside WhatsApp's 24-hour customer-service window. After that you need an approved message template.
