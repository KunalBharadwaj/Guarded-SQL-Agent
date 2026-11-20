"""Retry and model-fallback behaviour of the provider-agnostic client."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
from agent import llm
from agent.llm import _is_transient, with_model_fallback, with_retry


class Status429(Exception):
    status_code = 429


class APITimeoutError(Exception):   # name matches a real httpx/SDK transient class
    pass


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)   # no real backoff waits
    monkeypatch.setattr(config, "LLM_API_RETRIES", 3)


def test_is_transient_covers_status_and_connection_errors():
    assert _is_transient(Status429())
    assert _is_transient(APITimeoutError())       # no status_code, matched by class name
    assert not _is_transient(ValueError("permanent"))


def test_with_retry_retries_connection_errors_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise APITimeoutError()               # the case the old code never retried
        return "ok"

    assert with_retry(fn) == "ok"
    assert calls["n"] == 3


def test_with_retry_does_not_retry_non_transient():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        with_retry(fn)
    assert calls["n"] == 1                          # no wasted retries on a hard error


def test_model_fallback_switches_on_primary_failure():
    seen = []

    def call(model):
        seen.append(model)
        if model == "primary":
            raise RuntimeError("primary unavailable")
        return f"ran {model}"

    assert with_model_fallback("primary", "backup", call) == "ran backup"
    assert seen == ["primary", "backup"]


def test_model_fallback_reraises_without_a_distinct_fallback():
    def call(model):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        with_model_fallback("only", None, call)
    with pytest.raises(RuntimeError):
        with_model_fallback("same", "same", call)
