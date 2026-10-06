# Changelog

Versions follow `pyproject.toml`. The v1.0 engine (estimators, guardrails,
decision policy) is frozen: every later version adds layers around it.

## 1.2.0 (2026-10-06)

### Added

* **A second category.** The unchanged pipeline, with nothing re-tuned, was run
  on Dominick's crackers. 7 of 8 headline findings replicate, including the
  central one (the guardrails, not the model, set most of each price move).
  The one that does not: in crackers the model-internal value range reaches
  -0.1% at its low end, where cereal's is +4.1%. `reports/27_SECOND_CATEGORY.md`.
* **A live evaluation of the copilot.** Run against Qwen2.5-7B, a small
  open-weights model run locally through Docker, on the fixed 20-question set.
  The first run passed 8 of 20. After the fixes below it passes 17 of 20. The
  same 20 questions were used while fixing, so 17 of 20 is a development figure,
  not a held-out score. No ungrounded number was shown in any run. All three
  runs are published in `evals/copilot/`, with `docs/COPILOT_CARD.md`.
* **An MIT licence** for the code and documentation. The Dominick's data is not
  covered by it and is not included (stated in the README, not in `LICENSE`, so
  that GitHub recognises the standard MIT text).
* A practice sheet for explaining the project without notes (`docs/INTERVIEW_DRILL.md`).
* This changelog.

### Changed

* The copilot's price tools take a product name and look the code up in code.
  The first live run showed the model calling the right tool with product codes
  it had made up.
* The copilot's guard now recognises a gain by its value, not only by its
  wording: a percentage returned by a tool as a model-internal estimated uplift
  needs that label however the sentence is phrased.
* The evaluation scorer no longer counts a required tool whose call returned an
  error. A replay writes its own results file and never overwrites the recorded
  run.
* The data loader drops and counts blank export rows (the crackers file has
  200). The cereal table is byte-identical to before.
* The hosting workflow `deploy-demo.yml` is manual only. The demo is not hosted,
  by decision: in a local test it needed about 570 MB of memory for one
  visitor, more than free hosting tiers offer. It runs locally with one Docker
  command.

### Known limits (unchanged by this release)

* The copilot guard checks where a number comes from, not the unit or meaning
  the answer gives it. A weekly total described as "per unit" passed once.
* Three of the 20 evaluation cases still fail. They are documented and were
  left as they are.
* The value range is a model-internal estimate. Only a randomised pilot can
  measure it.

## 1.1.0 (2026-10-04)

* A public demo on synthetic data (`python scripts/make_demo.py`, or
  `Dockerfile.demo`), labelled synthetic on every page.
* Business value as a range (+4.1% to +9.4% of category gross profit),
  product roles, and a ground-truth recovery study on synthetic panels.
* The Pricing Copilot: a tool-calling LLM layer with a deterministic guard.
* A fix for recommendation ids that look like numbers being read as floats.
* Published to GitHub with continuous integration on Python 3.11, 3.12, 3.13.

## 1.0.0 (2026-08-19)

* The frozen engine: demand model, elasticity estimation with shrinkage,
  constrained price optimisation with risk gating, API, dashboard, review
  workflow, and the scientific audit in `reports/11` to `reports/21`.
