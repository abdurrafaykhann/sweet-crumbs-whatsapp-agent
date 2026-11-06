# Eval results: smoke (bot google_genai:gemini-flash-lite-latest, judge groq:openai/gpt-oss-120b)

Counted runs: 2 (not counted because the model was unavailable: 0). **Passed 1/2 (50%)**; deterministic checks 2/2 (100%); LLM judge 1/2 (50%). Average 8.8 s and 5496 tokens per conversation.

| Category | Passed |
|---|---|
| handoff | 0/1 (0%) |
| pricing | 1/1 (100%) |

| Wording | Passed |
|---|---|
| original | 1/2 (50%) |

## Failures

### Case 23 (original)

- judge: Assistant mixed English with Roman Urdu despite customer using only Roman Urdu, violating language criterion.

```
customer: Manager se baat karao
bot: Bilkul, I'm connecting you with our manager right now. Someone from our team will reply here shortly!
```
