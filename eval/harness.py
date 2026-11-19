"""Execution-accuracy scorer.

A prediction is correct when it *executes to the same result set* as the gold
query — not when its SQL string matches. Two differently-written queries can be
equally correct, so we compare results, not text.

Comparison rules:
  - Rows are compared as a multiset (Counter) so column/row *order* from the
    engine does not matter...
  - ...UNLESS the gold query has a top-level ORDER BY, in which case row order is
    significant and we compare the ordered sequences.
  - Cell values are normalised (floats rounded) so 1 == 1.0 and 2.3000001 == 2.3.

Everything runs through a READ-ONLY connection, so a scorer can never mutate the DB.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path

# Lenient matching only tries column subsets when the prediction isn't absurdly wide,
# to bound the permutation search (our schema's widest table is ~7 columns).
MAX_LENIENT_PRED_COLS = 12

FLOAT_NDIGITS = 4
_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open the database read-only. Writes raise sqlite3.OperationalError."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _norm_cell(v: object) -> object:
    if isinstance(v, float):
        return round(v, FLOAT_NDIGITS)
    if isinstance(v, int):  # make 1 == 1.0
        return round(float(v), FLOAT_NDIGITS)
    return v


def _norm_rows(rows: list[tuple]) -> list[tuple]:
    return [tuple(_norm_cell(c) for c in row) for row in rows]


def has_order_by(sql: str) -> bool:
    # Strip string literals so an ORDER BY inside a string doesn't count.
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return bool(_ORDER_BY_RE.search(stripped))


@dataclass
class ExecResult:
    ok: bool                 # did the SQL execute without error?
    rows: list[tuple] | None
    error: str | None = None


def run_sql(conn: sqlite3.Connection, sql: str) -> ExecResult:
    """Execute read-only SQL, returning rows or the error message."""
    try:
        cur = conn.execute(sql)
        return ExecResult(ok=True, rows=cur.fetchall())
    except Exception as e:  # noqa: BLE001 — surface any engine error to the loop
        return ExecResult(ok=False, rows=None, error=f"{type(e).__name__}: {e}")


@dataclass
class ScoreResult:
    correct: bool
    reason: str                 # "match" | "exec_error" | "result_mismatch" | "empty"
    pred_error: str | None = None
    pred_rowcount: int | None = None
    gold_rowcount: int | None = None


def result_sets_match(pred_rows: list[tuple], gold_rows: list[tuple], ordered: bool) -> bool:
    p, g = _norm_rows(pred_rows), _norm_rows(gold_rows)
    if ordered:
        return p == g
    return Counter(p) == Counter(g)


def column_subset_match(pred_rows: list[tuple], gold_rows: list[tuple], ordered: bool) -> bool:
    """Lenient: the prediction is correct if some ordered subset of its columns
    reproduces the gold result row-for-row.

    This forgives *extra* columns (e.g. SELECT * instead of two named columns) but
    NOT wrong rows: because whole predicted rows are projected onto the chosen columns
    and compared as tuples, a missing filter or bad join changes the rows and still
    fails. Row/column correlation is preserved (we project tuples, not columns
    independently).
    """
    if not pred_rows or not gold_rows:
        return False
    g_ncols, p_ncols = len(gold_rows[0]), len(pred_rows[0])
    if p_ncols < g_ncols or p_ncols > MAX_LENIENT_PRED_COLS:
        return False
    for cols in permutations(range(p_ncols), g_ncols):
        projected = [tuple(row[c] for c in cols) for row in pred_rows]
        if result_sets_match(projected, gold_rows, ordered):
            return True
    return False


def score(conn: sqlite3.Connection, pred_sql: str, gold_sql: str,
          lenient: bool = False) -> ScoreResult:
    """Score one prediction against its gold query on an open read-only connection.

    With lenient=True, a result that isn't an exact match but whose columns contain
    the gold's columns (extra-column projection) is accepted as "lenient_match".
    """
    gold = run_sql(conn, gold_sql)
    if not gold.ok:
        # A broken gold query is a dataset bug, not a model error — surface loudly.
        return ScoreResult(False, "gold_error", pred_error=gold.error)

    pred = run_sql(conn, pred_sql)
    if not pred.ok:
        return ScoreResult(False, "exec_error", pred_error=pred.error,
                           gold_rowcount=len(gold.rows))

    ordered = has_order_by(gold_sql)
    if result_sets_match(pred.rows, gold.rows, ordered):
        reason, correct = "match", True
    elif lenient and column_subset_match(pred.rows, gold.rows, ordered):
        reason, correct = "lenient_match", True
    else:
        reason, correct = "result_mismatch", False
    return ScoreResult(correct, reason, pred_rowcount=len(pred.rows),
                       gold_rowcount=len(gold.rows))
