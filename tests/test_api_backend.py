"""The gate must generalize: the API backend enforces the same read/confirm rules."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.backends.api_backend import ApiBackend, ApiCall
from agent.backends.base import Backend, Status
from agent.backends.sql_backend import SqlBackend


@pytest.fixture()
def api():
    return ApiBackend()


def test_both_backends_satisfy_the_interface():
    assert isinstance(ApiBackend(), Backend)
    assert isinstance(SqlBackend(), Backend)


def test_get_reads_without_confirmation(api):
    out = api.run(ApiCall("GET", "/tickets"))
    assert out.status is Status.EXECUTED
    assert out.rowcount == 3


def test_get_single_and_missing(api):
    assert api.run(ApiCall("GET", "/tickets/1")).result["subject"] == "Login fails"
    assert api.run(ApiCall("GET", "/tickets/999")).status is Status.ERROR


def test_post_needs_confirmation_then_commits(api):
    out = api.run(ApiCall("POST", "/tickets", {"subject": "New bug", "status": "open"}))
    assert out.status is Status.NEEDS_CONFIRMATION
    assert len(api._store["tickets"]) == 3           # nothing created yet

    out = api.run(ApiCall("POST", "/tickets", {"subject": "New bug"}), confirmed=True)
    assert out.status is Status.EXECUTED and out.committed
    assert len(api._store["tickets"]) == 4
    assert out.result["id"] == 4                      # dry-run did not consume the id


def test_delete_one_is_gated(api):
    out = api.run(ApiCall("DELETE", "/tickets/1"))
    assert out.status is Status.NEEDS_CONFIRMATION
    assert out.rowcount == 1
    assert 1 in api._store["tickets"]                 # still there

    api.run(ApiCall("DELETE", "/tickets/1"), confirmed=True)
    assert 1 not in api._store["tickets"]


def test_delete_whole_collection_reports_large_blast_radius(api):
    out = api.run(ApiCall("DELETE", "/tickets"))      # no id -> wipe
    assert out.status is Status.NEEDS_CONFIRMATION
    assert out.rowcount == 3                           # blast radius = all records
    assert len(api._store["tickets"]) == 3            # unconfirmed: untouched


def test_unknown_method_blocked(api):
    out = api.run(ApiCall("FETCHALL", "/tickets"))
    assert out.status is Status.BLOCKED


def test_parse_structured_action():
    call = ApiCall.parse('{"method": "delete", "path": "/tickets/2"}')
    assert call.method == "DELETE" and call.path == "/tickets/2"
