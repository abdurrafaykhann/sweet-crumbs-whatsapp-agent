# Eval results: groq-originals (bot groq:openai/gpt-oss-120b, judge google_genai:gemini-flash-lite-latest)

Counted runs: 35 (not counted: 3 where the bot's model was unavailable, 0 where the judge's quota ran out; of those, 0 passed the deterministic checks). **Passed 28/35 (80%)**; deterministic checks 32/35 (91%); LLM judge 30/35 (86%). Average 163.1 s and 7251 tokens per conversation.

| Category | Passed |
|---|---|
| delivery | 5/6 (83%) |
| handoff | 4/5 (80%) |
| info | 3/4 (75%) |
| language | 1/1 (100%) |
| ordering | 4/6 (67%) |
| pricing | 2/3 (67%) |
| safety | 5/5 (100%) |
| status | 4/5 (80%) |

| Wording | Passed |
|---|---|
| original | 28/35 (80%) |

## Failures

### Case 2 (original)

- judge: Price is incorrect according to expected behaviour (should be Rs 3,200 for 2 lbs based on menu, but assistant hallucinated bundle/extra offers not in the expected behaviour).

```
customer: Chocolate cake 2 pound kitne ka hai
bot: 2 lb Chocolate Fudge cake Rs 3,200 hai. 🎂 Agar Birthday Bundle lete hain to cake + 6 cupcakes + candles sirf Rs 4,700, jo Rs 400 bachata hai. Bataiye?
```

### Case 8 (original)

- check: reply should contain (1000,) but says: 'Hi! Welcome to Sweet Crumbs Bakery 😊 How can I help you today?'
- judge: Assistant failed to address the customer's delivery request for 5 chicken patties and the delivery minimum, giving only a generic welcome.

```
customer: 5 chicken patties deliver kar do DHA phase 5
bot: Hi! Welcome to Sweet Crumbs Bakery 😊 How can I help you today?
```

### Case 9 (original)

- check: expected one of the tools ('order_status',), used none
- judge: Assistant correctly identified the customer's active orders in matching Roman Urdu and asked which one they meant.

```
customer: Mera order kahan hai
bot: Ayesha, aap ke do active orders hain: SC-10421 jo abhi delivery pe hai, aur SC-10431 jo schedule hai. Kaun sa order ka status chahte hain? 😊
```

### Case 17 (original)

- check: expected a hand-off to staff, none was created
- judge: Collected necessary design details, checked delivery, provided estimate/questions, escalated appropriately without confirming order.

```
customer: Theme cake for 15 Nov, 20 guests
bot: Hi! For a custom theme cake on 15 Nov we need 48 h notice. Please tell me the flavour you’d like, the size (e.g., 2 lb or 3 lb for 20 guests), and if you want delivery (share address) or pickup. Also, any short message (up to 30 chars) on the cake?
customer: Spiderman theme, chocolate, 2 tiers, delivery to DHA Phase 5, name Hira, pay by bank transfer
bot: Great, Hira! For a Spiderman chocolate theme cake with 2 tiers, could you let me know the weight you’d like (e.g., 3 lb or 4 lb – that’s perfect for 20 guests)? Also, any short message (up to 30 chars) on the cake? And what time on 15 Nov would you prefer the delivery? Once I have those, I’ll set up the draft and share the payment details for bank transfer.
```

### Case 18 (original)

- judge: Failed to state that eggless cakes need 24 hours' notice and offered it for today instead of tomorrow.

```
customer: Eggless chocolate cake today
bot: Sure! Which size (e.g., 2 lb or 3 lb) and how many would you like? Delivery or pickup, and any message on the cake? 😊
```

### Case 19 (original)

- judge: Hot tea is not delivered, but assistant tried to take the order instead of offering cold coffee or mint margarita.

```
customer: Chai delivery
bot: Sure! Chai delivery needs a minimum order of Rs 1,000. What quantity and your area? 😊 (We can add some biscuits for just Rs 150 extra.)
```

### Case 30 (original)

- judge: Expected behaviour required accepting the order at 1:30 pm on Friday and mentioning delivery after 2:30 pm due to Jummah, which the assistant did not do.

```
customer: I need 8 chicken patties delivered to House 12, Block 7, Gulshan-e-Iqbal today. Name Hira, cash on delivery.
bot: Sure, Hira! When would you like the delivery – ASAP today or a specific time slot? (We can schedule between 10 am‑11 pm.)
customer: yes
bot: Please check your order:

- 8 x Chicken Patty – Rs 1,120  
Subtotal: Rs 1,120  
Delivery (Zone B): Rs 200  

*Total: Rs 1,320* – Cash on delivery  
Address: House 12, Block 7, Gulshan‑e‑Iqbal  
When: ASAP, around 3:45 PM  

Reply *YES* to confirm or let me know any changes.
customer: yes please confirm
bot: Your order *SC‑10432* is confirmed! 🎉 Rider will deliver around 3:45 PM. Please pay Rs 1,320 in cash on delivery. Thank you!
```
