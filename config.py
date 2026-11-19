"""Central configuration for the Guarded NL-to-Action Agent.

Values can be overridden via environment variables (prefixed GUARDED_) so the
agent is reproducible without editing code. Loaded once at import time.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv optional; env vars still work without it
    pass

# --- Paths ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("GUARDED_DB_PATH", ROOT / "db" / "guarded.db"))
RESULTS_DIR = ROOT / "results"
SCHEMA_SQL = ROOT / "db" / "schema.sql"
SCHEMA_CARDS = ROOT / "db" / "schema_cards.json"
GOLD_PATH = ROOT / "eval" / "gold.jsonl"
ADVERSARIAL_PATH = ROOT / "eval" / "adversarial.jsonl"
AUDIT_LOG = RESULTS_DIR / "audit.jsonl"

# --- LLM --------------------------------------------------------------------
# Provider is chosen at build time from whichever key is present (anthropic
# preferred), unless GUARDED_LLM_PROVIDER forces one.
LLM_PROVIDER = os.getenv("GUARDED_LLM_PROVIDER")  # None => auto-detect
ANTHROPIC_MODEL = os.getenv("GUARDED_ANTHROPIC_MODEL", "claude-opus-5-5")
ANTHROPIC_FALLBACK_MODEL = os.getenv("GUARDED_ANTHROPIC_FALLBACK", "claude-sonnet-5-5")
OPENAI_MODEL = os.getenv("GUARDED_OPENAI_MODEL", "gpt-4o")
# OpenRouter is OpenAI-compatible: same SDK, custom base URL + key. Model slugs are
# namespaced, e.g. "anthropic/claude-sonnet-4.5", "openai/gpt-4o", "google/gemini-2.0-flash".
OPENROUTER_MODEL = os.getenv("GUARDED_OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_BASE_URL = os.getenv("GUARDED_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# Cerebras is also OpenAI-compatible. Self-serve models: "gpt-oss-120b", "qwen-3.8-27b".
CEREBRAS_MODEL = os.getenv("GUARDED_CEREBRAS_MODEL", "gpt-oss-120b")
CEREBRAS_BASE_URL = os.getenv("GUARDED_CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
# Groq is OpenAI-compatible. Production models: "llama-3.3-70b-versatile", "openai/gpt-oss-120b".
GROQ_MODEL = os.getenv("GUARDED_GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = os.getenv("GUARDED_GROQ_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MAX_TOKENS = int(os.getenv("GUARDED_LLM_MAX_TOKENS", "512"))
LLM_TEMPERATURE = float(os.getenv("GUARDED_LLM_TEMPERATURE", "0.0"))
# API-level retry with exponential backoff (handles 429 rate limits and
# OpenRouter 402 in-flight credit-reservation hiccups). Distinct from the
# self-correction retries in MAX_RETRIES.
LLM_API_RETRIES = int(os.getenv("GUARDED_LLM_API_RETRIES", "5"))
LLM_BACKOFF_BASE = float(os.getenv("GUARDED_LLM_BACKOFF_BASE", "1.5"))

# --- Retrieval (Phase 1) ----------------------------------------------------
TOP_K_TABLES = int(os.getenv("GUARDED_TOP_K", "5"))
NUM_FEWSHOT = int(os.getenv("GUARDED_NUM_FEWSHOT", "2"))

# --- Self-correction (Phase 2) ---------------------------------------------
MAX_RETRIES = int(os.getenv("GUARDED_MAX_RETRIES", "3"))

# --- Confidence gating (Phase 3) -------------------------------------------
# Below this retrieval similarity the request is treated as ambiguous and escalated.
CONFIDENCE_THRESHOLD = float(os.getenv("GUARDED_CONFIDENCE_THRESHOLD", "0.25"))
# A mutation touching more rows than this is "large blast radius" -> escalate.
BLAST_RADIUS_ROWS = int(os.getenv("GUARDED_BLAST_RADIUS", "100"))

RESULTS_DIR.mkdir(exist_ok=True)
