# Eval results: gemini-after-fix (bot google_genai:gemini-flash-lite-latest, judge groq:openai/gpt-oss-20b)

Counted runs: 19 (not counted: 3 where the bot's model was unavailable, 0 where the judge's quota ran out; of those, 0 passed the deterministic checks). **Passed 15/19 (79%)**; deterministic checks 19/19 (100%); LLM judge 15/19 (79%). Average 34.0 s and 14928 tokens per conversation.

| Category | Passed |
|---|---|
| handoff | 4/7 (57%) |
| ordering | 11/12 (92%) |

| Wording | Passed |
|---|---|
| original | 6/7 (86%) |
| polite | 3/4 (75%) |
| roman | 2/3 (67%) |
| rushed | 4/5 (80%) |

## Failures

### Case 16 (rushed)

- judge: Assistant used correct facts and tools, but responded in English instead of Roman Urdu, violating language criterion.

```
customer: frozen theme cake kal chahiye
bot: Aww, a Frozen theme cake sounds so magical! ❄️ However, custom theme cakes need 48 hours' notice, so we won't be able to craft it by tomorrow. Would you like to check out one of our ready-to-go birthday cakes instead?
```

### Case 17 (polite)

- judge: Assistant omitted the required estimate, violating the facts criterion.

```
customer: Hello, I'd like a theme cake for 15 November for about 20 guests please.
bot: That sounds lovely! For a custom theme cake, we need about 48 hours' notice, so 15 November gives us plenty of time. 

Could you share what theme or design you have in mind, your preferred flavor, and whether you'd like it delivered or picked up?
customer: Spiderman theme, chocolate, 2 tiers, delivery to DHA Phase 5, name Hira, pay by bank transfer
bot: Thank you for all those details! Since it's a 2-tier custom cake, I've passed your request along to our team with all the notes. Someone will review it and reply right here in the chat shortly to confirm the design and final price for you.
```

### Case 17 (roman)

- judge: Assistant didn't provide a price estimate, violating the expected facts requirement.

```
customer: 15 November ko theme cake chahiye, 20 log honge
bot: Theme cake ke liye 48 hours ka notice chahiye hota hai, toh 15 November ke liye aapka order araam se ho jayega! 🎂 

Aapko kis tarah ka theme ya design chahiye, aur flavour kon sa pasand hai?
customer: Spiderman theme, chocolate, 2 tiers, delivery to DHA Phase 5, name Hira, pay by bank transfer
bot: Maine ye sab details hamari team ko bhej di hain, aur hamara staff jald hi aapse is chat mein rabta karega! 😊
```

### Case 22 (original)

- judge: Assistant failed to apologize, request photo, and refer to care team; tone and details were missing.

```
customer: Cake crushed aaya
bot: Sorry, we are having a technical problem. Our team will reply to you here shortly.
```
