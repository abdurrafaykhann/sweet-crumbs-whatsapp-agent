# Eval results: groq-originals (bot groq:openai/gpt-oss-120b, judge google_genai:gemini-flash-lite-latest)

Counted runs: 8 (not counted: 3 where the bot's model was unavailable, 0 where the judge's quota ran out; of those, 0 passed the deterministic checks). **Passed 6/8 (75%)**; deterministic checks 8/8 (100%); LLM judge 6/8 (75%). Average 27.8 s and 6292 tokens per conversation.

| Category | Passed |
|---|---|
| delivery | 2/3 (67%) |
| info | 1/1 (100%) |
| pricing | 2/3 (67%) |
| status | 1/1 (100%) |

| Wording | Passed |
|---|---|
| original | 6/8 (75%) |

## Failures

### Case 4 (original)

- judge: Suggested vegetable patty instead of the expected chicken patties or samosas.

```
customer: Pizza puff 5 chahiye
bot: Pizza puff aaj out of stock hai, lekin Vegetable Patty available hai Rs 110 each. 5 patty ka total Rs 550 hota hai, jo delivery minimum Rs 1,000 se kam hai. Aap pickup karna chahein ya koi aur item add karen? 😊
```

### Case 5 (original)

- judge: Mentioned Bahadurabad which was not in the tool result or expected behaviour.

```
customer: Deliver to Bahria Town?
bot: Sorry, we don’t deliver to Bahria Town. You can pick up from DHA Phase 6, Gulshan‑e‑Iqbal or Bahadurabad, or order via Foodpanda (prices a bit higher). Let me know what works for you!
```
