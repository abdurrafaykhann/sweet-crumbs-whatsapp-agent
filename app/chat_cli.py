"""Terminal chat with the agent, no WhatsApp needed.

    python -m app.chat_cli                       # as a new customer
    python -m app.chat_cli --phone 923000000101  # as Ayesha (demo customers: see data/sweet-crumbs-bakery-demo.md section 11)
    python -m app.chat_cli --debug               # also show which tools the bot used
    python -m app.chat_cli --now 2026-11-06T21:30:00

Commands inside the chat:  /image  send a payment screenshot   /reset  forget this chat   /state  show the order in progress   /quit

Seed the database first: python -m app.seed"""
import argparse
import os


def command(cmd: str, phone: str) -> None:
    from . import devtools, handoff
    if cmd == "/image":
        print(f"bot > {handoff.handle_media(phone, 'image', 'cli-upload') or '(staff have this chat: no automatic reply)'}\n")
    elif cmd == "/reset":
        devtools.reset_conversation(phone)
        print("(chat reset: draft, messages and memory forgotten; orders are kept)\n")
    elif cmd == "/state":
        import json
        print(json.dumps(devtools.chat_state(phone), indent=2, ensure_ascii=False), "\n")
    else:
        print("commands: /image  /reset  /state  /quit\n")


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
        if text.lower() in ("quit", "exit", "q", "/quit"):
            break
        if not text:
            continue
        if text.startswith("/"):
            command(text.lower(), args.phone)
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
