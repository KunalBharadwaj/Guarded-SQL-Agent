"""Same gate, two backends — proof the guardrail pattern isn't SQL-specific.

Runs scripted actions (no LLM needed) through the SQL backend and the API backend and
prints how the identical classify -> gate logic routes each one.

    python demo_backends.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.backends.api_backend import ApiBackend, ApiCall  # noqa: E402
from agent.backends.base import Status  # noqa: E402
from agent.backends.sql_backend import SqlBackend  # noqa: E402

_ICON = {Status.EXECUTED: "✓ ran", Status.NEEDS_CONFIRMATION: "⏸ confirm",
         Status.BLOCKED: "⛔ blocked", Status.ERROR: "✗ error"}


def show(backend, action, label):
    out = backend.run(action)                     # confirmed=False, as the agent would
    cls = out.decision.action_class.value
    # Blast radius only makes sense for a held mutation.
    rc = (f"  (blast radius: {out.rowcount})"
          if out.status is Status.NEEDS_CONFIRMATION and out.rowcount is not None else "")
    print(f"  {_ICON[out.status]:<11} [{cls:<11}] {label}{rc}")


def main():
    print("SQL backend (guarded.db) — same gate:")
    sql = SqlBackend()
    show(sql, "SELECT COUNT(*) FROM customers", "count customers")
    show(sql, "UPDATE customers SET is_active = 0 WHERE country = 'UK'", "deactivate UK customers")
    show(sql, "DROP TABLE orders", "drop orders table")
    show(sql, "SELECT 1; DROP TABLE customers", "stacked injection")

    print("\nAPI backend (in-memory tickets) — same gate:")
    api = ApiBackend()
    show(api, ApiCall("GET", "/tickets"), "list tickets")
    show(api, ApiCall("POST", "/tickets", {"subject": "x"}), "create a ticket")
    show(api, ApiCall("DELETE", "/tickets/1"), "delete ticket #1")
    show(api, ApiCall("DELETE", "/tickets"), "delete ALL tickets")
    show(api, ApiCall("FETCHALL", "/tickets"), "unknown method")

    print("\nReads auto-run; writes/deletes are held for confirmation; "
          "injections/unknown actions are blocked — on both backends.")


if __name__ == "__main__":
    main()
