"""The safety core must fail closed. These cases are the safety claim."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.guardrails import ActionClass, Gate, classify


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "SELECT count(*) FROM orders WHERE status = 'shipped'",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.customer_id",
    "SELECT * FROM products ORDER BY unit_price DESC LIMIT 5",
])
def test_reads_are_allowed(sql):
    d = classify(sql)
    assert d.action_class is ActionClass.READ
    assert d.gate is Gate.ALLOW


@pytest.mark.parametrize("sql,klass", [
    ("INSERT INTO customers (first_name, last_name) VALUES ('a','b')", ActionClass.WRITE),
    ("UPDATE customers SET is_active = 0 WHERE customer_id = 1", ActionClass.WRITE),
    ("CREATE TABLE temp (id INTEGER)", ActionClass.WRITE),
])
def test_writes_need_confirmation(sql, klass):
    d = classify(sql)
    assert d.action_class is klass
    assert d.gate is Gate.CONFIRM


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers WHERE customer_id = 1",
    "DROP TABLE customers",
    "ALTER TABLE customers ADD COLUMN x INTEGER",
    "DELETE FROM orders",                                  # no WHERE: huge blast radius
])
def test_destructive_need_confirmation(sql):
    d = classify(sql)
    assert d.action_class is ActionClass.DESTRUCTIVE
    assert d.gate is Gate.CONFIRM


def test_cte_wrapping_delete_is_caught():
    # A mutation hidden inside a CTE must still be classified destructive.
    sql = "WITH doomed AS (SELECT customer_id FROM customers) DELETE FROM customers"
    d = classify(sql)
    assert d.action_class is ActionClass.DESTRUCTIVE
    assert d.gate is Gate.CONFIRM


def test_stacked_injection_is_blocked():
    # Classic injection: a benign read with a destructive statement appended.
    sql = "SELECT * FROM customers; DROP TABLE customers"
    d = classify(sql)
    assert d.action_class is ActionClass.INVALID
    assert d.gate is Gate.BLOCK


def test_comment_hidden_drop_is_not_executed_as_read():
    sql = "SELECT 1; DROP TABLE customers --"
    d = classify(sql)
    assert d.gate is Gate.BLOCK


@pytest.mark.parametrize("sql", ["", "   ", "this is not sql at all !!!"])
def test_garbage_is_blocked_or_gated(sql):
    d = classify(sql)
    # Never silently allowed as a read.
    assert d.gate in (Gate.BLOCK, Gate.CONFIRM)
    assert d.action_class is not ActionClass.READ
