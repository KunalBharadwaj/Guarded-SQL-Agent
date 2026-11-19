"""Append-only audit log.

Every action the agent considers — read, blocked injection, gated mutation,
escalation — is recorded with its classification and what happened. This is both
a safety feature (a tamper-evident trail of what ran) and the single best thing to
show in the demo.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config


def record(entry: dict, path: Path | None = None) -> dict:
    """Append one audit entry (a timestamp is added) and return it."""
    path = path or config.AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_all(path: Path | None = None) -> list[dict]:
    path = path or config.AUDIT_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
