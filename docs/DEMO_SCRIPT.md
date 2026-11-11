# Demo video script (75-90 seconds)

Live site: https://sweet-crumbs-indol.vercel.app  (chat `/ui`, owner dashboard `/admin/`).
WhatsApp itself is not used on screen (Meta locked the free test account), so the web chat tester stands in for it: it runs the same agent, the same tools and the same database.

## Before you press record
1. Warm the site up: open `/health` once. The first request after a quiet period takes 20-30 s.
2. Open two browser tabs: `/ui` (chat) and `/admin/` already logged in with the **owner** password (the demo password is read-only and cannot move orders).
3. In `/ui` choose **New customer** and type a fresh phone number, e.g. `923001112233`, so the chat starts empty.
4. Do one full dry run. Models vary a little; record the take where the order is placed.
5. Browser at 100% zoom, bookmarks bar hidden, notifications off, dashboard sound ON (the new-order chime is part of the demo).

## Shots
| Time | Screen | You do | Say (voice-over) |
|---|---|---|---|
| 0:00-0:08 | `/ui` chat, empty | Type **Assalam o alaikum** | "This is Sweet Crumbs, a bakery in Karachi, and this is its AI assistant. It speaks English, Roman Urdu and Urdu." |
| 0:08-0:20 | chat | Type **Chocolate cake 2 pound kitne ka hai** | "It looks the price up in the menu database instead of guessing: Rs 3,200, answered in Roman Urdu." |
| 0:20-0:50 | chat | Type: **6 chicken patties pickup chahiye Gulshan branch se, abhi. Naam Hira, cash dungi** then, when it shows the summary, **yes** | "It collects what it needs, shows an order summary, and only places the order after I say yes. That rule is enforced in code, not left to the model." (cut the waiting time in the edit) |
| 0:50-0:58 | chat, order code visible | Pause on the confirmation (code like SC-10433) | "Order placed." |
| 0:58-1:10 | `/admin/` Orders | Switch tab: the new order is on top, a toast and chime appear. Click it, press **Start preparing**, then **Ready for pickup** | "On the owner's dashboard the order appears live. One tap moves it forward, and the customer is told in their own language." |
| 1:10-1:18 | `/ui` chat | Type **Manager se baat karao**. Switch to `/admin/`, open **Needs you** | "If a customer is upset or asks for a person, the bot hands over, with everything it already collected, and goes silent until staff hand the chat back." |
| 1:18-1:28 | `/admin/` Analytics | Scroll once | "Analytics show orders, bot conversion, and model usage against the free-tier limits. Everything runs free: Vercel, Supabase, Groq with Gemini as fallback." |
| end card | README or repo | Hold 3 s | "Code, evals and architecture are on GitHub." |

## Voice-over tips
- Speak slowly; leave a beat while the bot is typing, then speed that part up 2x when editing.
- Say plainly what is not on screen: "The same engine answers on WhatsApp through Meta's Cloud API; here I'm using the built-in tester."
- Mention one number: "35 test scenarios, each reworded five ways, 100% of the fact checks passing on Gemini." Only say what the README says.

## After recording
Upload as YouTube **Unlisted** (or Loom), then add the link under "Live demo" in the README and to the portfolio checklist.
