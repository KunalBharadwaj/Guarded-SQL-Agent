"""The full pipeline: ground -> generate -> gate -> execute / correct / escalate.

This is where grounding, self-correction, the safety gate, confidence gating, and
the audit log come together. Every path writes an audit record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import config
from agent import audit, confidence, decomposition
from agent.executor import Status, run_action
from agent.guardrails import ActionClass, classify
from agent.llm import LLMClient, get_client
from agent.retriever import SchemaRetriever
from agent.self_correction import correct_loop


class ResponseStatus(str, Enum):
    ANSWERED = "ANSWERED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    ESCALATED = "ESCALATED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass
class AgentResponse:
    status: ResponseStatus
    question: str
    sql: str | None = None
    rows: list | None = None
    rowcount: int | None = None
    message: str = ""
    action_class: str | None = None
    confidence: float | None = None
    attempts: int | None = None
    audit_entry: dict = field(default_factory=dict)


class Agent:
    def __init__(self, client: LLMClient | None = None,
                 retriever: SchemaRetriever | None = None, db_path=config.DB_PATH):
        self.client = client or get_client()
        self.retriever = retriever or SchemaRetriever()
        self.db_path = db_path

    def ask(self, question: str, confirmed: bool = False) -> AgentResponse:
        top = self.retriever.top_score(question)

        # 1) Ambiguity gate — defer before generating anything if the request
        #    barely matches the schema.
        amb = confidence.assess_query(top, loop_success=True, attempts=1)
        if amb.escalate:
            return self._finalize(AgentResponse(
                ResponseStatus.ESCALATED, question, confidence=amb.score,
                message=f"Escalating: {amb.reason}. {amb.clarify}"))

        # 2) Grounded generation + self-correction (reads execute during the loop).
        loop = correct_loop(self.client, question, self.retriever, self.db_path)
        decision = classify(loop.sql)

        # 3) Blocked outright (unparseable / multi-statement).
        if decision.action_class is ActionClass.INVALID:
            return self._finalize(AgentResponse(
                ResponseStatus.BLOCKED, question, sql=loop.sql,
                action_class=decision.action_class.value,
                message=f"Blocked: {decision.reason}"))

        # 4) Reads: confidence-gate, then return the already-executed result.
        if decision.is_read:
            assessment = confidence.assess_query(top, loop.success, loop.attempts)
            if assessment.escalate:
                return self._finalize(AgentResponse(
                    ResponseStatus.ESCALATED, question, sql=loop.sql,
                    confidence=assessment.score, attempts=loop.attempts,
                    action_class=decision.action_class.value,
                    message=f"Escalating: {assessment.reason}. {assessment.clarify}"))
            out = run_action(loop.sql, self.db_path)  # read-only execution via the gate
            if out.status is Status.ERROR:
                return self._finalize(AgentResponse(
                    ResponseStatus.ERROR, question, sql=loop.sql,
                    action_class=decision.action_class.value, message=out.error))
            return self._finalize(AgentResponse(
                ResponseStatus.ANSWERED, question, sql=loop.sql, rows=out.rows,
                rowcount=out.rowcount, confidence=assessment.score,
                attempts=loop.attempts, action_class=decision.action_class.value,
                message="OK"))

        # 5) Mutations: route through the gate (dry-run / confirm).
        out = run_action(loop.sql, self.db_path, confirmed=confirmed)
        if out.status is Status.NEEDS_CONFIRMATION:
            kind = decision.action_class.value.lower()
            if out.rowcount is None:
                scope = ("an unknown number of rows — dry-run could not complete "
                         f"({out.error})" if out.error else "an unknown number of rows")
            elif confidence.large_blast_radius(out.rowcount):
                scope = f"{out.rowcount} row(s) — LARGE blast radius, review carefully"
            else:
                scope = f"{out.rowcount} row(s)"
            msg = (f"Confirmation required: this {kind} would affect {scope}. "
                   "Re-run with confirmation to execute.")
            return self._finalize(AgentResponse(
                ResponseStatus.NEEDS_CONFIRMATION, question, sql=loop.sql,
                rowcount=out.rowcount, action_class=decision.action_class.value,
                message=msg))
        if out.status is Status.EXECUTED:
            return self._finalize(AgentResponse(
                ResponseStatus.ANSWERED, question, sql=loop.sql, rowcount=out.rowcount,
                action_class=decision.action_class.value,
                message=f"Executed and committed ({out.rowcount} row(s) affected)."))
        return self._finalize(AgentResponse(
            ResponseStatus.ERROR, question, sql=loop.sql,
            action_class=decision.action_class.value, message=out.error or "error"))

    def solve(self, question: str) -> decomposition.MultiStepResult:
        """Answer a (possibly complex) question via multi-step decomposition.

        Simple questions plan to one step and behave like `ask`. Every step is
        audited, so the plan and each sub-query show up in the trail.
        """
        res = decomposition.solve(self.client, self.retriever, question, self.db_path)
        for st in res.steps:
            audit.record({
                "question": f"[step {st.index}/{len(res.plan)}] {st.question}",
                "sql": st.sql, "action_class": "READ",
                "status": "MULTISTEP_OK" if st.ok else "MULTISTEP_FAIL",
                "rowcount": len(st.rows) if st.rows is not None else None,
                "message": st.summary, "parent_question": question,
            })
        return res

    def _finalize(self, resp: AgentResponse) -> AgentResponse:
        resp.audit_entry = audit.record({
            "question": resp.question,
            "sql": resp.sql,
            "action_class": resp.action_class,
            "status": resp.status.value,
            "rowcount": resp.rowcount,
            "confidence": resp.confidence,
            "attempts": resp.attempts,
            "message": resp.message,
        })
        return resp
