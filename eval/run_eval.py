"""Run an evaluation stage and write a report to results/.

Stages (accuracy lift is visible across them):
  baseline      prompt-only NL->SQL, table names only (Phase 0)
  grounded      schema-grounded retrieval                         (Phase 1)
  self_correct  grounded + execute-fix loop                       (Phase 2)

Providers:
  (default)     real LLM chosen by key (anthropic/openai)
  oracle        use the gold SQL as the prediction — proves the harness scores a
                perfect run at 100% without needing an API key
  stub          always emit "SELECT 1" — a plumbing smoke test

Usage:
  python eval/run_eval.py --stage baseline
  python eval/run_eval.py --stage baseline --provider oracle
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from agent import schema  # noqa: E402
from eval.harness import readonly_connection, score  # noqa: E402


def load_gold() -> list[dict]:
    items = []
    for line in config.GOLD_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def _predict_fn(stage: str, provider: str):
    """Return a callable question -> predicted_sql for the chosen stage/provider."""
    if provider == "stub":
        return lambda q, gold: "SELECT 1"
    if provider == "oracle":
        return lambda q, gold: gold

    # Real LLM path.
    from agent.generator import generate_sql
    from agent.llm import get_client

    client = get_client()

    if stage == "baseline":
        ctx = schema.baseline_context(config.DB_PATH)
        return lambda q, gold: generate_sql(client, q, ctx).sql

    if stage in ("grounded", "self_correct"):
        # Phase 1/2 wiring: import lazily so Phase 0 runs without those modules.
        from agent.retriever import SchemaRetriever

        retriever = SchemaRetriever()
        if stage == "grounded":
            return lambda q, gold: generate_sql(client, q, retriever.context_for(q)).sql
        from agent.self_correction import correct_loop
        return lambda q, gold: correct_loop(client, q, retriever).sql

    raise SystemExit(f"unknown stage {stage!r}")


def resolve_provider_name(provider: str, client_factory=None) -> str:
    """Map the CLI's 'auto' to the concrete backend that will actually run.

    'oracle' and 'stub' are passed through; anything else means the real LLM path,
    so we report the resolved backend (e.g. 'groq') instead of the literal 'auto',
    which otherwise makes every saved report ambiguous about which model produced it.
    """
    if provider in ("oracle", "stub"):
        return provider
    if client_factory is None:
        from agent.llm import get_client
        client_factory = get_client
    return client_factory().name


def run(stage: str, provider: str, lenient: bool = False) -> dict:
    gold = load_gold()
    predict = _predict_fn(stage, provider)
    conn = readonly_connection(config.DB_PATH)

    records, by_diff = [], defaultdict(lambda: [0, 0])  # difficulty -> [correct, total]
    correct = 0
    t0 = time.time()
    for item in gold:
        try:
            pred_sql = predict(item["question"], item["gold_sql"])
        except Exception as e:  # noqa: BLE001 — a generation failure counts as wrong
            pred_sql = ""
            gen_error = f"{type(e).__name__}: {e}"
        else:
            gen_error = None

        res = score(conn, pred_sql, item["gold_sql"], lenient=lenient) if pred_sql else None
        ok = bool(res and res.correct)
        correct += ok
        d = item.get("difficulty", "?")
        by_diff[d][0] += ok
        by_diff[d][1] += 1
        records.append({
            "id": item["id"], "difficulty": d, "question": item["question"],
            "pred_sql": pred_sql, "gold_sql": item["gold_sql"],
            "correct": ok,
            "reason": gen_error or (res.reason if res else "no_prediction"),
            "pred_error": res.pred_error if res else gen_error,
        })

    total = len(gold)
    report = {
        "stage": stage,
        "provider": provider,
        "scoring": "lenient" if lenient else "strict",
        "accuracy": round(correct / total, 4) if total else 0.0,
        "correct": correct,
        "total": total,
        "by_difficulty": {
            d: {"accuracy": round(c / n, 4), "correct": c, "total": n}
            for d, (c, n) in sorted(by_diff.items())
        },
        "elapsed_sec": round(time.time() - t0, 2),
        "records": records,
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="baseline",
                    choices=["baseline", "grounded", "self_correct"])
    ap.add_argument("--provider", default="auto",
                    help="auto (real LLM) | oracle | stub")
    ap.add_argument("--out", default=None, help="output JSON path")
    ap.add_argument("--lenient", action="store_true",
                    help="accept extra-column projections (gold columns ⊆ predicted columns)")
    args = ap.parse_args()

    provider = resolve_provider_name(args.provider)
    report = run(args.stage, provider, lenient=args.lenient)

    out = Path(args.out) if args.out else config.RESULTS_DIR / f"{args.stage}.json"
    out.write_text(json.dumps(report, indent=2))

    print(f"stage={report['stage']} provider={report['provider']} "
          f"accuracy={report['accuracy']:.1%} ({report['correct']}/{report['total']})")
    for d, s in report["by_difficulty"].items():
        print(f"  {d:<8} {s['accuracy']:.1%} ({s['correct']}/{s['total']})")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
