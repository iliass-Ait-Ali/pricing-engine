# 22 — Post-freeze engineering (Phase O)

**Date:** Tuesday, 2026-08-25 (implementation and verification)
**Committed:** Thursday, 2026-08-27
**Scope:** engineering only. No estimator, model, feature, guardrail,
decision rule or scientific claim was changed. v1.0.0's frozen scientific
architecture is untouched; this phase only adds tooling around it.
**Outcome:** three items from `FUTURE_WORK.md` §3 ("Product and operations —
no new science") implemented: a recommendation review/approval workflow,
monitoring metric history + local threshold alerting, and a batch
scale-testing harness. Two related items were deliberately **not**
attempted: a real cost feed (no external data source exists for this
project) and staffing the `REVIEW_REQUIRED` queue (an operational/HR
decision, not code). No GitHub remote was created or pushed to.

This is the evidence log for the phase. Every result below is the output of
the command shown, run in this repository on the real Dominick's Cereals
panel.

---

## 1. Starting state

| item | state at the start of the phase |
| --- | --- |
| tests | `python -m pytest` → 519 passed |
| lint | `python -m ruff check .` → All checks passed |
| `artifacts/recommendation_log.csv` | 6,005 rows, no `rec_id` column (pre-Phase-O schema) |
| `artifacts/metrics/monitoring.json` | a single overwritten snapshot, no history file |
| `artifacts/metrics/batch_performance.json` | one fixed-size measurement (3,000 contexts) |

---

## 2. Recommendation review/approval workflow

### 2.1 Design

`src/pricing_engine/audit.py`'s `RecommendationLog` already declared the
intended lifecycle (`GENERATED -> REVIEWED -> APPROVED/REJECTED ->
PUBLISHED`) but nothing ever transitioned past `GENERATED`, and there was no
row identifier to transition. Two decisions were made explicitly with the
user before implementation:

* **Row key**: add a `rec_id` column (12-hex-char `uuid4`) rather than a
  composite natural key — simpler for a CLI/UI, at the cost of a one-time
  schema change (§2.2).
* **Transition storage**: `recommendation_log.csv` is never rewritten after
  it is written — a new, also append-only, `RecommendationTransitions` class
  (`recommendation_transitions.csv`) records every transition event, and
  "current state" is a read-time join (`RecommendationLog.read_with_state`).

The state machine (`TRANSITIONS` in `audit.py`) rejects illegal edges
(`GENERATED -> PUBLISHED`, or anything out of `REJECTED`/`PUBLISHED`, which
are terminal) by raising `ValueError` — a review decision either takes
effect or fails loudly, never a silent no-op.

### 2.2 The one-time schema archival (expected, disclosed)

Adding `rec_id` to `LOG_COLUMNS` changed the log's schema, which triggers the
log's own pre-existing `_rotate_if_schema_changed()` behaviour (built at
Phase M for exactly this situation) on the next `append()`. Running
`python scripts/optimize.py --batch 3000 --profile standard` after this
change produced:

```
recommendation schema changed; previous log archived to recommendation_log__schema_20260825T115810.csv
appended 3,000 rows to artifacts/recommendation_log.csv
```

The real, pre-Phase-O log (6,005 rows) was archived — not deleted — to
`artifacts/recommendation_log__schema_20260825T115810.csv`; the new
`recommendation_log.csv` holds the 3,000 fresh rows from that run. This was
anticipated and agreed with the user before implementation, not an incident.

Before this rotation happens, reading the pre-existing (old-schema) log
must not crash: `RecommendationLog.read_with_state()` and
`scripts/review.py --list` were both verified against the real file in that
intermediate state (rows shown with `rec_id = <NA>`, all reported
`GENERATED`, none individually transitionable) before the rotation was
triggered.

### 2.3 Real end-to-end walk-through

CLI, on one real `REVIEW_REQUIRED` recommendation:

```
$ python scripts/review.py --transition 3bec65f82d56 --to REVIEWED --actor alice
3bec65f82d56: GENERATED -> REVIEWED (actor='alice')
$ python scripts/review.py --approve 3bec65f82d56 --actor alice
3bec65f82d56: REVIEWED -> APPROVED (actor='alice')
$ python scripts/review.py --publish 3bec65f82d56 --actor alice
3bec65f82d56: APPROVED -> PUBLISHED (actor='alice')
$ python scripts/review.py --transition 3bec65f82d56 --to REVIEWED --actor alice
ERROR: Illegal transition 'PUBLISHED' -> 'REVIEWED' for rec_id '3bec65f82d56'; allowed from 'PUBLISHED': []
$ echo $?
1
```

Dashboard, via Streamlit's `AppTest` harness on the "Review queue" page:
selecting a real pending `rec_id` and clicking "Mark reviewed" wrote a new
row to `recommendation_transitions.csv` with no exception raised. The
resulting file (excerpt):

```
transitioned_at_utc,rec_id,from_state,to_state,actor,note
2026-08-25T11:58:21+00:00,3bec65f82d56,GENERATED,REVIEWED,alice,
2026-08-25T11:58:22+00:00,3bec65f82d56,REVIEWED,APPROVED,alice,
2026-08-25T11:58:24+00:00,3bec65f82d56,APPROVED,PUBLISHED,alice,
2026-08-25T12:40:17+00:00,4b8854530da1,GENERATED,REVIEWED,,
```

`scripts/review.py --list` after these transitions reports **2,999** pending
of 3,000 (one `PUBLISHED`, correctly excluded).

### 2.4 Tests

```
$ pytest tests/test_review_workflow.py -q
.........                                                                [100%]
```

9 passed: unique `rec_id` per row; a full valid transition sequence reaches
`PUBLISHED`; an invalid jump raises; `REJECTED` is terminal; transitioning
one `rec_id` never touches another's state; transition history is recorded
correctly; an unknown `rec_id` raises when validated; a fresh log with no
transitions defaults every row to `GENERATED`; a pre-`rec_id` log on disk is
still readable (§2.2). `tests/test_integration.py` and
`tests/test_decision_states.py`, which read `lifecycle_state` (the
append-time column, left untouched by this change), were re-run and are
unaffected.

---

## 3. Monitoring: metric history + local threshold alerting

### 3.1 Design

`src/pricing_engine/monitoring/drift.py` computed drift/schema/prediction/
performance numbers but never compared them to anything, and
`scripts/monitor.py` overwrote a single `monitoring.json` snapshot each run.
Two additions:

* `configs/config.yaml` gained a `monitoring:` block: `psi_bands` (replacing
  the hardcoded `PSI_BANDS` constant as the source of truth), `alerts`
  (PSI/WAPE/bias thresholds), and `expected_schema` (replacing the hardcoded
  dict in `scripts/monitor.py`). These thresholds are **loosely calibrated
  against this project's own already-reported numbers (test WAPE 0.4565,
  bias −3.88), not agreed with a business or fitted against realised
  out-of-sample error** — the same honest caveat `MONITORING_DESIGN.md`
  already carried before this phase.
* `evaluate_alerts(payload, thresholds) -> AlertResult`: a pure comparison
  function (no I/O) that flags a hard schema failure, a prediction-drift PSI
  breach, or a per-quarter WAPE/bias breach. `append_history(path, record)`
  appends one JSON line to a JSONL file.

`scripts/monitor.py` now writes both the existing overwritten
`monitoring.json` snapshot (backward compatible) **and** appends to
`artifacts/metrics/monitoring_history.jsonl`, and exits non-zero when an
alert fires — no metric-store database, no scheduling, no paging.

### 3.2 Real runs

```
$ python scripts/monitor.py
reference weeks 2-257 (3,206,437 rows), current weeks 343-399 (749,040 rows)
alerts: PASS (12 checked, 0 breached)
wrote reports/MONITORING_DESIGN.md
$ echo $?
0
```

Run a second time to confirm the history file accumulates rather than being
overwritten:

```
$ wc -l artifacts/metrics/monitoring_history.jsonl
2 artifacts/metrics/monitoring_history.jsonl
```

Two independent, valid JSON lines, each with its own `run_at_utc` timestamp
and its own `alerts` block — nothing from the first run was lost by the
second.

### 3.3 Tests

```
$ pytest tests/test_monitoring.py -q
................                                                         [100%]
```

16 passed (9 pre-existing + 7 new): custom PSI bands change the reported
band; `evaluate_alerts` passes within threshold, flags a PSI breach, flags a
WAPE/bias breach, hard-fails on a schema break, and correctly does *not*
hard-fail when that flag is turned off; `append_history` grows a JSONL file
across repeated calls without disturbing earlier lines.

---

## 4. Batch scale-testing harness

### 4.1 Design

`scripts/benchmark_batch.py` measures `optimize_price_batch` at one fixed
size (3,000 contexts) and proves it equivalent to the per-context loop — that
equivalence check is not repeated here. `scripts/benchmark_scale.py` instead
sweeps `optimize_price_batch` alone across requested context counts, built by
tiling (replicating) the same real sampled base pool up to the requested
size — contexts above the base size are **the same real contexts repeated,
not independent new ones or catalogue growth**, which is stated in both the
JSON `caveat` field and here. `peak_process_memory_mb()` (Windows/POSIX) was
extracted from `benchmark_batch.py` into `pricing_engine.utils.io` so both
scripts share one implementation instead of duplicating it.

### 4.2 Real run

```
$ python scripts/benchmark_scale.py --sizes 500,3000,10000,30000
decision week 399: base pool 3,000 real contexts, sweeping sizes [500, 3000, 10000, 30000]
  n=    500 (x  1 replication) |    1.29s |     386.9 ctx/s | peak 4602.4 MB
  n=  3,000 (x  1 replication) |    3.88s |     772.3 ctx/s | peak 4602.4 MB
  n= 10,000 (x  4 replication) |    9.95s |   1,004.8 ctx/s | peak 4602.4 MB
  n= 30,000 (x 10 replication) |   33.88s |     885.6 ctx/s | peak 4602.4 MB
wrote artifacts/metrics/batch_scale_benchmark.json
```

Throughput is not monotone (772 → 1,005 → 886 ctx/s) — this is a single-run,
single-process wall-clock measurement subject to ordinary noise (grid-width
grouping, JIT/cache warmup, OS scheduling), not a claim of a stable
throughput curve. Peak process memory is flat across sizes on this run,
consistent with the batch path holding one grid-chunked working set at a
time (`max_simulation_rows`) rather than materialising every context's full
simulation grid at once.

### 4.3 Tests

```
$ pytest tests/test_batch_scale_benchmark.py -q
...                                                                       [100%]
```

3 passed, on a small deterministic synthetic fixture (no real dataset
needed): the sweep returns exactly one result per requested size; each
size's context frame is exactly `n` rows after tiling (not the base pool
size) — checked with a `monkeypatch` spy on `optimize_price_batch`'s actual
call; and every timing is non-negative (loosely — strict monotonicity across
tiny fixture sizes was deliberately not asserted, since wall-clock noise
would make that flaky, matching what §4.2 shows on the real run too).

---

## 5. Full verification

```
$ python -m ruff check .
All checks passed!
$ python -m pytest -q
537 passed, 1 failed (tests/test_metric_consistency.py - see 5.1 below)
$ python scripts/smoke_dashboard.py
All 10 dashboard pages rendered without exceptions.
```

(538 = 519 pre-Phase-O + 19 new: 9 in `test_review_workflow.py`, 7 new in
`test_monitoring.py` (9 pre-existing + 7 new = 16 collected there), and 3 in
`test_batch_scale_benchmark.py`.) `scripts/update_readme_metrics.py
--refresh-tests` was run once at the end of the phase so the README's
generated test count matches this live collection.

### 5.1 Cross-document consistency cascade (an unplanned but necessary consequence)

Refreshing the live test count from 519 to 538 tripped a second, pre-existing,
whole-repo check this phase had not originally planned for:
`tests/test_metric_consistency.py` (built at Phase N) fails the build if any
hand-authored document states a metric that contradicts the artifact that owns
it. 26 "519"-mentions across `STATUS.md`, `ROADMAP.md`,
`docs/TECHNICAL_DESIGN.md`, `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md`
and `reports/VALIDATION_SUMMARY.md` were flagged. This was surfaced to the
user rather than resolved unilaterally, because two of the three options
carried real, different tradeoffs (rewrite frozen historical evidence to a
number that would misstate what those phases actually found, vs. leave a
known test failure, vs. footnote what is genuinely historical and update what
is genuinely live) - the user chose the third.

Applied:

* `STATUS.md` and `docs/TECHNICAL_DESIGN.md` - genuinely live/current-state
  mentions - updated to **538**.
* `ROADMAP.md`'s Phase N summary row, `reports/VALIDATION_SUMMARY.md` (a
  historical record of the Phase N freeze gauntlet, pinned to a specific
  verified commit) and `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md`
  (the frozen v1.0 portfolio deliverable, whose DOCX/PDF regeneration pipeline
  needs `pandoc`, not installed in this environment, and was out of scope for
  this engineering-only phase) - given an explicit **"at Phase N"** /
  **"519 tests at Phase N"** qualifier at each of 25 locations, matching this
  project's own pre-existing convention (`ROADMAP.md`'s Phase K row already
  reads "149 passed *(at Phase K...)*" for exactly this reason). The number
  519 is not wrong in any of these - it is what Phase N's gauntlet actually
  found - so it was qualified, not overwritten.
* **One genuine exception remains, disclosed rather than worked around**:
  `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:7405` is a literal,
  verbatim console transcript (`$ python -m pytest` followed by the captured
  output line `519 passed in 7.05s`) from the Phase N freeze run. Editing text
  *inside* a captured terminal
  transcript to insert a qualifier would fabricate tool output that pytest
  never printed - a worse defect than a known, disclosed gap. This one line
  is left exactly as it was captured. As a direct result,
  `pytest tests/test_metric_consistency.py` (and therefore an unrestricted
  `python -m pytest` run) has **one known, disclosed, pre-existing-style
  failure** at the end of this phase, tracked here the same way the
  never-executed GitHub-hosted CI run is tracked in `STATUS.md` open issue 10
  - named rather than silently masked.

---

## 6. What did **not** change

* **No new modelling method, estimator, feature or guardrail.** The pricing
  engine's recommendations for a given context are byte-for-byte identical
  before and after this phase — nothing in `src/pricing_engine/optimization/`,
  `models/`, `economics/`, or `simulation/` was touched.
* **No guardrail was loosened**, no policy profile changed, no risk
  threshold changed.
* **The frozen v1.0.0 classification is unchanged.** This phase adds
  operational tooling around the frozen system; it does not reopen or
  re-evaluate the scientific claims closed at Phase N.
* **No causal or realised-uplift claim was added.** Every uplift figure
  anywhere in this project remains labelled model-internal estimated.
* **Monitoring alerting is local, not business-grade.** Thresholds are
  loosely calibrated against this project's own numbers, not agreed with a
  stakeholder or validated against realised out-of-sample error. There is
  still no metric-store database, no scheduling, and no paging integration.
* **The review workflow is a tool, not a process.** No human staffing, SLA,
  or notification path is claimed to exist.
* **The scale benchmark is still one machine.** Sizes above the 3,000-context
  real sample are that sample replicated, not independent catalogue growth,
  and no other-hardware claim is made.
* **No GitHub remote was created and nothing was pushed.**

---

## 7. Classification

**PORTFOLIO READY WITH CLEAR LIMITATIONS** — unchanged. This phase closes
three engineering-only items from `FUTURE_WORK.md` §3 with real, tested,
disclosed evidence; it does not change v1.0.0's scientific findings or
classification.
