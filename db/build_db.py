"""Build db/guarded.db from schema.sql + seed.py (idempotent: drops & rebuilds).

Usage:  python db/build_db.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db.seed import seed  # noqa: E402


def build() -> None:
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(config.SCHEMA_SQL.read_text())
    seed(conn)
    # quick sanity counts
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("customers", "orders", "order_items", "products", "payments", "reviews")
    }
    conn.close()
    print(f"Built {config.DB_PATH}")
    for t, n in counts.items():
        print(f"  {t:<12} {n}")


if __name__ == "__main__":
    build()
