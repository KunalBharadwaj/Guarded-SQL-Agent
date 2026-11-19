"""Command-line entrypoint for the Guarded NL-to-Action Agent.

Ask a question:
    python cli.py "how many orders were cancelled?"

Confirm a gated mutation (destructive ops require this):
    python cli.py --confirm "delete all returned orders"

Show the audit trail:
    python cli.py --audit
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import audit  # noqa: E402
from agent.orchestrator import Agent, ResponseStatus  # noqa: E402


def _print_response(r) -> None:
    icon = {
        ResponseStatus.ANSWERED: "✓",
        ResponseStatus.NEEDS_CONFIRMATION: "⏸",
        ResponseStatus.ESCALATED: "🙋",
        ResponseStatus.BLOCKED: "⛔",
        ResponseStatus.ERROR: "✗",
    }[r.status]
    print(f"\n[{icon} {r.status.value}] {r.message}")
    if r.sql:
        print(f"\n  SQL: {r.sql}")
    if r.confidence is not None:
        print(f"  confidence: {r.confidence}  attempts: {r.attempts}")
    if r.rows is not None:
        print(f"\n  {len(r.rows)} row(s):")
        for row in r.rows[:20]:
            print("   ", row)
        if len(r.rows) > 20:
            print(f"    … and {len(r.rows) - 20} more")


def main() -> None:
    ap = argparse.ArgumentParser(description="Guarded NL-to-Action Agent")
    ap.add_argument("question", nargs="?", help="natural-language request")
    ap.add_argument("--confirm", action="store_true",
                    help="confirm a gated mutation (required for writes/destructive ops)")
    ap.add_argument("--decompose", action="store_true",
                    help="plan a complex question into ordered sub-questions and solve step by step")
    ap.add_argument("--audit", action="store_true", help="print the audit log and exit")
    args = ap.parse_args()

    if args.audit:
        for e in audit.read_all():
            print(f"{e['ts']}  {e['status']:<18} {e.get('action_class') or '-':<12} "
                  f"{(e.get('sql') or e['question'])[:70]}")
        return

    if not args.question:
        ap.error("provide a question, or use --audit")

    agent = Agent()
    if args.decompose:
        _print_multistep(agent.solve(args.question))
    else:
        _print_response(agent.ask(args.question, confirmed=args.confirm))


def _print_multistep(res) -> None:
    tag = "decomposed into" if res.decomposed else "answered in"
    print(f"\n[{'✓' if res.success else '✗'}] {tag} {len(res.plan)} step(s):")
    for st in res.steps:
        mark = "✓" if st.ok else "✗"
        print(f"\n  {mark} step {st.index}: {st.question}")
        print(f"      SQL: {st.sql}")
        print(f"      {st.summary}")
    if res.final_rows is not None:
        print(f"\n  Final answer ({len(res.final_rows)} row(s)):")
        for row in res.final_rows[:20]:
            print("   ", row)


if __name__ == "__main__":
    main()
