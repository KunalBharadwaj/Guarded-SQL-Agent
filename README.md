# Guarded NL-to-Action Agent

Turn a natural-language question into a **correct, safe** SQL action against a realistic
business database. The agent grounds on the real schema, self-corrects when a query fails,
and routes **every** action through a safety gate — reads run read-only, mutations are held
for human confirmation, and ambiguous requests escalate instead of guessing.

> **Headline:** _N% execution accuracy on held-out queries, with 100% of destructive
> operations gated behind human confirmation._ (Accuracy is filled in once an LLM key is set
> — see status below.)

## The problem

Naive "write SQL for X" prompting fails on real databases: it hallucinates columns, botches
joins, and can emit destructive statements (`DELETE`, `DROP`). That's two failures at once —
**wrong** and **dangerous**. This project fixes both:

1. **Schema grounding (RAG)** — retrieve only the relevant tables + a few examples; don't
   dump the whole schema.
2. **Execution-based self-correction** — run the query, feed the error back, let the model fix
   it (capped retries).
3. **Guardrails** — parse with `sqlglot`, classify read / write / destructive, execute
   read-only by default, require explicit confirmation for anything that writes.
4. **Confidence gating** — escalate to a human when the request is ambiguous or the blast
   radius is large.

## Architecture

```
NL request ─► Schema retriever ─► Generate SQL ─► Validate + gate ──► safe read ──► Execute
                                        ▲                         └─► needs confirm ─► Human confirm
                                        └──────── retry on error ◄── (self-correction loop)
```

Every generated action is parsed and classified **before** it runs. Reads execute on a
physically read-only connection; mutations are dry-run inside a rolled-back transaction
(verifying them and measuring blast radius) and never committed without confirmation.

## Metrics (per stage)

Model: `openai/gpt-oss-120b` via Groq (free tier). Full held-out set of 36 queries, 0 API errors.

| Stage | Strict | Lenient | Notes |
|-------|-------:|--------:|-------|
| Baseline (table names only)      | 52.8% (19/36) | 52.8% | naive prompt, the problem statement |
| Grounded (schema retrieval)      | **83.3% (30/36)** | **91.7% (33/36)** | the big accuracy lever |
| Grounded + self-correction       | 83.3% (30/36) | 91.7% (33/36) | see note |
| **Destructive-op containment**   | **100% (15/15)** | — | gate vs adversarial attacks |

**Schema grounding is the big lever: 52.8% → 83.3% (strict).**

**Two scoring modes.** *Strict* requires an exact result-set match. *Lenient* additionally
accepts an **extra-column projection** — the gold's columns appearing as a subset of the
prediction's (e.g. `SELECT *` instead of the two requested columns) — by projecting the
predicted rows onto a column subset and comparing row-for-row. It forgives only extra columns,
never wrong rows: a missing `WHERE` changes the rows and still fails (see
`tests/test_harness.py`). Three grounded misses were pure projections (now pass); the three
that remain are genuine row/value differences, not projection. Re-score any saved run offline
with `python eval/rescore.py` (no API calls).

**Self-correction** adds no lift here because the remaining misses are result *mismatches*,
not execution errors/empties — and execution-based correction only retries those. The loop's
recovery mechanism is verified directly in `tests/test_self_correction.py`.

Safety containment is fully measured: `python eval/safety_eval.py` shows no destructive op
executes unconfirmed.

Headline: **83.3% strict / 91.7% lenient execution accuracy on held-out queries, with 100% of
destructive operations gated behind human confirmation.**

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python db/build_db.py                       # build & seed db/guarded.db  (no key needed)
pytest -q                                   # safety + harness + retriever tests (no key needed)

cp .env.example .env                        # add GROQ_API_KEY (or another provider)
python eval/run_eval.py --stage baseline    # baseline accuracy
python eval/run_eval.py --stage grounded    # accuracy with schema grounding

python cli.py "how many orders were cancelled?"                               # a guarded read
python cli.py --decompose "which customers placed more orders than average?"  # multi-step
python cli.py --audit                                                         # the audit trail
```

No key handy? Lots is verifiable offline:
- `python eval/run_eval.py --stage baseline --provider oracle` — scores gold-vs-gold (100%), proving the scorer.
- `python eval/safety_eval.py` — adversarial attacks, 100% containment.
- `python eval/rescore.py` — re-grade saved predictions strict vs lenient, no API calls.
- `python demo_backends.py` — the same gate governing SQL **and** a mock API backend.

## Scope

**In:** NL→SQL over a non-trivial schema, retrieval grounding, execute-fix loop, read-only +
confirmation guardrails, execution-accuracy eval (strict + lenient), confidence/escalation,
multi-step decomposition for complex questions, a second (API) backend.

**Out:** training a text-to-SQL model; supporting every SQL dialect; a full BI tool.

## Layout

```
agent/   retriever, generator, guardrails, executor, self-correction, decomposition, confidence, audit, orchestrator, llm, backends/
db/      schema.sql, seed.py, schema_cards.json (RAG corpus), build_db.py
eval/    gold.jsonl, adversarial.jsonl, harness.py, run_eval.py
results/ per-stage reports + audit log
tests/   guardrails / harness / executor / retriever
```

## Status — all phases complete

- **Phase 0 (baseline + eval harness):** done. Baseline 52.8%.
- **Phase 1 (schema grounding):** done. **52.8% → 83.3%** on the full held-out set.
- **Phase 2 (self-correction + guardrails):** done — guardrails, executor, self-correction
  loop, audit log, orchestrator, adversarial safety eval (**100% containment**).
- **Phase 3 (confidence + second backend):** done — confidence gating + escalation, CLI with
  live audit trail, multi-step decomposition for complex questions, and a second (API) backend
  proving the gate isn't SQL-specific.

63 tests pass (`pytest -q`). Numbers above are from a clean full-set run on Groq's free tier.
The only unbuilt stretch item is a browser chat UI (the CLI covers the same flow).
