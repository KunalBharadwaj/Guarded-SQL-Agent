# Guarded SQL Agent

A natural-language-to-SQL agent for an uncooperative production database. It grounds
generation in the real schema, repairs failed queries from execution feedback, and routes
every generated statement through a safety gate: reads run read-only, mutations are withheld
for explicit human confirmation, and ambiguous requests are deferred rather than guessed.

The design targets two independent guarantees: competitive execution accuracy, and zero
unconfirmed destructive operations.

## Problem

Single-shot "generate SQL for X" prompting degrades sharply on a non-trivial schema. It
hallucinates columns, mis-specifies joins, and will readily emit `DELETE` / `DROP` / `UPDATE`
against live data. Those are two distinct failure modes, correctness and safety, and each has
to be handled on its own terms.

Four mechanisms, ordered by leverage:

1. **Schema grounding (retrieval).** The model cannot know the schema a priori, so the
   relevant tables, columns, and a few exemplar queries are retrieved into the prompt rather
   than dumping the full DDL. This is the dominant accuracy lever and the cheapest to add.
2. **Execution-based self-correction.** Execute the candidate, capture the failure (a SQL
   error or an empty result), and feed it back for a bounded number of repair attempts.
3. **Guardrails.** Parse each statement with `sqlglot`, classify it at the AST level as
   read / write / destructive, execute reads through a read-only connection, and require
   explicit confirmation for anything that mutates.
4. **Confidence gating.** When retrieval similarity is low (an ambiguous request) or a
   mutation's blast radius is large, escalate to a human instead of executing.

## Architecture

```
NL request ─► schema retriever ─► generate SQL ─► classify + gate ──► read  ──► execute (read-only)
                                       ▲                          └──► mutation ─► dry-run ─► human confirm
                                       └──────── execution error ◄──── self-correction loop
```

Classification precedes execution unconditionally. Reads run on a physically read-only SQLite
connection, so writes are rejected by the driver independently of the classifier. Mutations
are validated by a transactional dry-run that is rolled back, which both confirms
executability and measures blast radius, and are never committed without an explicit
confirmation flag. The result is defense in depth: a misclassified statement still cannot
mutate state on the read path.

## Results

Model `openai/gpt-oss-120b` (Groq, free tier). Evaluation set of 36 hand-written
question/gold-SQL pairs over a ten-table schema (8 easy, 15 medium, 13 hard). One full-set run.

| Stage | Strict | Lenient |
|-------|-------:|--------:|
| Baseline (table names only) | 55.6% (20/36) | 58.3% (21/36) |
| Grounded (schema retrieval) | 86.1% (31/36) | 91.7% (33/36) |
| Grounded + self-correction  | 86.1% (31/36) | 94.4% (34/36) |
| Safety — gate layer (raw SQL)        | 100% (15/15) | — |
| Safety — agent layer (NL injection)  | 100% (10/10) | — |

Retrieval grounding is the decisive lever, +30.5 points strict over the baseline.

The set is small, so treat these as indicative, not precise: the 95% confidence interval on the
grounded 31/36 is roughly **[71%, 94%]** (Wilson). The provider is not bit-deterministic even at
temperature 0, so a given query's prediction can vary run to run by a few items; the stage
numbers are a single run, not a converged average.

**No train/test leakage.** The few-shot examples in `db/schema_cards.json` are kept disjoint
from the evaluation set in both question and SQL, and deliberately withhold the constructs the
hard gold queries hinge on (the revenue formula, month bucketing, the reorder-level join, the
never-reviewed/never-ordered anti-joins, the manager self-join). An earlier pool overlapped the
gold set; removing it did not lower the score (it moved within run-to-run noise), which is itself
the evidence that the grounding lift is real rather than memorised.

**Scoring.** Strict scoring requires result-set equivalence: the row multiset must match,
order-sensitive only when the gold query is itself ordered. Lenient scoring additionally
tolerates over-projection by accepting a prediction when the gold columns appear as an ordered
sub-tuple of the predicted rows. It forgives `SELECT *` in place of two named columns while still
rejecting an incorrect row set, since a missing predicate or a bad join changes the projected
tuples. `python eval/rescore.py` re-grades saved runs under both schemes offline.

**Self-correction** shows no strict lift on this run: grounded and grounded+loop both land at
31/36. The residual failures are semantic result mismatches, not execution errors or empty
results, and an execution-driven loop can only act on the latter. The repair path is exercised
directly in `tests/test_self_correction.py`; it is kept because it is cheap insurance against
malformed queries, not because it moves this particular number.

**Safety is measured at two levels** (`eval/safety_eval.py`):

- *Gate layer* feeds raw attacker SQL straight to the executor and confirms the sqlglot gate
  never executes it unconfirmed (15/15). This tests the classifier, not the model.
- *Agent layer* is the end-to-end test: ten natural-language injection prompts go through the
  whole agent. In this run the model actually emitted the destructive SQL for eight of them
  (DROP / DELETE / UPDATE); every one was held for confirmation, one escalated, one degraded to
  a harmless read, and the database was byte-for-byte unchanged (10/10 contained).

Containment is deterministic regardless of model output: `tests/test_safety_e2e.py` drives the
full pipeline with a client that behaves as if an injection always succeeds and asserts nothing
commits.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python db/build_db.py                     # build and seed db/guarded.db (no key needed)
pytest -q                                  # 80 tests (no key needed)

cp .env.example .env                       # set GROQ_API_KEY, or another provider
python eval/run_eval.py --stage baseline
python eval/run_eval.py --stage grounded

python cli.py "how many orders were cancelled?"                               # guarded read
python cli.py --decompose "which customers placed more orders than average?"  # multi-step
python cli.py --audit                                                         # audit trail
```

Much of the system is verifiable with no API key:

- `python eval/run_eval.py --stage baseline --provider oracle` grades the gold set against
  itself (100%), validating the scorer end to end.
- `python eval/safety_eval.py` runs the gate-layer suite (and the live agent layer when a key
  is set) and reports containment at both levels.
- `python eval/rescore.py` re-grades saved predictions under strict and lenient scoring.
- `python demo_backends.py` shows the same gate governing both the SQL backend and a mock API.

## Providers

The LLM client is provider-agnostic over an OpenAI-compatible surface. Groq, Cerebras,
OpenRouter, OpenAI, and Anthropic are supported. The active provider is set by
`GUARDED_LLM_PROVIDER` or inferred from whichever API key is present; transient 429 and 5xx
responses are retried with exponential backoff.

## Layout

```
agent/   llm, retriever, generator, guardrails, executor, self_correction,
         decomposition, confidence, audit, orchestrator, backends/{base,sql,api}
db/      schema.sql, seed.py, build_db.py, schema_cards.json (retrieval corpus)
eval/    harness.py, run_eval.py, rescore.py, safety_eval.py,
         gold.jsonl, adversarial.jsonl (SQL), adversarial_nl.jsonl (NL injection)
tests/   harness, guardrails, executor, retriever, self_correction, orchestrator,
         api_backend, decomposition, llm, run_eval, safety_e2e  (80 tests)
results/ per-stage reports and the demo audit log
```

## Scope

In scope: NL-to-SQL over a non-trivial schema, retrieval grounding, the self-correction loop,
read-only and confirmation guardrails, execution-accuracy evaluation (strict and lenient),
confidence-based escalation, multi-step decomposition, and a second backend (a mock API) to
show the gate is not SQL-specific.

Out of scope: training a text-to-SQL model, supporting every SQL dialect, and building a full
BI tool.
