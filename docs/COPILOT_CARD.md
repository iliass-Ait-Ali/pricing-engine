# Pricing Copilot card

A plain-English question box on top of the pricing engine: *"What price for
Cheerios in store 2, and why?"*, *"Compare the three policies"*, *"How many
decisions need a person this week?"*

## Design in one sentence

**The language model chooses which engine tools to call and writes the
answer. It never prices anything and never supplies a number, and a
deterministic guard enforces that before anyone sees the answer.**

```text
question -> LLM picks tools -> engine runs them (same code as the API)
         -> LLM drafts an answer from the tool results
         -> guard: numbers, wording, decision state
              pass                    -> answer + tool trace
              fail                    -> one rewrite with the guard's feedback
              fail again              -> deterministic template from tool results
```

## Tools (`src/pricing_engine/copilot/tools.py`)

| tool | wraps | returns |
| --- | --- | --- |
| `list_products` | served contexts | UPC, store, description (name -> code lookup) |
| `get_model_info` | serving state | data provenance, model version, price-response method |
| `recommend_price` | `AppState.recommend` -> `optimize_price` | decision state, prices, allowed range, risk, reason codes, model-internal estimated economics |
| `simulate_prices` | `AppState.simulate_prices` | at most 25 candidate prices with units, revenue, gross profit |
| `compare_policies` | `optimize_price` x 3 profiles | conservative / standard / aggressive side by side |
| `explain_reason_codes` | `copilot/glossary.py` | plain-English meaning (a test keeps it complete) |
| `portfolio_summary` | `optimize_price_batch` | decision mix, risk mix, model-internal estimated uplift |

Every tool rounds its numbers and gives percentages as percentages (8.4, not
0.0838), so the model never has to compute anything.

## The guard (`src/pricing_engine/copilot/guard.py`)

1. **Numbers.** Every number in the answer must equal a tool result, a tool
   argument or a number from the question, at the precision the answer states
   it. `$3.49` passes against 3.4912. `8.5%` fails against 8.6. Small counts
   (up to 12) and list markers are exempt.
2. **Wording.** Claims that are causal, guaranteed, proven or "realised", or
   that call a price optimal, fail unless the sentence negates them. Any gain or uplift must be
   called model-internal. These are the same rules `scripts/audit_claims.py`
   applies to the repository.
3. **Decision state.** A retrieved recommendation's state (`RECOMMEND_CHANGE`,
   `REVIEW_REQUIRED`, `KEEP_CURRENT`) must be quoted exactly.

The synthetic-data label is appended by code, not by the model.

## Known limits

* **Right number, wrong label.** "The cost is $3.49", when $3.49 is the price,
  passes the number check. Mitigation: the dashboard shows the raw tool results
  next to every answer, and the evaluation set checks attribution on its
  cases. This is the guard's main blind spot.
* **The guard checks form, not judgement.** A grounded answer can still
  emphasise the wrong thing. The tool trace is always shown so a person can
  check it.
* **Cost and abuse.** On the public demo: per-client and daily request caps,
  a token cap per answer, and a small model. Set a hard spending limit on the
  OpenAI project as well.
* **Model drift.** CI replays recorded model turns. Re-record
  (`scripts/eval_copilot.py --mode record`) when the model or the demo data
  changes; stale recordings fail the guard on purpose.

## Evaluation (`evals/copilot/questions.yaml`)

There are 20 cases: recommendations, explanations, simulations, policy
comparisons, portfolio questions, causal bait, fabrication bait, an unknown
product, an off-topic question and a prompt injection. Each case lists the
tools a good answer needs, the words it must contain and the phrasings it must
never use.

```bash
python scripts/make_demo.py                       # the demo the cases refer to
python scripts/eval_copilot.py --mode record      # needs OPENAI_API_KEY
python scripts/eval_copilot.py --mode replay      # offline, what CI runs
```

Results go to `evals/copilot/results.json`. No live results are reported here
until the record run has been done; nothing in this card is a measured
accuracy figure.

## Running it

```bash
pip install -e ".[api,dashboard,genai]"
export OPENAI_API_KEY=...            # optional OPENAI_MODEL (default in configs/config.yaml)
uvicorn api.main:app                 # POST /copilot/ask {"question": "..."}
streamlit run dashboard/app.py       # page "Pricing Copilot"
```

Without a key the endpoint returns 503 and the dashboard page explains why.
The rest of the system does not depend on the copilot.
