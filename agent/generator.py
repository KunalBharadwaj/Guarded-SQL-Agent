"""NL -> SQL generation with structured output.

We ask the model for a strict JSON object {"sql": ..., "rationale": ...} and parse
it defensively (reusing the constrained-decoding idea: pin the action format so a
stray prose answer can't slip through as an action). Extraction falls back to
fenced ```sql blocks, then to a raw statement, so a slightly-off model still works.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent.llm import LLMClient

SYSTEM = (
    "You are a careful data analyst that translates natural-language questions into a "
    "single SQLite SQL query. Rules:\n"
    "- Output ONLY a JSON object: {\"sql\": \"<one SQL statement>\", \"rationale\": \"<brief>\"}.\n"
    "- Use exactly one statement. No comments, no trailing semicolons inside strings.\n"
    "- Only reference tables and columns you were given. Do not invent names.\n"
    "- Prefer a read-only SELECT unless the request clearly asks to modify data."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_STMT_RE = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|WITH)\b.*", re.DOTALL | re.IGNORECASE
)


@dataclass
class Generation:
    sql: str
    rationale: str
    raw: str


def extract_sql(raw: str) -> tuple[str, str]:
    """Return (sql, rationale) from a model response, best-effort."""
    # 1) strict JSON object
    m = _JSON_RE.search(raw)
    if m:
        try:
            obj = json.loads(m.group(0))
            sql = str(obj.get("sql", "")).strip()
            if sql:
                return sql.rstrip(";").strip(), str(obj.get("rationale", "")).strip()
        except json.JSONDecodeError:
            pass
    # 2) fenced code block
    m = _SQL_FENCE_RE.search(raw)
    if m and m.group(1).strip():
        return m.group(1).strip().rstrip(";").strip(), ""
    # 3) first SQL-looking statement
    m = _STMT_RE.search(raw)
    if m:
        return m.group(0).strip().rstrip(";").strip(), ""
    return raw.strip().rstrip(";").strip(), ""


def build_user_prompt(question: str, schema_context: str, feedback: str | None = None) -> str:
    parts = [f"Database schema:\n{schema_context}", f"\nQuestion: {question}"]
    if feedback:
        parts.append(
            "\nYour previous attempt failed. Fix it.\n" + feedback
        )
    parts.append("\nReturn the JSON object now.")
    return "\n".join(parts)


def generate_sql(
    client: LLMClient,
    question: str,
    schema_context: str,
    feedback: str | None = None,
) -> Generation:
    user = build_user_prompt(question, schema_context, feedback)
    raw = client.complete(SYSTEM, user)
    sql, rationale = extract_sql(raw)
    return Generation(sql=sql, rationale=rationale, raw=raw)
