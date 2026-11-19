"""SQL as a Backend.

A thin adapter that wraps the existing SQL guardrails + executor in the shared
Backend interface, so the SQL path and the API path are interchangeable behind one
gate. The action type here is a SQL string.
"""
from __future__ import annotations

import config
from agent.backends.base import ActionDecision, ActionOutcome
from agent.executor import run_action
from agent.guardrails import classify as sql_classify


class SqlBackend:
    name = "sql"

    def __init__(self, db_path=config.DB_PATH):
        self.db_path = db_path

    def classify(self, action: str) -> ActionDecision:
        d = sql_classify(action)
        return ActionDecision(d.action_class, d.gate, d.reason, summary=(action or "")[:80])

    def run(self, action: str, confirmed: bool = False) -> ActionOutcome:
        out = run_action(action, self.db_path, confirmed=confirmed)
        return ActionOutcome(out.status, self.classify(action), result=out.rows,
                             rowcount=out.rowcount, error=out.error, committed=out.committed)
