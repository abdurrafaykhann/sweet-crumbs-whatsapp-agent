# Owner dashboard (M6 API, M7 UI)

`ADMIN_PASSWORD` and `JWT_SECRET` (16 characters or more) in `.env` switch the dashboard on; with either missing or weak the login answers 503 (it is off, never open).
`POST /api/admin/login {"password": "..."}` returns a JWT (12 hours, HS256). Every other route needs `Authorization: Bearer <token>` (the server-sent-events route also accepts `?token=`). Login attempts are limited to 10 per minute per address.

## API (`app/admin.py`)
| Area | Routes |
|---|---|
| Orders | `GET /orders?status&date&branch&fulfilment&q&limit&offset`, `GET /orders/{code}` (lines and timeline), `POST /orders/{code}/status` `{status, rider_name, eta}`, `POST /orders/{code}/payment` `{action: confirm\|reject, amount, note}`, `PATCH /orders/{code}`, `POST /orders/{code}/cancel` |
| Conversations | `GET /conversations?q&escalated`, `GET /conversations/{phone}` (messages with the tools the bot used, order in progress, brief), `POST .../take-over`, `POST .../reply` `{text}`, `POST .../hand-back` |
| Escalations | `GET /escalations?status`, `POST /escalations/{id}/resolve` `{hand_back}` |
| Menu and stock | `GET /menu`, `PATCH /variants/{id}` `{price, stock}`, `POST /products/{sku}/sold-out`, `POST /products/{sku}/back-in-stock` `{stock}`: the bot reads the same rows, so changes apply to the next message |
| Customers | `GET /customers?q`, `GET /customers/{id}` |
| Analytics | `GET /analytics?date&days` |
| Live | `GET /updates?after_order&after_escalation&after_message` (polling), `GET /events` (server-sent events: `order`, `escalation`, `message`) |

## Order status flow (`rules.next_statuses`)
```
draft -> confirmed | cancelled
awaiting_advance -> (advance verified by a person) -> confirmed / scheduled
confirmed | scheduled -> preparing -> out_for_delivery (needs a rider; ETA defaults to the zone's longest time) -> delivered      (delivery)
                                    -> ready_for_pickup -> delivered                                                              (pickup)
any open status -> cancelled by staff (stock the system reserved goes back on sale)
```
Every change messages the customer on WhatsApp **in their language** (English, Roman Urdu or Urdu script) with fixed wording (`app/notify.py`: preparing, out for delivery with rider and ETA, ready for pickup, delivered with the points earned, cancelled, advance confirmed, payment could not be verified) and is kept in the chat history. Delivering an order adds its Crumbs Club points (1 per Rs 100); a delivery more than 30 minutes late is noted on the timeline (the fee refund is processed by a person).

## Payment verification
Only a person confirms an advance. A customer's screenshot creates a high-priority escalation (M4); `POST /orders/{code}/payment {"action": "confirm"}` records the amount, moves the order to `scheduled` or `confirmed`, tells the customer, closes the screenshot escalation and, if nothing else is open, hands the chat back to the bot (with what was said meanwhile added to its memory). `reject` asks for a clearer screenshot and leaves the order waiting.

## Analytics (`app/analytics.py`)
Today's orders and revenue (cancelled excluded), open escalations and payment checks, top items, orders by area, and over the chosen window (default 7 days): conversations, **bot conversion rate** (chats that became a bot-made order), **escalation rate**, average and p90 **response time** of the bot, and **LLM requests and tokens per provider against the free-tier limits**.

Tests: `tests/test_admin.py` (19), run on SQLite and PostgreSQL.
