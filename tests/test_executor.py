"""The executor must never commit a mutation without confirmation, and reads must
never be able to write."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.executor import Status, dry_run_mutation, execute_read, run_action


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "t.db"
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER);"
        "INSERT INTO t VALUES (1,10),(2,20),(3,30);"
    )
    c.commit()
    c.close()
    return p


def _count(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        c.close()


def test_read_executes(db):
    out = run_action("SELECT v FROM t WHERE id = 2", db)
    assert out.status is Status.EXECUTED
    assert out.rows == [(20,)]


def test_mutation_without_confirmation_does_not_commit(db):
    out = run_action("DELETE FROM t", db)
    assert out.status is Status.NEEDS_CONFIRMATION
    assert out.committed is False
    assert out.rowcount == 3          # blast radius reported
    assert _count(db) == 3            # nothing actually deleted


def test_confirmed_mutation_commits(db):
    out = run_action("DELETE FROM t WHERE id = 1", db, confirmed=True)
    assert out.status is Status.EXECUTED
    assert out.committed is True
    assert _count(db) == 2


def test_stacked_injection_blocked(db):
    out = run_action("SELECT * FROM t; DROP TABLE t", db)
    assert out.status is Status.BLOCKED
    assert _count(db) == 3            # table intact


def test_read_path_refuses_mutation(db):
    # Even if routed to the read path, a write cannot run read-only.
    out = execute_read("DELETE FROM t", db)
    assert out.status is Status.BLOCKED
    assert _count(db) == 3


def test_dry_run_reports_blast_radius_without_committing(db):
    out = dry_run_mutation("UPDATE t SET v = 0", db)
    assert out.rowcount == 3
    assert out.committed is False
    # confirm the update did not persist
    c = sqlite3.connect(db)
    assert c.execute("SELECT SUM(v) FROM t").fetchone()[0] == 60
    c.close()
