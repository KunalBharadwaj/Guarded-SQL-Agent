"""Multi-step decomposition, driven offline by a scripted client (no API)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.decomposition import make_plan, parse_steps, solve
from agent.retriever import SchemaRetriever


class ScriptClient:
    """Returns scripted responses in order: first the plan, then each step's SQL."""
    name = "script"

    def __init__(self, plan_steps, step_sqls):
        self._queue = [json.dumps({"steps": plan_steps})]
        self._queue += [json.dumps({"sql": s, "rationale": ""}) for s in step_sqls]
        self._i = 0

    def complete(self, system, user):
        out = self._queue[min(self._i, len(self._queue) - 1)]
        self._i += 1
        return out


def test_parse_steps_handles_prose_wrapping():
    raw = 'Sure!\n{"steps": ["a", "b"]}\nhope that helps'
    assert parse_steps(raw) == ["a", "b"]


def test_make_plan_falls_back_to_single_step():
    class Bad:
        name = "bad"
        def complete(self, s, u):
            return "no json here"
    assert make_plan(Bad(), "how many customers") == ["how many customers"]


def test_single_step_behaves_like_one_shot():
    client = ScriptClient(
        plan_steps=["how many customers are there"],
        step_sqls=["SELECT COUNT(*) FROM customers"],
    )
    res = solve(client, SchemaRetriever(), "how many customers are there")
    assert res.decomposed is False
    assert res.success is True
    assert res.final_rows == [(120,)]


def test_dependent_two_step_chain():
    # Classic decomposition: compute an aggregate, then filter by it.
    client = ScriptClient(
        plan_steps=[
            "what is the average number of orders per customer",
            "list customers with more orders than that average",
        ],
        step_sqls=[
            "SELECT AVG(c) FROM (SELECT COUNT(*) c FROM orders GROUP BY customer_id)",
            "SELECT customer_id FROM orders GROUP BY customer_id HAVING COUNT(*) > 5",
        ],
    )
    res = solve(client, SchemaRetriever(), "which customers order more than average")
    assert res.decomposed is True
    assert len(res.steps) == 2
    assert all(st.ok for st in res.steps)
    assert res.success is True and res.final_rows


def test_mutation_in_a_step_stops_the_chain():
    client = ScriptClient(
        plan_steps=["step one", "delete everything"],
        step_sqls=["SELECT COUNT(*) FROM orders", "DELETE FROM orders"],
    )
    res = solve(client, SchemaRetriever(), "do something then wipe orders")
    assert res.success is False
    assert res.steps[-1].ok is False
    assert "non-read" in res.steps[-1].summary
