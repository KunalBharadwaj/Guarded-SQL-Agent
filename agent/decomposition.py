"""Multi-step decomposition for complex questions.

Some questions can't be answered well in one shot — typically when a later query
needs a value an earlier one computes (e.g. "customers who placed more orders than
average": first the average, then the filter). The planner breaks such a question
into an ordered list of sub-questions; each is answered by the normal grounded +
self-corrected read path, and a short summary of each result is fed forward as
context to the next step. Simple questions plan to a single step and behave exactly
like the one-shot path.

Intermediate steps must be reads — decomposition never executes a mutation to make
progress; a generated write stops the chain and is left for the gate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import config
from agent.guardrails import classify
from agent.llm import LLMClient
from agent.retriever import SchemaRetriever
from agent.self_correction import correct_loop
from eval.harness import readonly_connection, run_sql

PLANNER_SYSTEM = (
    "You break a natural-language data question into the MINIMUM ordered sub-questions "
    "needed to answer it against a SQL database. Most questions need only ONE step. Use "
    "multiple steps ONLY when a later query must use a value computed by an earlier one "
    "(for example 'which customers spent more than the average order value'). Each step "
    "must be answerable by a single read-only SQL query. Return ONLY a JSON object: "
    '{"steps": ["sub-question 1", "sub-question 2", ...]}. Use at most 4 steps.'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Step:
    index: int
    question: str
    sql: str
    ok: bool
    rows: list | None
    summary: str


@dataclass
class MultiStepResult:
    question: str
    plan: list[str]
    steps: list[Step] = field(default_factory=list)
    final_sql: str | None = None
    final_rows: list | None = None
    success: bool = False

    @property
    def decomposed(self) -> bool:
        return len(self.plan) > 1


def parse_steps(raw: str) -> list[str] | None:
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    steps = obj.get("steps")
    if isinstance(steps, list):
        cleaned = [str(s).strip() for s in steps if str(s).strip()]
        return cleaned or None
    return None


def make_plan(client: LLMClient, question: str, max_steps: int = 4) -> list[str]:
    raw = client.complete(PLANNER_SYSTEM, f"Question: {question}\nReturn the JSON.")
    steps = parse_steps(raw) or [question]          # fall back to a single step
    return steps[:max_steps]


def _summarize(idx: int, subq: str, rows: list | None, error: str | None) -> str:
    if error:
        return f"Step {idx} ({subq}) errored: {error}"
    if not rows:
        return f"Step {idx} ({subq}) returned no rows."
    if len(rows) == 1 and len(rows[0]) == 1:
        return f"Step {idx} ({subq}) = {rows[0][0]}"
    sample = rows[:5]
    more = "" if len(rows) <= 5 else f" (+{len(rows) - 5} more)"
    return f"Step {idx} ({subq}) returned {len(rows)} row(s); sample: {sample}{more}"


def solve(client: LLMClient, retriever: SchemaRetriever, question: str,
          db_path=config.DB_PATH) -> MultiStepResult:
    plan = make_plan(client, question)
    result = MultiStepResult(question=question, plan=plan)
    carried: list[str] = []
    conn = readonly_connection(db_path)
    try:
        for i, subq in enumerate(plan, start=1):
            extra = ("Results from earlier steps:\n" + "\n".join(carried)) if carried else None
            loop = correct_loop(client, subq, retriever, db_path=db_path, extra_context=extra)

            if not classify(loop.sql).is_read:
                result.steps.append(Step(i, subq, loop.sql, False, None,
                                         f"Step {i} produced a non-read action; stopped."))
                result.success = False
                return result

            res = run_sql(conn, loop.sql)
            rows = res.rows if res.ok else None
            summary = _summarize(i, subq, rows, None if res.ok else res.error)
            result.steps.append(Step(i, subq, loop.sql, res.ok and bool(rows), rows, summary))
            carried.append(summary)
            result.final_sql, result.final_rows = loop.sql, rows

            if not res.ok:
                result.success = False
                return result
    finally:
        conn.close()

    result.success = result.final_rows is not None
    return result
