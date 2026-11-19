"""Grounding must surface the tables a question actually needs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.retriever import SchemaRetriever


@pytest.fixture(scope="module")
def retriever():
    return SchemaRetriever()


@pytest.mark.parametrize("question,expected", [
    ("Which products are below their reorder level?", "inventory"),
    ("What is the average star rating per product?", "reviews"),
    ("How many payments were refunded?", "payments"),
    ("List each supplier's country.", "suppliers"),
    ("Who is each employee's manager?", "employees"),
])
def test_relevant_table_in_topk(retriever, question, expected):
    tables = [c["table"] for c in retriever.retrieve(question).tables]
    assert expected in tables, f"{expected} not in top-{retriever.top_k}: {tables}"


def test_context_is_compact(retriever):
    # Grounding retrieves a subset, not the whole 10-table schema.
    ctx = retriever.context_for("How many orders were cancelled?")
    assert ctx.count("TABLE ") <= retriever.top_k
    assert "orders" in ctx


def test_unrelated_question_has_low_score(retriever):
    # A question about nothing in the schema should score low (feeds Phase-3 gating).
    assert retriever.top_score("what is the weather on mars tomorrow") < 0.25
