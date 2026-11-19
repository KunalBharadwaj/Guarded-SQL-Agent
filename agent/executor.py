"""Execute actions through the safety gate — the only place SQL touches the DB.

Reads run on a physically read-only connection (writes are impossible even if the
classifier were fooled — defense in depth). Mutations are never committed without
`confirmed=True`; before committing we dry-run them inside a transaction we roll
back, which both verifies the statement and measures blast radius (rows affected).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from agent.guardrails import ActionClass, Decision, Gate, classify


class Status(str, Enum):
    EXECUTED = "EXECUTED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass
class Outcome:
    status: Status
    decision: Decision
    rows: list[tuple] | None = None
    rowcount: int | None = None        # rows affected (mutations) or returned (reads)
    error: str | None = None
    committed: bool = False


def _ro_conn(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)


def _rw_conn(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(db_path).resolve())
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def execute_read(sql: str, db_path: str | Path) -> Outcome:
    decision = classify(sql)
    if not decision.is_read:
        return Outcome(Status.BLOCKED, decision,
                       error="refused: not a read-only query on the read path")
    conn = _ro_conn(db_path)
    try:
        rows = conn.execute(sql).fetchall()
        return Outcome(Status.EXECUTED, decision, rows=rows, rowcount=len(rows))
    except Exception as e:  # noqa: BLE001
        return Outcome(Status.ERROR, decision, error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


def dry_run_mutation(sql: str, db_path: str | Path) -> Outcome:
    """Run a mutation inside a rolled-back transaction: verifies it and returns the
    number of rows it *would* affect, committing nothing."""
    conn = _rw_conn(db_path)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(sql)
        affected = cur.rowcount
        conn.rollback()
        return Outcome(Status.NEEDS_CONFIRMATION, classify(sql),
                       rowcount=affected, committed=False)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return Outcome(Status.ERROR, classify(sql), error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


def run_action(sql: str, db_path: str | Path, confirmed: bool = False) -> Outcome:
    """Route one action through the gate.

    READ            -> execute read-only.
    WRITE/DESTRUCT  -> if not confirmed, dry-run and report blast radius
                       (NEEDS_CONFIRMATION); if confirmed, dry-run then commit.
    INVALID         -> BLOCKED.
    """
    decision = classify(sql)

    if decision.gate is Gate.BLOCK:
        return Outcome(Status.BLOCKED, decision, error=decision.reason)

    if decision.gate is Gate.ALLOW:  # READ
        return execute_read(sql, db_path)

    # CONFIRM path (WRITE or DESTRUCTIVE). The confirmation requirement comes from
    # the classification, not from whether the dry-run succeeds — so even if dry-run
    # validation raises (e.g. a FK constraint), the op is still held, never executed.
    if not confirmed:
        preview = dry_run_mutation(sql, db_path)
        if preview.status is Status.ERROR:
            return Outcome(Status.NEEDS_CONFIRMATION, decision, rowcount=None,
                           error=f"dry-run validation could not complete: {preview.error}")
        return Outcome(Status.NEEDS_CONFIRMATION, decision, rowcount=preview.rowcount)

    # Confirmed: verify on a dry-run first; only commit if it succeeds.
    check = dry_run_mutation(sql, db_path)
    if check.status is Status.ERROR:
        return Outcome(Status.ERROR, decision, error=check.error)

    conn = _rw_conn(db_path)
    try:
        cur = conn.execute(sql)
        conn.commit()
        return Outcome(Status.EXECUTED, decision, rowcount=cur.rowcount, committed=True)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return Outcome(Status.ERROR, decision, error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()
