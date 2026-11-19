"""Execute-observe-retry loop.

Generate SQL, run it read-only, and if it errors (or comes back empty, which on
this dataset signals a wrong join/filter) feed the failure back and ask for a fix.
Retries are capped (config.MAX_RETRIES) so a stubborn query can't loop forever.

Only reads are executed here — self-correction is about getting a SELECT right.
A generated mutation is handed back untouched for the safety gate to deal with;
we never run a write just to "see if it works".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
from agent.generator import generate_sql
from agent.guardrails import classify
from agent.llm import LLMClient
from agent.retriever import SchemaRetriever
from eval.harness import readonly_connection, run_sql


@dataclass
class LoopResult:
    sql: str
    attempts: int
    success: bool                      # executed without error and returned rows
    is_read: bool
    rationale: str = ""
    history: list[dict] = field(default_factory=list)  # per failed attempt


def _feedback(sql: str, error: str | None) -> str:
    if error:
        return f"Your previous query raised an error:\n{error}\n\nThe query was:\n{sql}"
    return (
        "Your previous query executed but returned 0 rows, which is almost certainly "
        "wrong for this question. Re-check the joins, filters, and column names.\n\n"
        f"The query was:\n{sql}"
    )


def correct_loop(
    client: LLMClient,
    question: str,
    retriever: SchemaRetriever,
    db_path=config.DB_PATH,
    max_retries: int | None = None,
    extra_context: str | None = None,
) -> LoopResult:
    max_retries = config.MAX_RETRIES if max_retries is None else max_retries
    context = retriever.context_for(question)
    if extra_context:
        context = f"{context}\n\n{extra_context}"
    feedback: str | None = None
    history: list[dict] = []
    last = LoopResult(sql="", attempts=0, success=False, is_read=False)

    for attempt in range(max_retries + 1):
        gen = generate_sql(client, question, context, feedback=feedback)
        decision = classify(gen.sql)
        last = LoopResult(sql=gen.sql, attempts=attempt + 1, success=False,
                          is_read=decision.is_read, rationale=gen.rationale,
                          history=history)

        if not decision.is_read:
            # A mutation (or unparseable) — the loop doesn't execute it; the gate does.
            return last

        conn = readonly_connection(db_path)
        try:
            res = run_sql(conn, gen.sql)
        finally:
            conn.close()

        if res.ok and res.rows:
            last.success = True
            return last

        # Failed: record it and build feedback for the next attempt.
        err = res.error or "empty_result"
        history.append({"attempt": attempt + 1, "sql": gen.sql, "error": err})
        feedback = _feedback(gen.sql, res.error)

    return last
