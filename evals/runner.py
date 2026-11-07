"""Run the eval suite.

    python -m evals.runner --list
    python -m evals.runner --category delivery --rewordings 2
    python -m evals.runner --case 2 --case 9
    python -m evals.runner --model google_genai:gemini-flash-lite-latest --judge groq:openai/gpt-oss-120b --delay 5 --tag gemini

Passed variants are cached (keyed by case, wording, bot model, judge model and a hash of the app code), so a re-run only repeats
failures and new variants. Runs where the model was unavailable are not counted as failures and are not cached."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import time
from collections import defaultdict

from . import harness
from .cases import CASES, CATEGORIES, Case
from .judge import judge as grade

ROOT = pathlib.Path(__file__).resolve().parent
RESULTS = ROOT / "results"
STORE = harness.CACHE / "results"


def code_version() -> str:
    h = hashlib.sha1()
    for p in sorted((ROOT.parent / "app").glob("*.py")) + [ROOT.parent / "data" / "sweet-crumbs-bakery-demo.md"]:
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def phone_for(case: Case, n: int) -> str:
    return f"92300990{n:04d}" if case.who == "new" else f"923000000{case.who}"


class QuotaExhausted(RuntimeError):
    """The provider asks for a wait longer than we are willing to sit through (usually a daily quota). Resume later: the cache keeps progress."""


class Paced:
    """Keeps a minimum gap between model calls (free-tier requests per minute) and, when every provider says
    "try again in N seconds", sleeps exactly that long instead of failing, as long as N is within `max_wait`."""

    def __init__(self, inner, gap: float, max_wait: float = 900.0, sleep=time.sleep, clock=time.time):
        self.inner, self.gap, self.max_wait, self.sleep, self.clock, self.last = inner, gap, max_wait, sleep, clock, 0.0

    def bind_tools(self, tools):
        self.inner.bind_tools(tools)
        return self

    def invoke(self, messages, config=None):
        from app.llm import LLMUnavailable
        while True:
            wait = self.last + self.gap - self.clock()
            if wait > 0:
                self.sleep(wait)
            try:
                return self.inner.invoke(messages, config)
            except LLMUnavailable as e:
                pause = (e.retry_after or 60) + 1
                if pause > self.max_wait:
                    raise QuotaExhausted(f"provider asks for {int(pause)} s, more than --max-wait {int(self.max_wait)} s") from e
                print(f"  rate limited: waiting {int(pause)} s as the provider asked", flush=True)
                self.sleep(pause)
            finally:
                self.last = self.clock()


SMALL_JUDGES = {"groq": "groq:openai/gpt-oss-20b", "google_genai": "google_genai:gemini-flash-lite-latest"}


def default_judge(bot: str | None) -> str:
    """The judge is a different provider from the bot, and a small model: the big model's quota is kept for the bot."""
    if os.environ.get("LLM_JUDGE"):
        return os.environ["LLM_JUDGE"]
    bot_provider = (bot or os.environ.get("LLM_PRIMARY") or "groq:").partition(":")[0]
    return SMALL_JUDGES["google_genai"] if bot_provider == "groq" else SMALL_JUDGES["groq"]


def variants_of(case: Case, rewordings: int) -> list[tuple[str, list[str]]]:
    return [("original", case.turns)] + case.variants[:rewordings]


def key_of(case: Case, style: str, turns: list[str], bot: str, judge: str) -> str:
    return hashlib.sha1(json.dumps([case.id, style, turns, bot, judge, case.now, code_version()], ensure_ascii=False).encode()).hexdigest()[:16]


def run_variant(case: Case, style: str, turns: list[str], n: int, bot_graph, judge_model, bot: str, judge_name: str) -> dict:
    harness.fresh_db()
    run = harness.run_script(phone_for(case, n), turns, bot_graph, case.now or harness.DEFAULT_NOW)
    rec = {"case": case.id, "category": case.category, "style": style, "turns": [vars(t) for t in run.turns], "bot": bot, "judge_model": judge_name,
           "seconds": round(sum(t.seconds for t in run.turns), 1), "tokens": sum(t.tokens for t in run.turns), "provider": next((t.provider for t in run.turns if t.provider), "")}
    if run.error:
        rec.update(status="error", hard_failures=[], judge={}, passed=False, note=run.error)
        return rec
    fails = [msg for chk in case.checks if (msg := chk(run))]
    try:
        verdict = grade(judge_model, case, run)
        judged = True
    except Exception as exc:  # judge quota or bad JSON: the hard checks still count, the judge result is missing
        verdict, judged = {"pass": None, "reason": f"judge failed: {str(exc)[:100]}"}, False
    rec.update(status="ok" if judged else "unjudged", hard_failures=fails, judge=verdict, judged=judged, passed=judged and not fails and bool(verdict["pass"]))
    return rec


def summarise(records: list[dict]) -> dict:
    ok = [r for r in records if r["status"] == "ok"]
    unjudged = [r for r in records if r["status"] == "unjudged"]
    by = lambda f: {k: (sum(r["passed"] for r in v), len(v)) for k, v in sorted(_group(ok, f).items())}
    return {
        "variants_run": len(records), "errors": len(records) - len(ok) - len(unjudged), "unjudged": len(unjudged),
        "unjudged_hard_pass": sum(not r["hard_failures"] for r in unjudged), "passed": sum(r["passed"] for r in ok), "counted": len(ok),
        "hard_check_pass": sum(not r["hard_failures"] for r in ok), "judge_pass": sum(bool(r["judge"].get("pass")) for r in ok),
        "by_category": by(lambda r: r["category"]), "by_style": by(lambda r: r["style"]),
        "avg_seconds": round(sum(r["seconds"] for r in ok) / len(ok), 1) if ok else 0, "avg_tokens": round(sum(r["tokens"] for r in ok) / len(ok)) if ok else 0,
    }


def _group(items, f):
    g = defaultdict(list)
    for r in items:
        g[f(r)].append(r)
    return g


def markdown(summary: dict, records: list[dict], title: str) -> str:
    rate = lambda a, b: f"{a}/{b} ({round(100 * a / b)}%)" if b else "n/a"
    lines = [f"# {title}", "",
             f"Counted runs: {summary['counted']} (not counted: {summary['errors']} where the bot's model was unavailable, {summary['unjudged']} where the judge's quota ran out; of those, {summary['unjudged_hard_pass']} passed the deterministic checks). "
             f"**Passed {rate(summary['passed'], summary['counted'])}**; deterministic checks {rate(summary['hard_check_pass'], summary['counted'])}; "
             f"LLM judge {rate(summary['judge_pass'], summary['counted'])}. Average {summary['avg_seconds']} s and {summary['avg_tokens']} tokens per conversation.", "",
             "| Category | Passed |", "|---|---|", *[f"| {k} | {rate(*v)} |" for k, v in summary["by_category"].items()], "",
             "| Wording | Passed |", "|---|---|", *[f"| {k} | {rate(*v)} |" for k, v in summary["by_style"].items()], "", "## Failures", ""]
    bad = [r for r in records if r["status"] == "ok" and not r["passed"]]
    if not bad:
        lines.append("None.")
    for r in bad:
        lines += [f"### Case {r['case']} ({r['style']})", "", *[f"- check: {f}" for f in r["hard_failures"]], f"- judge: {r['judge'].get('reason', '')}", "", "```"]
        for t in r["turns"]:
            lines += [f"customer: {t['user']}", f"bot: {t['reply']}"]
        lines += ["```", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", action="append", choices=CATEGORIES)
    ap.add_argument("--case", action="append")
    ap.add_argument("--rewordings", type=int, default=0, help="how many rewordings per case to run besides the original (up to 6)")
    ap.add_argument("--model", help="bot model, e.g. groq:openai/gpt-oss-120b (default: LLM_PRIMARY with LLM_FALLBACK)")
    ap.add_argument("--judge", help="judge model; default: a small model from the other provider (each model has its own quota)")
    ap.add_argument("--max-wait", type=float, default=900.0, help="longest pause (seconds) to sit through when a provider says try again in N seconds")
    ap.add_argument("--stop-after-errors", type=int, default=3, help="stop when this many runs in a row fail because a model is unavailable; re-run later to resume from the cache")
    ap.add_argument("--delay", type=float, default=0.0, help="minimum seconds between bot model calls")
    ap.add_argument("--judge-delay", type=float, default=0.0)
    ap.add_argument("--force", action="store_true", help="ignore cached passes")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    cases = [c for c in CASES if (not args.category or c.category in args.category) and (not args.case or c.id in args.case)]
    if args.list:
        for c in cases:
            print(f"{c.id:>3} {c.category:<12} {len(c.turns)} turn(s), {1 + len(c.variants)} wordings  {c.turns[0][:60]}")
        return

    from app import agent
    from app.llm import get_model
    from langgraph.checkpoint.memory import InMemorySaver
    bot_name = args.model or f"{os.environ.get('LLM_PRIMARY', 'default')}+fallback"
    STORE.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    args.judge = args.judge or default_judge(args.model)
    bot_model = Paced(get_model(args.model), args.delay, args.max_wait)
    judge_model = Paced(get_model(args.judge), args.judge_delay, args.max_wait)
    streak = 0
    records, n = [], 0
    for case in cases:
        if streak >= args.stop_after_errors:
            break
        for style, turns in variants_of(case, args.rewordings):
            n += 1
            f = STORE / f"{key_of(case, style, turns, bot_name, args.judge)}.json"
            if f.exists() and not args.force:
                rec = json.loads(f.read_text(encoding="utf-8"))
                if rec["passed"]:
                    records.append(rec)
                    print(f"case {case.id:>3} {style:<9} cached pass")
                    continue
            graph = agent.build_graph(bot_model, InMemorySaver())
            rec = run_variant(case, style, turns, n, graph, judge_model, bot_name, args.judge)
            records.append(rec)
            streak = streak + 1 if rec["status"] == "error" else 0
            if streak >= args.stop_after_errors:
                break
            if rec["status"] == "ok":
                f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            mark = "PASS" if rec["passed"] else ("ERROR" if rec["status"] == "error" else "FAIL")
            print(f"case {case.id:>3} {style:<9} {mark}  {rec['seconds']}s  {'; '.join(rec['hard_failures'])[:90]} {rec['judge'].get('reason', '')[:70] if not rec['passed'] else ''}", flush=True)
    if streak >= args.stop_after_errors:
        print(f"stopped: {streak} runs in a row failed because a model is unavailable (quota). Re-run the same command later; passed runs are cached.")
    summary = summarise(records)
    out = RESULTS / f"{args.tag}"
    out.with_suffix(".json").write_text(json.dumps({"bot": bot_name, "judge": args.judge, "summary": summary, "records": records}, ensure_ascii=False, indent=1), encoding="utf-8")
    out.with_suffix(".md").write_text(markdown(summary, records, f"Eval results: {args.tag} (bot {bot_name}, judge {args.judge})"), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("passed", "counted", "errors", "hard_check_pass", "judge_pass")}))


if __name__ == "__main__":
    main()
