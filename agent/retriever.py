"""Schema grounding via retrieval.

For each request we retrieve only the *relevant* table cards and a couple of
few-shot examples, instead of dumping the whole schema (context bloat lowers
accuracy). Default backend is lightweight TF-IDF (no API key, no torch); set
GUARDED_EMBEDDINGS=sentence-transformers to use embeddings instead.

`top_score()` exposes the best similarity so Phase-3 confidence gating can treat a
request that matches nothing as ambiguous.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import config


def _table_doc(card: dict) -> str:
    cols = " ".join(f"{c['name']} {c.get('desc','')}" for c in card["columns"])
    joins = " ".join(card.get("joins", []))
    return f"{card['table']} {card['description']} {cols} {joins}"


def _format_table(card: dict) -> str:
    lines = [f"TABLE {card['table']} — {card['description']}"]
    for c in card["columns"]:
        lines.append(f"  - {c['name']} ({c['type']}): {c.get('desc','')}")
    if card.get("joins"):
        lines.append("  joins: " + "; ".join(card["joins"]))
    return "\n".join(lines)


@dataclass
class Retrieved:
    tables: list[dict]
    examples: list[dict]
    top_score: float


class _TfidfBackend:
    name = "tfidf"

    def __init__(self, docs: list[str]):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vec = TfidfVectorizer(stop_words="english")
        self._matrix = self._vec.fit_transform(docs)

    def scores(self, query: str) -> np.ndarray:
        from sklearn.metrics.pairwise import cosine_similarity

        q = self._vec.transform([query])
        return cosine_similarity(q, self._matrix)[0]


class _EmbeddingBackend:
    name = "sentence-transformers"

    def __init__(self, docs: list[str], model: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)
        self._emb = self._model.encode(docs, normalize_embeddings=True)

    def scores(self, query: str) -> np.ndarray:
        q = self._model.encode([query], normalize_embeddings=True)
        return (self._emb @ q[0])


def _make_backend(docs: list[str]):
    if os.getenv("GUARDED_EMBEDDINGS", "tfidf").lower().startswith("sentence"):
        try:
            return _EmbeddingBackend(docs)
        except Exception:  # noqa: BLE001 — fall back to TF-IDF if torch unavailable
            pass
    return _TfidfBackend(docs)


class SchemaRetriever:
    def __init__(self, cards_path: str | Path | None = None,
                 top_k: int | None = None, num_fewshot: int | None = None):
        data = json.loads(Path(cards_path or config.SCHEMA_CARDS).read_text())
        self.cards = data["tables"]
        self.examples = data.get("examples", [])
        self.top_k = top_k or config.TOP_K_TABLES
        self.num_fewshot = num_fewshot if num_fewshot is not None else config.NUM_FEWSHOT

        self._table_backend = _make_backend([_table_doc(c) for c in self.cards])
        self._ex_backend = (
            _make_backend([e["question"] for e in self.examples]) if self.examples else None
        )

    def retrieve(self, question: str) -> Retrieved:
        tscores = self._table_backend.scores(question)
        order = np.argsort(tscores)[::-1][: self.top_k]
        tables = [self.cards[i] for i in order]
        top = float(tscores[order[0]]) if len(order) else 0.0

        examples = []
        if self._ex_backend is not None and self.num_fewshot:
            escores = self._ex_backend.scores(question)
            eorder = np.argsort(escores)[::-1][: self.num_fewshot]
            examples = [self.examples[i] for i in eorder]

        return Retrieved(tables=tables, examples=examples, top_score=top)

    def context_for(self, question: str) -> str:
        r = self.retrieve(question)
        parts = [_format_table(c) for c in r.tables]
        if r.examples:
            parts.append("\nExample questions and queries:")
            for e in r.examples:
                parts.append(f"  Q: {e['question']}\n  SQL: {e['sql']}")
        return "\n".join(parts)

    def top_score(self, question: str) -> float:
        return self.retrieve(question).top_score
