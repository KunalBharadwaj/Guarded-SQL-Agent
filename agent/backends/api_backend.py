"""A second backend: an API agent over an in-memory store.

Same gate as the SQL side, different action type. The HTTP method is the classifier:
GET reads (auto-execute), POST/PUT/PATCH write, DELETE is destructive — writes and
deletes are held for confirmation, and a mutation's blast radius is measured by
applying it to a throwaway copy of the store (the API analogue of the SQL dry-run).

An action is an ApiCall{method, path, body}; `ApiCall.parse` reads the same structured
JSON the model would emit, so the agent drives this backend exactly like the SQL one.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from agent.backends.base import ActionClass, ActionDecision, ActionOutcome, Gate, Status

_METHOD_CLASS = {
    "GET": ActionClass.READ,
    "POST": ActionClass.WRITE,
    "PUT": ActionClass.WRITE,
    "PATCH": ActionClass.WRITE,
    "DELETE": ActionClass.DESTRUCTIVE,
}
_GATE = {
    ActionClass.READ: Gate.ALLOW,
    ActionClass.WRITE: Gate.CONFIRM,
    ActionClass.DESTRUCTIVE: Gate.CONFIRM,
    ActionClass.INVALID: Gate.BLOCK,
}


class ApiError(Exception):
    """Raised inside a (possibly throwaway) store operation, e.g. 404 / bad path."""


@dataclass
class ApiCall:
    method: str
    path: str                     # "/tickets" or "/tickets/5"
    body: dict | None = None

    @staticmethod
    def parse(raw: str) -> "ApiCall":
        obj = json.loads(raw)
        return ApiCall(str(obj["method"]).upper(), str(obj["path"]), obj.get("body"))


def _split(path: str) -> tuple[str, int | None]:
    parts = [p for p in path.strip("/").split("/") if p != ""]
    if not parts:
        raise ApiError("empty path")
    collection = parts[0]
    if len(parts) == 1:
        return collection, None
    try:
        return collection, int(parts[1])
    except ValueError as e:
        raise ApiError(f"bad id in path: {path}") from e


class ApiBackend:
    name = "api"

    def __init__(self, seed: dict | None = None):
        # {collection: {id: record}}
        self._store: dict[str, dict[int, dict]] = seed or {
            "tickets": {
                1: {"id": 1, "subject": "Login fails", "status": "open", "priority": "high"},
                2: {"id": 2, "subject": "Typo on homepage", "status": "open", "priority": "low"},
                3: {"id": 3, "subject": "Export broken", "status": "closed", "priority": "medium"},
            }
        }
        self._next_id = max((max(c) for c in self._store.values() if c), default=0) + 1

    # --- classification (the gate) -----------------------------------------
    def classify(self, action: ApiCall) -> ActionDecision:
        method = action.method.upper()
        klass = _METHOD_CLASS.get(method, ActionClass.INVALID)
        gate = _GATE[klass]
        reason = (f"{method} is a {klass.value.lower()} action"
                  if klass is not ActionClass.INVALID
                  else f"unsupported method {method!r}")
        return ActionDecision(klass, gate, reason, summary=f"{method} {action.path}")

    # --- read --------------------------------------------------------------
    def _read(self, action: ApiCall):
        collection, ident = _split(action.path)
        coll = self._store.get(collection)
        if coll is None:
            raise ApiError(f"no such collection: {collection}")
        if ident is None:
            return list(coll.values())
        if ident not in coll:
            raise ApiError(f"{collection}/{ident} not found")
        return coll[ident]

    # --- apply a mutation to a given store (real or throwaway) --------------
    def _apply(self, store: dict, action: ApiCall) -> tuple[int, object]:
        method = action.method.upper()
        collection, ident = _split(action.path)
        coll = store.get(collection)
        if coll is None:
            raise ApiError(f"no such collection: {collection}")

        if method == "POST":
            rec = dict(action.body or {})
            rec["id"] = self._next_id
            coll[self._next_id] = rec
            self._next_id += 1          # consume the id so the next POST gets a fresh one
            return 1, rec
        if method in ("PUT", "PATCH"):
            if ident is None or ident not in coll:
                raise ApiError(f"cannot update missing {collection}/{ident}")
            coll[ident].update(action.body or {})
            return 1, coll[ident]
        if method == "DELETE":
            if ident is None:                 # DELETE /collection -> wipe (big blast)
                n = len(coll)
                coll.clear()
                return n, None
            if ident not in coll:
                raise ApiError(f"cannot delete missing {collection}/{ident}")
            del coll[ident]
            return 1, None
        raise ApiError(f"unsupported method {method}")

    # --- the gated run -----------------------------------------------------
    def run(self, action: ApiCall, confirmed: bool = False) -> ActionOutcome:
        decision = self.classify(action)

        if decision.gate is Gate.BLOCK:
            return ActionOutcome(Status.BLOCKED, decision, error=decision.reason)

        if decision.gate is Gate.ALLOW:  # GET
            try:
                result = self._read(action)
            except ApiError as e:
                return ActionOutcome(Status.ERROR, decision, error=str(e))
            rc = len(result) if isinstance(result, list) else 1
            return ActionOutcome(Status.EXECUTED, decision, result=result, rowcount=rc)

        # CONFIRM path: measure blast radius on a throwaway copy, never commit unconfirmed.
        trial = copy.deepcopy(self._store)
        saved_next = self._next_id
        try:
            affected, _ = self._apply(trial, action)
        except ApiError as e:
            self._next_id = saved_next
            return ActionOutcome(Status.NEEDS_CONFIRMATION, decision, rowcount=None,
                                 error=f"dry-run could not complete: {e}")
        self._next_id = saved_next  # dry-run must not consume an id

        if not confirmed:
            return ActionOutcome(Status.NEEDS_CONFIRMATION, decision, rowcount=affected)

        affected, result = self._apply(self._store, action)  # commit for real
        return ActionOutcome(Status.EXECUTED, decision, result=result,
                             rowcount=affected, committed=True)
