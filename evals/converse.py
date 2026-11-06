"""Print full transcripts of realistic conversations plus the resulting database state.
Usage: python -m evals.converse [scenario-name ...]   (uses the active brain: set GROQ_API_KEY / LLM_* in .env)"""
import os
import sys
import tempfile
import time

os.environ["AGENT_DB"] = os.path.join(tempfile.mkdtemp(), "convo.db")
from app import agent, db  # noqa: E402

SCENARIOS = {
    # birthday cake end to end, incl. payment: Rs 3000 + delivery -> cash allowed; shows payment question
    "birthday-en": ["Hi", "It's my daughter's birthday tomorrow and I need a cake", "she loves pineapple. we'll be around 12 people",
                    "write 'Happy 7th Birthday Zara' on it", "tomorrow 6pm, deliver to House 12, Street 4, Block 5 Gulshan-e-Iqbal. I'm Sana",
                    "cash on delivery is fine", "yes please confirm"],
    # big cake: 50% advance, payment details, proof, then 'where is my order'
    "big-cake-payment": ["I need a 3 lb chocolate fudge cake for my wedding anniversary, day after tomorrow at 7pm, pickup",
                         "write 'Happy Anniversary Ali & Sara'", "ok, yes please place it", "I've paid, TID 7766554433 on Easypaisa", "where is my order?"],
    "birthday-rur": ["Assalam o alaikum", "kal meri behen ki birthday hai, cake chahiye", "chocolate wala, 10-12 log honge",
                     "uspe 'Happy Birthday Hina' likhwana hai", "kal shaam 7 baje pickup, cash", "haan confirm kar do"],
    "ingredients-en": ["hi, what's in your chocolate fudge cake?", "does it have nuts? my son is allergic", "and how long does it stay fresh?"],
    "variations": ["I want some brownies", "what's the difference between them?", "2 nutella ones and 2 plain, pickup, cash"],
    "tracking": ["2 chicken patties and a white sandwich bread, pickup, cash", "yes confirm", "hey where is my order?"],
    "location-timing": ["where is your bakery?", "what are your timings?", "do you deliver to Clifton? how much is it?"],
    "change-details": ["1 lb pineapple cake for pickup tomorrow 6pm, cash, no message needed", "yes", "sorry, can I make it delivery instead? House 4, Gulshan-e-Iqbal block 3",
                       "actually can you change the time to 8pm", "also write 'Happy Birthday Ammi' on it", "thanks! and cancel nothing, that's all"],
    "payment-questions": ["can I pay by credit card?", "ok what are your payment options for a 3 lb cake?", "can you give me a discount for 3 cakes?"],
    "complaint": ["my cake arrived damaged and my guests were waiting. I want a refund"],
    "lead-time": ["I need a 3 lb red velvet cake in 3 hours", "ok what about tomorrow evening 8pm for pickup"],
}

names = sys.argv[1:] or list(SCENARIOS)
brain = agent.make_brain()
print(f"brain={brain.name} model={getattr(brain, 'model', '-')}\n")
for n in names:
    db.init(reset=True)
    print("=" * 70, f"\n{n}\n" + "=" * 70)
    for msg in SCENARIOS[n]:
        t0 = time.time()
        r = agent.handle_message(f"conv-{n}", msg, brain=brain)
        print(f"  customer: {msg}\n  Aisha   : {r}   [{(time.time() - t0):.1f}s]\n")
    c = db.conn()
    for o in c.execute("SELECT id,items,delivery,address,note,total,status,ready_at FROM orders"):
        print("  [DB order]", dict(o))
    for e in c.execute("SELECT reason FROM escalations"):
        print("  [DB handoff]", e["reason"][:100])
    print("  [DB customer]", db.get_customer(c, f"conv-{n}"), "\n")
    c.close()
