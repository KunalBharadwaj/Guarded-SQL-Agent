"""End-to-end routing of the orchestrator, driven by a fake LLM (no API needed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
from agent.orchestrator import Agent, ResponseStatus


class FakeClient:
    name = "fake"

    def __init__(self, sql):
        self._sql = sql

    def complete(self, system, user):
        return json.dumps({"sql": self._sql, "rationale": "fake"})


@pytest.fixture(autouse=True)
def tmp_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")


def _agent(sql):
    return Agent(client=FakeClient(sql))


def test_read_is_answered():
    r = _agent("SELECT COUNT(*) FROM customers").ask("how many customers are there")
    assert r.status is ResponseStatus.ANSWERED
    assert r.rows and r.rows[0][0] == 120
    assert r.audit_entry["ts"]


def test_mutation_needs_confirmation():
    r = _agent("DELETE FROM reviews WHERE rating = 1").ask(
        "delete reviews with rating 1 from the reviews table")
    assert r.status is ResponseStatus.NEEDS_CONFIRMATION
    assert r.action_class == "DESTRUCTIVE"


def test_injection_is_blocked():
    r = _agent("SELECT 1; DROP TABLE customers").ask(
        "show me the customers in the customers table")
    assert r.status is ResponseStatus.BLOCKED


def test_ambiguous_request_escalates():
    # Scores below the schema-match threshold -> defer without even generating.
    r = _agent("SELECT 1").ask("what is the weather on mars tomorrow")
    assert r.status is ResponseStatus.ESCALATED
    assert "scalat" in r.message.lower()
