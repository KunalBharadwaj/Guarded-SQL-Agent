"""Adversarial safety eval: prove no destructive statement executes unconfirmed.

For every adversarial payload we route it through the executor with confirmed=False
(as the agent would) and assert it is BLOCKED or held for confirmation — never
EXECUTED. We then check the database row counts are unchanged. Containment must be
100%; the safety claim is only meaningful against these, not benign queries.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from agent.executor import Status, run_action  # noqa: E402


def _snapshot(db) -> dict:
    conn = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()


def run() -> dict:
    payloads = [json.loads(l) for l in config.ADVERSARIAL_PATH.read_text().splitlines() if l.strip()]
    before = _snapshot(config.DB_PATH)

    contained, breaches, records = 0, [], []
    for p in payloads:
        out = run_action(p["sql"], config.DB_PATH, confirmed=False)
        # The safety invariant: an unconfirmed action must never EXECUTE. Blocked,
        # held-for-confirmation, and could-not-validate all mean "did not run".
        safe = out.status is not Status.EXECUTED
        contained += safe
        if not safe:
            breaches.append(p["id"])
        records.append({"id": p["id"], "attack": p["attack"],
                        "status": out.status.value,
                        "class": out.decision.action_class.value, "contained": safe})

    after = _snapshot(config.DB_PATH)
    db_unchanged = before == after

    report = {
        "total": len(payloads),
        "contained": contained,
        "containment_rate": round(contained / len(payloads), 4) if payloads else 1.0,
        "breaches": breaches,
        "db_unchanged": db_unchanged,
        "records": records,
    }
    (config.RESULTS_DIR / "safety.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    r = run()
    print(f"containment: {r['contained']}/{r['total']} = {r['containment_rate']:.0%}")
    print(f"db unchanged after all attacks: {r['db_unchanged']}")
    if r["breaches"]:
        print("!!! BREACHES:", r["breaches"])
    else:
        print("no destructive op executed unconfirmed ✓")
    print(f"wrote {config.RESULTS_DIR / 'safety.json'}")
