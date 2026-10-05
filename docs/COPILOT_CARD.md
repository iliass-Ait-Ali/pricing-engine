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
| `list_products` | served contexts | UPC, store, description (browse what is served) |
| `get_model_info` | serving state | data provenance, model version, price-response method |
| `recommend_price` | `AppState.recommend` -> `optimize_price` | decision state, prices, allowed range, risk, reason codes, model-internal estimated economics |
| `simulate_prices` | `AppState.simulate_prices` | at most 25 candidate prices with units, revenue, gross profit |
| `compare_policies` | `optimize_price` x 3 profiles | conservative / standard / aggressive side by side |
| `explain_reason_codes` | `copilot/glossary.py` | plain-English meaning (a test keeps it complete) |
| `portfolio_summary` | `optimize_price_batch` | decision mix, risk mix, model-internal estimated uplift |

Every tool rounds its numbers and gives percentages as percentages (8.4, not
0.0838), so the model never has to compute anything. The price tools take a
product name or a code and resolve it in code, so the model never has to copy
a product code either; a code that is not served is refused, not guessed.

## The guard (`src/pricing_engine/copilot/guard.py`)

1. **Numbers.** Every number in the answer must equal a tool result, a tool
   argument or a number from the question, at the precision the answer states
   it. `$3.49` passes against 3.4912. `8.5%` fails against 8.6. Small counts
   (up to 12) and list markers are exempt.
2. **Wording.** Claims that are causal, guaranteed, proven or "realised", or
   that call a price optimal, fail unless the sentence negates them. Any gain or uplift must be
   called model-internal, whether it is recognised by its wording or by its
   value (a percentage a tool returned as an uplift). These are the same rules
   `scripts/audit_claims.py` applies to the repository.
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
* **Cost and abuse.** On the public demo: per-client and daily request caps
  and a token cap per answer. The default provider is Groq's free tier, which
  cannot run up a bill; with a paid provider, set a hard spending limit too.
* **Model drift.** CI replays recorded model turns. Re-record
  (`scripts/eval_copilot.py --mode record`) when the model or the demo data
  changes; stale recordings fail the guard on purpose.

## Which model

The copilot talks to any endpoint that speaks the OpenAI chat-completions
format with tool calling. It picks the first key it finds:

| key | provider | default model | cost |
| --- | --- | --- | --- |
| `COPILOT_API_KEY` + `COPILOT_BASE_URL` | anything compatible (a local Ollama server, OpenRouter, ...) | set `COPILOT_MODEL` | yours |
| `GROQ_API_KEY` | Groq | `openai/gpt-oss-120b` (open weights) | free tier, no card |
| `OPENAI_API_KEY` | OpenAI | `gpt-4o-mini` | paid |

Keys are read from the environment or from a git-ignored `.env` file in the
repository root. `COPILOT_MODEL` overrides the model for any provider. The
guard does not depend on the model: a weaker model fails the checks more often
and falls back to the template more often, but it still cannot show an
ungrounded number.

## Evaluation (`evals/copilot/questions.yaml`)

There are 20 cases: recommendations, explanations, simulations, policy
comparisons, portfolio questions, causal bait, fabrication bait, an unknown
product, an off-topic question and a prompt injection. Each case lists the
tools a good answer needs, the words it must contain and the phrasings it must
never use.

```bash
python scripts/make_demo.py --fast --out-root build/demo_fast    # the build CI replays against
python scripts/eval_copilot.py --mode record --config build/demo_fast/demo_rooted.yaml
python scripts/eval_copilot.py --mode replay --config build/demo_fast/demo_rooted.yaml
```

Record mode needs a model: a free `GROQ_API_KEY` in `.env`, or a local model
with no account (below). Replay needs nothing and is what CI runs.

Results go to `evals/copilot/results.json` (the recorded run) and
`evals/copilot/recordings/` (the model turns CI replays).

### Results (recorded 2026-10-05)

**Model:** `qwen2.5:7b`, an open-weights model with 7 billion parameters, run
locally (Ollama in Docker, one laptop GPU, temperature 0). It was chosen
because it needs no account and no key. It is far smaller than the default
hosted model, so read these figures as what a weak model does behind the
guard, not as the copilot's best case.

| run | what changed | cases passed | guard: first time / after one rewrite / template | ungrounded numbers shown |
| --- | --- | --- | --- | --- |
| 1 | as shipped in v1.1.0 | 8 of 20 | 16 / 4 / 0 | 0 |
| 2 | price tools look a product up by name | 16 of 20 | 11 / 8 / 1 | 0 |
| 3 (current) | guard recognises an uplift figure by its value | 17 of 20 | 10 / 9 / 1 | 0 |

All three runs are scored by the same, final scorer. Every result file is in
`evals/copilot/` (`history/` for runs 1 and 2), with each answer in full.

**What the first live run found**

1. **Invented product codes.** In 9 of 20 cases the model called the right
   tool with a UPC it had made up, sometimes straight after looking the real
   one up. The engine refused each time, so no price was fabricated, but the
   person got "no data" for a product that is served. Fix: the price tools
   take the product *name* and resolve the code in code
   (`tools.resolve_upc`); an unknown code returns an error that says how to
   recover.
2. **A scorer that was too kind.** The original scorer only checked that the
   required tool was *called*, so those "no data" answers counted as passes
   (14 of 20). A required tool now has to return a result. This made the
   score stricter, not better.
3. **A gap in the guard.** "Estimated to increase the gross profit by 43.7%"
   went through without the model-internal label, because the rule looked for
   words like "uplift" and the sentence used none. The guard now also
   recognises a gain by its value: a percentage that a tool returned as a
   model-internal estimated uplift needs the label however it is phrased.
4. **The blind spot, seen live.** Run 3 still contains "$2.15 per unit", where
   $2.15 is the weekly gross profit: a right number with a wrong label, which
   passes. In run 1 one answer attached `RECOMMEND_CHANGE` to a product whose
   recommendation had never been retrieved (the state name came from the
   portfolio summary). The guard checks form, not meaning; this is why the
   tool results are always shown next to the answer.

**The three cases that still fail**

* `rec_revenue_objective`: two drafts failed the guard, so the person saw the
  deterministic template. Correct, but not a model answer.
* `explain_review`: the answer says "needs approval from a person"; the case
  requires the word "approve". Right in substance, scored as a failure.
* `unknown_product`: the answer correctly says no such product is served, but
  the case requires a `list_products` check that the model skipped.

**How far to trust the figure**

* The question set was written before any live run and was not edited after.
  The tools, the prompt and the guard *were* changed after run 1, on these
  same 20 questions, so 17 of 20 is a development-set figure, not a held-out
  score.
* One model, one run per stage, 20 questions: no interval is given because
  none would mean anything at this size.
* A pass means the answer met the case's checks. It does not mean every
  sentence is right (see point 4 above).
* The default hosted model (`openai/gpt-oss-120b` on Groq's free tier) has not
  been run. Re-recording with it takes one command and a free key.

CI replays run 3 against a fresh demo build. A recording goes stale when the
numbers it quotes move (the `model_info` answer quotes the model version,
which carries a build time, so it is stale on every rebuild); a stale case is
reported as failed, never as passed.

### Running it with no account at all

```bash
docker run -d --gpus all --name pe-ollama -e OLLAMA_CONTEXT_LENGTH=16384 \
  -v pe_ollama:/root/.ollama -p 127.0.0.1:11434:11434 ollama/ollama
docker exec pe-ollama ollama pull qwen2.5:7b
```

and in `.env`:

```text
COPILOT_API_KEY=local
COPILOT_BASE_URL=http://127.0.0.1:11434/v1
COPILOT_MODEL=qwen2.5:7b
```

The context length matters: the default (4,096 tokens) is too small for the
tool definitions plus a tool result, and the model then answers from a
truncated prompt. Drop `--gpus all` to run on the CPU (slower).

## Running it

```bash
pip install -e ".[api,dashboard,genai]"
echo GROQ_API_KEY=... > .env         # git-ignored; or OPENAI_API_KEY=...
uvicorn api.main:app                 # POST /copilot/ask {"question": "..."}
streamlit run dashboard/app.py       # page "Pricing Copilot"
```

Without a key the endpoint returns 503 and the dashboard page explains why.
The rest of the system does not depend on the copilot.
