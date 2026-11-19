"""Read schema structure from the live SQLite database.

Used for the baseline context (table names only), as a source for the Phase-1
schema cards, and anywhere the agent needs to know what really exists.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(db_path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def table_names(db_path: str | Path) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def columns(db_path: str | Path, table: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # cid, name, type, notnull, dflt_value, pk
        return [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                for r in rows]
    finally:
        conn.close()


def baseline_context(db_path: str | Path) -> str:
    """Minimal, deliberately naive context: just the table names.

    The model must guess columns and joins — this is the Phase-0 baseline whose
    gap Phase-1 grounding closes.
    """
    names = table_names(db_path)
    return "Tables: " + ", ".join(names)


def full_ddl(db_path: str | Path) -> str:
    """Every CREATE statement — the 'dump the whole schema' anti-pattern, kept for
    comparison in the write-up."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return "\n\n".join(r[0] for r in rows if r[0])
    finally:
        conn.close()
