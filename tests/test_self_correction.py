"""The self-correction loop must recover from an erroring query within the retry cap.

(On the live eval the grounded queries rarely *error* — the remaining misses are
wrong-column result mismatches, which execution-based correction can't see — so this
offline test is what demonstrates the loop actually fixing a broken query.)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.retriever import SchemaRetriever
from agent.self_correction import correct_loop


class SequenceClient:
    """Returns a scripted SQL per call, so we can stage a failure then a fix."""
    name = "seq"

    def __init__(self, sqls):
        self._sqls = list(sqls)
        self._i = 0

    def complete(self, system, user):
        sql = self._sqls[min(self._i, len(self._sqls) - 1)]
        self._i += 1
        return json.dumps({"sql": sql, "rationale": ""})


def test_loop_fixes_an_erroring_query():
    client = SequenceClient([
        "SELECT no_such_column FROM customers",   # attempt 1: errors
        "SELECT COUNT(*) FROM customers",          # attempt 2: valid, non-empty
    ])
    res = correct_loop(client, "how many customers are there", SchemaRetriever())
    assert res.success is True
    assert res.attempts == 2
    assert len(res.history) == 1                   # one failed attempt recorded


def test_loop_gives_up_after_cap_and_reports_failure():
    client = SequenceClient(["SELECT oops FROM nope"])  # always errors
    res = correct_loop(client, "how many customers are there", SchemaRetriever(),
                       max_retries=2)
    assert res.success is False
    assert res.attempts == 3                        # initial + 2 retries
    assert len(res.history) == 3


def test_loop_does_not_execute_a_mutation():
    client = SequenceClient(["DELETE FROM customers"])
    res = correct_loop(client, "remove all the customers from customers", SchemaRetriever())
    assert res.is_read is False
    assert res.success is False                     # handed to the gate, not run
