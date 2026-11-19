"""Backend-agnostic gate interface.

The safety story isn't about SQL — it's about routing *any* generated action through
a classify -> gate -> execute/confirm path. A Backend maps a backend-specific action
(a SQL string, an API call, ...) onto the same ActionClass / Gate vocabulary the SQL
guardrails use, so one orchestration pattern covers both.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent.executor import Status  # shared action status (EXECUTED/NEEDS_CONFIRMATION/…)
from agent.guardrails import ActionClass, Gate

__all__ = ["ActionClass", "Gate", "Status", "ActionDecision", "ActionOutcome", "Backend"]


@dataclass
class ActionDecision:
    action_class: ActionClass
    gate: Gate
    reason: str
    summary: str = ""          # human-readable description of the action

    @property
    def is_read(self) -> bool:
        return self.action_class is ActionClass.READ


@dataclass
class ActionOutcome:
    status: Status
    decision: ActionDecision
    result: Any = None
    rowcount: int | None = None   # rows/records affected (mutations) or returned (reads)
    error: str | None = None
    committed: bool = False


@runtime_checkable
class Backend(Protocol):
    name: str

    def classify(self, action: Any) -> ActionDecision: ...

    def run(self, action: Any, confirmed: bool = False) -> ActionOutcome: ...
