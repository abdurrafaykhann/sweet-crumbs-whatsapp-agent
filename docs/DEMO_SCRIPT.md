# Demo video script (about 90 seconds)

Goal: show that this is an agent that **takes real actions**, in the language the customer uses, and knows when to hand off.

## Setup (once)
1. Start the app with the real model: put `GROQ_API_KEY` in `.env`, then
   ```bash
   DEMO_MODE=1 uvicorn app.main:app --port 8000
   ```
2. Open `http://localhost:8000/demo`, click **Reset demo** so stock and orders start clean.
3. Record with **Win + G** (Xbox Game Bar > Capture) or OBS. Browser window about 1100 px wide so the chat and the "Behind the scenes" panel are both visible. Close other tabs and notifications. Record voice-over after, over the screen capture.
4. Groq's free tier is slow on bursts: pause 3 to 5 seconds between messages and cut the waits in editing. Do one dry run first.

## Shot list (type these; they are also the chips under the chat)
| Time | You type | What to point out (voice-over) |
|---|---|---|
| 0:00 | (screen) | "A WhatsApp assistant for a small bakery. It speaks English, Roman Urdu and Urdu." |
| 0:08 | `Assalam o alaikum` | Greets by time of day, answers in kind. |
| 0:15 | `kal meri behen ki birthday hai, ek chocolate cake aur 6 cupcakes chahiye` | Understands the occasion, handles two items in one message. |
| 0:30 | `cake pe "Happy Birthday Hina" likhwa dein. delivery, House 12 Gulshan` | Takes a note and an address; reads the total back with the delivery fee and asks for confirmation. |
| 0:45 | `haan confirm` | Places the order. Point to the right panel: order appears, **stock drops**. |
| 0:55 | `actually can I cancel it?` | Switches to English mid-chat; cancels; stock returns. |
| 1:05 | `کیا کوکیز ہیں؟` | Urdu script; honest "out of stock", not an invention. |
| 1:12 | `mujhe refund chahiye` | Empathy first, then hands off; point to "Needs a human". |
| 1:22 | (screen: dashboard `/`) | Orders, revenue, handoffs, latency, cost per conversation. |
| 1:30 | (screen) | "Postgres, Redis, tool-calling LLM, and 60 automated test conversations." |

## Say honestly in the description
- It is a demo shop with demo stock; the model is a free-tier LLM.
- Add your measured numbers from `python -m evals.run_evals --brain llm` (state pass rate, latency, cost per conversation), not the rule-baseline numbers.
- Say whether it ran on a real WhatsApp number or on the web simulator.

## Post
LinkedIn: 3 lines on the problem (shops answer the same questions all day), what the agent does, and one number from your eval. Link the repo and the video.
