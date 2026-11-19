"""The safety gate: parse, classify, and decide — before anything runs.

Every generated action passes through `classify()`. We parse with sqlglot (not
regex, which injection slips past), walk the AST for *any* mutating node (so a
mutation hidden inside a CTE or subquery is still caught), and reject multi-
statement payloads outright.

Policy:
  READ         -> ALLOW   (execute read-only)
  WRITE        -> CONFIRM (INSERT / UPDATE / CREATE — needs human confirmation)
  DESTRUCTIVE  -> CONFIRM (DELETE / DROP / ALTER / TRUNCATE / unknown command)
  INVALID      -> BLOCK   (unparseable, empty, or more than one statement)

Only READ ever auto-executes. This is the project's safety headline, so it fails
closed: anything it cannot confidently classify as a read is gated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import sqlglot
from sqlglot import exp

DIALECT = "sqlite"


class ActionClass(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    INVALID = "INVALID"


class Gate(str, Enum):
    ALLOW = "ALLOW"       # safe read — auto-execute read-only
    CONFIRM = "CONFIRM"   # mutating — hold for human confirmation
    BLOCK = "BLOCK"       # malformed/multi-statement — never execute


# AST node types that signal a mutation, most-severe first.
_DESTRUCTIVE_NODES = (exp.Delete, exp.Drop, exp.Alter, exp.TruncateTable)
_WRITE_NODES = (exp.Insert, exp.Update, exp.Create)


@dataclass
class Decision:
    action_class: ActionClass
    gate: Gate
    reason: str
    statement_count: int = 0
    tables: list[str] = field(default_factory=list)
    normalized_sql: str | None = None

    @property
    def is_read(self) -> bool:
        return self.action_class is ActionClass.READ

    @property
    def needs_confirmation(self) -> bool:
        return self.gate is Gate.CONFIRM


def _tables(tree: exp.Expression) -> list[str]:
    names = []
    for t in tree.find_all(exp.Table):
        if t.name and t.name not in names:
            names.append(t.name)
    return names


def classify(sql: str) -> Decision:
    sql = (sql or "").strip()
    if not sql:
        return Decision(ActionClass.INVALID, Gate.BLOCK, "empty statement")

    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except Exception as e:  # noqa: BLE001 — any parse failure fails closed
        return Decision(ActionClass.INVALID, Gate.BLOCK,
                        f"unparseable SQL ({type(e).__name__})")

    if len(statements) == 0:
        return Decision(ActionClass.INVALID, Gate.BLOCK, "no statement parsed")
    if len(statements) > 1:
        return Decision(ActionClass.INVALID, Gate.BLOCK,
                        f"multiple statements ({len(statements)}) not allowed",
                        statement_count=len(statements))

    tree = statements[0]
    tables = _tables(tree)
    norm = tree.sql(dialect=DIALECT)

    # Defense in depth: scan the whole tree, not just the root node.
    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _DESTRUCTIVE_NODES):
            return Decision(ActionClass.DESTRUCTIVE, Gate.CONFIRM,
                            f"{type(node).__name__.lower()} requires confirmation",
                            1, tables, norm)
    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _WRITE_NODES):
            return Decision(ActionClass.WRITE, Gate.CONFIRM,
                            f"{type(node).__name__.lower()} requires confirmation",
                            1, tables, norm)

    # A bare SELECT (optionally wrapped in a WITH) with no mutating node is a read.
    if isinstance(tree, (exp.Select, exp.Union, exp.With, exp.Subquery)):
        return Decision(ActionClass.READ, Gate.ALLOW, "read-only query", 1, tables, norm)

    # exp.Command and anything else unrecognised: fail closed.
    return Decision(ActionClass.DESTRUCTIVE, Gate.CONFIRM,
                    f"unrecognised statement ({type(tree).__name__}) — gated", 1, tables, norm)
