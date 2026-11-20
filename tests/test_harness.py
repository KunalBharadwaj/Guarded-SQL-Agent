"""The scorer must reward equivalent queries and reject wrong ones."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from eval.harness import column_subset_match, has_order_by, result_sets_match, score


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE t (id INTEGER, v INTEGER);"
        "INSERT INTO t VALUES (1,10),(2,20),(3,30);"
    )
    return c


def test_order_insensitive_by_default(conn):
    # Same rows, different order -> correct (gold has no ORDER BY).
    r = score(conn, "SELECT id FROM t ORDER BY id DESC", "SELECT id FROM t")
    assert r.correct


def test_order_sensitive_when_gold_orders(conn):
    gold = "SELECT id FROM t ORDER BY id ASC"
    assert score(conn, "SELECT id FROM t ORDER BY id DESC", gold).correct is False
    assert score(conn, "SELECT id FROM t ORDER BY id ASC", gold).correct is True


def test_equivalent_but_differently_written(conn):
    # COUNT vs SUM(1) produce the same result set.
    r = score(conn, "SELECT SUM(1) FROM t", "SELECT COUNT(*) FROM t")
    assert r.correct


def test_int_float_equivalence(conn):
    r = score(conn, "SELECT 20.0", "SELECT AVG(v) FROM t")
    assert r.correct


def test_exec_error_is_incorrect(conn):
    r = score(conn, "SELECT nope FROM t", "SELECT id FROM t")
    assert r.correct is False
    assert r.reason == "exec_error"


def test_has_order_by_ignores_string_literals():
    assert has_order_by("SELECT * FROM t ORDER BY id") is True
    assert has_order_by("SELECT 'order by x' AS note FROM t") is False


def test_has_order_by_only_counts_top_level():
    # Regression: ORDER BY in a window function or subquery must NOT count.
    assert has_order_by(
        "SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS rn FROM t") is False
    assert has_order_by(
        "SELECT * FROM (SELECT id FROM t ORDER BY id) sub") is False
    assert has_order_by(
        "SELECT * FROM (SELECT id FROM t ORDER BY id) sub ORDER BY id") is True
    # CTE body ordering is nested; only the outer query's ORDER BY is top-level.
    assert has_order_by(
        "WITH x AS (SELECT id FROM t ORDER BY id) SELECT * FROM x") is False


def test_result_sets_match_multiset():
    assert result_sets_match([(1,), (1,), (2,)], [(1,), (2,), (1,)], ordered=False)
    assert not result_sets_match([(1,), (1,)], [(1,)], ordered=False)


# --- lenient (extra-column projection) matching ---------------------------

@pytest.fixture()
def uk_conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE customers (id INTEGER, first TEXT, last TEXT, country TEXT);"
        "INSERT INTO customers VALUES (1,'A','X','UK'),(2,'B','Y','UK'),(3,'C','Z','US');"
    )
    return c


def test_lenient_forgives_select_star(uk_conn):
    gold = "SELECT first, last FROM customers WHERE country='UK'"
    pred = "SELECT * FROM customers WHERE country='UK'"          # extra columns
    assert score(uk_conn, pred, gold).correct is False           # strict: mismatch
    r = score(uk_conn, pred, gold, lenient=True)
    assert r.correct is True and r.reason == "lenient_match"


def test_lenient_forgives_extra_trailing_column(uk_conn):
    gold = "SELECT first FROM customers WHERE country='UK'"
    pred = "SELECT first, last FROM customers WHERE country='UK'"
    assert score(uk_conn, pred, gold, lenient=True).correct is True


def test_lenient_does_not_forgive_wrong_rows(uk_conn):
    # SELECT * but WITHOUT the filter -> extra rows, must still fail even leniently.
    gold = "SELECT first, last FROM customers WHERE country='UK'"
    pred = "SELECT * FROM customers"                              # all customers
    assert score(uk_conn, pred, gold, lenient=True).correct is False


def test_lenient_requires_gold_columns_present(uk_conn):
    gold = "SELECT first, last FROM customers WHERE country='UK'"
    pred = "SELECT country FROM customers WHERE country='UK'"     # wrong columns
    assert score(uk_conn, pred, gold, lenient=True).correct is False


def test_column_subset_preserves_row_correlation():
    # gold pairs (a,1),(b,2); a prediction whose columns individually contain
    # {a,b} and {1,2} but mis-paired must NOT match.
    gold = [("a", 1), ("b", 2)]
    bad = [("a", 2), ("b", 1)]        # same column values, wrong pairing
    assert column_subset_match(bad, gold, ordered=False) is False
