"""Evaluate the Pricing Copilot on a fixed question set.

    python scripts/eval_copilot.py --mode live      # needs GROQ_API_KEY (free) or OPENAI_API_KEY
    python scripts/eval_copilot.py --mode record    # live, and save each case's model turns
    python scripts/eval_copilot.py --mode replay    # recorded model turns (no key), live tools

Always runs against the synthetic demo (``configs/demo.yaml``; build it first
with ``python scripts/make_demo.py``), because the question set names demo
products. In every mode the engine tools execute for real; only the
language-model turns are live or replayed. A replayed answer whose numbers no
longer match the current demo build fails the guard, which is the signal to
re-record.

Scores per case: required tools called, required words present, forbidden
phrasings absent, and the guard outcome (passed, passed after one rewrite, or
replaced by the deterministic template).

Outputs
-------
    evals/copilot/results.json
    evals/copilot/recordings/<case id>.json   (record mode)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.copilot.agent import PricingCopilot  # noqa: E402
from pricing_engine.copilot.client import (  # noqa: E402
    OpenAIChatClient,
    RecordingClient,
    ReplayClient,
    resolve_llm_settings,
)
from pricing_engine.serving import build_state  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

EVAL_DIR = REPO_ROOT / "evals" / "copilot"
REFUSAL_HINTS = re.compile(
    r"\b(only|cannot|can't|can not|not able|unable|decline|outside|isn't something|"
    r"not something|no way to|would need|randomi[sz]ed|pilot)\b", re.I)


def score_case(case: dict, out, global_forbidden: list[str]) -> dict:
    called = [c.name for c in out.tool_calls]
    answer = out.answer
    missing_tools = [t for t in case.get("expected_tools", []) if t not in called]
    missing_words = [w for w in case.get("must_include", []) if w.lower() not in answer.lower()]
    forbidden = [rx for rx in [*global_forbidden, *case.get("forbidden", [])]
                 if re.search(rx, answer, re.I)]
    refusal_ok = True
    if case.get("expect_refusal"):
        refusal_ok = bool(REFUSAL_HINTS.search(answer))
    passed = not (missing_tools or missing_words or forbidden) and refusal_ok \
        and out.status != "fallback_template"
    return {
        "id": case["id"],
        "category": case.get("category"),
        "passed": passed,
        "guard_status": out.status,
        "tools_called": called,
        "missing_tools": missing_tools,
        "missing_words": missing_words,
        "forbidden_hits": forbidden,
        "refusal_ok": refusal_ok,
        "unsupported_numbers_final": out.guard.unsupported_numbers,
        "latency_ms": out.latency_ms,
        "tokens": out.usage,
        "answer": answer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "record", "replay"], default="replay")
    parser.add_argument("--config", default=str(REPO_ROOT / "configs" / "demo.yaml"))
    parser.add_argument("--only", default=None, help="Comma-separated case ids.")
    parser.add_argument("--pause", type=float, default=20.0,
                        help="Seconds between live cases (free tiers limit tokens per minute).")
    args = parser.parse_args()

    spec = yaml.safe_load((EVAL_DIR / "questions.yaml").read_text(encoding="utf-8"))
    cases = spec["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    cfg = load_config(args.config)
    state = build_state(cfg)
    recordings = EVAL_DIR / "recordings"

    settings = None
    if args.mode in ("live", "record"):
        settings = resolve_llm_settings(cfg.get("copilot.model"), env_file=REPO_ROOT / ".env")
        if settings is None:
            raise SystemExit("No model key found. Put GROQ_API_KEY=... (free) or OPENAI_API_KEY=... "
                             "in .env, or use --mode replay.")
        print(f"provider {settings.provider}, model {settings.model}")

    results = []
    for case in cases:
        if args.mode == "replay":
            path = recordings / f"{case['id']}.json"
            if not path.exists():
                results.append({"id": case["id"], "passed": None, "skipped": "no recording"})
                continue
            client = ReplayClient.load(path)
        else:
            client = RecordingClient(OpenAIChatClient(settings))
        copilot = PricingCopilot(
            client, state,
            max_tool_rounds=int(cfg.get("copilot.max_tool_rounds", 5)),
            max_regenerations=int(cfg.get("copilot.max_regenerations", 1)),
            max_output_tokens=int(cfg.get("copilot.max_output_tokens", 1500)),
        )
        try:
            out = copilot.ask(case["question"])
        except RuntimeError as exc:  # replay ran out of turns: the recording is stale
            results.append({"id": case["id"], "passed": False, "error": str(exc)})
            continue
        if args.mode == "record":
            client.save(recordings / f"{case['id']}.json", case_id=case["id"],
                        question=case["question"], recorded_at_utc=utc_now(),
                        provider=settings.provider)
        if args.mode != "replay" and case is not cases[-1]:
            time.sleep(args.pause)
        row = score_case(case, out, spec.get("global_forbidden", []))
        results.append(row)
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"[{mark}] {case['id']:<24} guard={row['guard_status']:<26} tools={row['tools_called']}")

    scored = [r for r in results if r.get("passed") is not None]
    statuses = [r.get("guard_status") for r in scored if "guard_status" in r]
    summary = {
        "generated_at_utc": utc_now(),
        "mode": args.mode,
        "provider": settings.provider if settings else "replay",
        "model": settings.model if settings else "recorded",
        "data_mode": cfg.data_mode,
        "cases": len(cases),
        "scored": len(scored),
        "skipped": len(results) - len(scored),
        "pass_rate": sum(bool(r["passed"]) for r in scored) / len(scored) if scored else None,
        "guard_passed_first_time": statuses.count("passed"),
        "guard_passed_after_rewrite": statuses.count("passed_after_regeneration"),
        "guard_fallback_template": statuses.count("fallback_template"),
        "forbidden_hits": sum(len(r.get("forbidden_hits", [])) for r in scored),
        "unsupported_numbers_in_final_answers": sum(
            len(r.get("unsupported_numbers_final", [])) for r in scored),
        "results": results,
    }
    write_json(EVAL_DIR / "results.json", summary)
    if scored:
        print(f"\n{summary['pass_rate']:.0%} of {len(scored)} cases passed | guard: "
              f"{summary['guard_passed_first_time']} first time, "
              f"{summary['guard_passed_after_rewrite']} after rewrite, "
              f"{summary['guard_fallback_template']} template | forbidden hits "
              f"{summary['forbidden_hits']} | unsupported numbers shown "
              f"{summary['unsupported_numbers_in_final_answers']}")
    else:
        print("no recordings to replay: run --mode record with a model key in .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
