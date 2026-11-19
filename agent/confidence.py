"""Confidence gating — knowing when to defer.

The agent escalates instead of answering when:
  - the request barely matches the schema (low retrieval similarity => ambiguous),
  - the self-correction loop never produced a working query, or
  - a mutation's blast radius is large (handled in the orchestrator via the dry-run
    row count).

Deferring on low confidence — rather than guessing — is the FDE judgment signal.
"""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class Assessment:
    score: float          # 0..1 rough confidence
    escalate: bool
    reason: str
    clarify: str | None = None   # a question to put back to the human


def assess_query(top_score: float, loop_success: bool, attempts: int) -> Assessment:
    """Confidence for a read/query action."""
    if top_score < config.CONFIDENCE_THRESHOLD:
        return Assessment(
            score=round(top_score, 3), escalate=True,
            reason="request does not clearly match the known schema (ambiguous)",
            clarify="I'm not sure which tables this refers to. Which part of the "
                    "data do you mean (e.g. customers, orders, products, payments)?",
        )
    if not loop_success:
        return Assessment(
            score=0.2, escalate=True,
            reason="could not produce a working query within the retry budget",
            clarify="I couldn't build a reliable query for this. Could you rephrase "
                    "or add detail (which fields, filters, or time range)?",
        )
    # Confidence decays with each extra self-correction attempt.
    score = max(0.0, 1.0 - 0.2 * (attempts - 1)) * min(1.0, top_score / 0.5)
    return Assessment(score=round(score, 3), escalate=False, reason="confident")


def large_blast_radius(rowcount: int | None) -> bool:
    return rowcount is not None and rowcount > config.BLAST_RADIUS_ROWS
