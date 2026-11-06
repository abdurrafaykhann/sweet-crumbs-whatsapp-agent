"""Terminal chat with the agent, no WhatsApp needed.

    python -m app.chat_cli                       # as a new customer
    python -m app.chat_cli --phone 923000000101  # as Ayesha (demo customers: see data/sweet-crumbs-bakery-demo.md section 11)
    python -m app.chat_cli --debug               # also show which tools the bot used
    python -m app.chat_cli --now 2026-11-06T21:30:00

Seed the database first: python -m app.seed"""
import argparse
import os


def main():
    ap = argparse.ArgumentParser(description="Chat with the Sweet Crumbs agent in the terminal.")
    ap.add_argument("--phone", default="923000000108")
    ap.add_argument("--now", help="fixed clock, e.g. 2026-11-06T14:00:00 (default: DEMO_NOW from .env, else the real clock)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    if args.now:
        os.environ["DEMO_NOW"] = args.now
    from . import agent, clock, models as m
    from .database import get_engine
    m.Base.metadata.create_all(get_engine())
    print(f"Chatting as {args.phone}. Clock: {clock.now():%A %d %B %Y, %I:%M %p}. Type 'quit' to leave.\n")
    while True:
        try:
            text = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit", "q"):
            break
        if not text:
            continue
        r = agent.respond(args.phone, text)
        print(f"bot > {r.text}\n")
        if args.debug:
            for t in r.tools:
                print(f"      [tool] {t['name']} {t['args']}")
            print(f"      [usage] {r.usage}\n")
        if r.escalated:
            print("      [escalated: staff will take over]\n")


if __name__ == "__main__":
    main()
