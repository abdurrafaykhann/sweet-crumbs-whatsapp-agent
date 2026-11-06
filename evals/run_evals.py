"""Run the eval set against the active brain (Claude if ANTHROPIC_API_KEY is set, else the rule baseline).
Usage: python -m evals.run_evals [--brain rule|claude] [--json out.json]
Each case runs on a fresh database. Reports pass rate, avg latency per message and (Claude) token cost."""
import argparse
import json
import os
import sys
import tempfile
import time

ap = argparse.ArgumentParser()
ap.add_argument("--brain", choices=["rule", "claude", "llm"], default=None)
ap.add_argument("--json")
ap.add_argument("--suite", choices=["basic", "all"], default=None)
ap.add_argument("--only", help="comma separated case names")
args = ap.parse_args()
if args.brain:
    os.environ["AGENT_BRAIN"] = args.brain

os.environ["AGENT_DB"] = os.path.join(tempfile.mkdtemp(), "eval.db")

from app import agent, db  # noqa: E402
from evals.cases import CASES  # noqa: E402

brain = agent.make_brain()


def run_case(name, turns, expect):
    db.init(reset=True)
    fails, lat, wording = [], [], []
    for i, turn in enumerate(turns):
        msg, must = turn[0], turn[1]
        forbidden = turn[2] if len(turn) > 2 else None
        t0 = time.time()
        reply = agent.handle_message(f"eval-{name}", msg, brain=brain)
        lat.append((time.time() - t0) * 1000)
        if must and not any(m.lower() in reply.lower() for m in must):
            wording.append(f"turn {i + 1} {msg!r}: reply {reply!r} lacks any of {must}")
        if forbidden and any(f.lower() in reply.lower() for f in forbidden):
            wording.append(f"turn {i + 1} {msg!r}: reply {reply!r} contains forbidden {forbidden}")
    c = db.conn()
    if expect:
        one = lambda q, p=(): c.execute(q, p).fetchone()[0]
        got = {"orders": one("SELECT COUNT(*) FROM orders"),
               "escalations": one("SELECT COUNT(*) FROM escalations"),
               "cancelled": one("SELECT COUNT(*) FROM orders WHERE status='cancelled'")}
        for k in ("orders", "escalations", "cancelled"):
            if k in expect and got[k] != expect[k]:
                fails.append(f"{k}: expected {expect[k]}, got {got[k]}")
        for prod, left in expect.get("stock", {}).items():
            row = c.execute("SELECT v.stock FROM variations v JOIN products p ON p.id=v.product_id WHERE p.name || ' - ' || v.name = ?", (prod,)).fetchone()
            if row is None or row[0] != left:
                fails.append(f"stock {prod}: expected {left}, got {None if row is None else row[0]}")
        if "status" in expect:
            row = c.execute("SELECT status FROM orders ORDER BY id DESC LIMIT 1").fetchone()
            if row is None or row[0] != expect["status"]:
                fails.append(f"latest order status: expected {expect['status']}, got {None if row is None else row[0]}")
        if "address_contains" in expect:
            addrs = " ".join((x["address"] or "") for x in c.execute("SELECT address FROM orders"))
            if expect["address_contains"].lower() not in addrs.lower():
                fails.append(f"order address should contain {expect['address_contains']!r}, got {addrs!r}")
        if "note_contains" in expect:
            notes = " ".join((r["note"] or "") for r in c.execute("SELECT note FROM orders"))
            if expect["note_contains"].lower() not in notes.lower():
                fails.append(f"order note should contain {expect['note_contains']!r}, notes: {notes!r}")
    c.close()
    return fails, wording, lat


results, all_lat = [], []
suite = args.suite or ("basic" if brain.name == "rule" else "all")
only = set(args.only.split(",")) if args.only else None
for name, turns, expect, kind in CASES:
    if (suite == "basic" and kind != "basic") or (only and name not in only):
        continue
    f, w, lat = run_case(name, turns, expect)
    all_lat += lat
    results.append({"case": name, "state_pass": not f, "wording_pass": not w, "pass": not f and not w, "state_failures": f,
                    "wording_failures": w})
    print(("PASS " if not f and not w else "FAIL " if f else "WORD ") + name)
    for x in f + w:
        print("     -", x)

passed = sum(r["pass"] for r in results)
state_ok = sum(r["state_pass"] for r in results)
summary = {"brain": brain.name, "model": getattr(brain, "model", "n/a"), "cases": len(results), "passed_strict": passed,
           "strict_pass_rate": round(passed / len(results) * 100, 1),
           "state_pass_rate": round(state_ok / len(results) * 100, 1),
           "_note": "state = orders/handoffs/stock in DB correct; strict also requires expected wording",
           "avg_latency_ms_per_message": round(sum(all_lat) / len(all_lat), 1),
           "tokens_in": getattr(brain, "tokens_in", 0), "tokens_out": getattr(brain, "tokens_out", 0)}
print("\n" + json.dumps(summary, indent=2))
if args.json:
    json.dump({"summary": summary, "results": results}, open(args.json, "w"), indent=2, ensure_ascii=False)
sys.exit(0 if state_ok == len(results) else 1)
