"""Re-score a saved stage report under strict and lenient matching — offline, no LLM.

The eval saves each prediction's SQL, so we can re-grade the exact same predictions
with the lenient column-subset rule without spending any API calls.

    python eval/rescore.py results/grounded.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from eval.harness import readonly_connection, score  # noqa: E402


def rescore(path: Path) -> dict:
    report = json.loads(path.read_text())
    conn = readonly_connection(config.DB_PATH)
    strict = lenient = 0
    flipped = []
    for rec in report["records"]:
        pred, gold = rec.get("pred_sql"), rec["gold_sql"]
        if not pred:
            continue
        s = score(conn, pred, gold, lenient=False)
        l = score(conn, pred, gold, lenient=True)
        strict += s.correct
        lenient += l.correct
        if l.correct and not s.correct:
            flipped.append((rec["id"], l.reason))
    n = report["total"]
    return {"stage": report["stage"], "total": n,
            "strict": strict, "strict_acc": round(strict / n, 4),
            "lenient": lenient, "lenient_acc": round(lenient / n, 4),
            "flipped": flipped}


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or [
        config.RESULTS_DIR / f"{s}.json" for s in ("baseline", "grounded", "self_correct")]
    for p in paths:
        if not p.exists():
            print(f"(skip {p}: not found)")
            continue
        r = rescore(p)
        print(f"{r['stage']:13} strict {r['strict_acc']:.1%} ({r['strict']}/{r['total']})"
              f"  ->  lenient {r['lenient_acc']:.1%} ({r['lenient']}/{r['total']})")
        if r["flipped"]:
            print("   newly-correct under lenient:",
                  ", ".join(f"{i}" for i, _ in r["flipped"]))


if __name__ == "__main__":
    main()
