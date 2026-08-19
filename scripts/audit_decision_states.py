"""Phase M / Task 10 - decision-state consistency audit.

    python scripts/audit_decision_states.py

Enumerates every observed combination of (risk level, decision state,
actionable, proposal vs final price) over the real decision pool, checks the
invariants on every one of them, and explains why HIGH-risk contexts split
between REVIEW_REQUIRED and KEEP_CURRENT.

Outputs
-------
    reports/19_DECISION_STATE_AUDIT.md
    artifacts/metrics/decision_state_audit.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _phase_m import load_decision_pool, load_models, md_table, promotion_share  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.optimization.attribution import (  # noqa: E402
    Thresholds,
    contexts_from_frame,
    evaluate,
)
from pricing_engine.utils.io import write_json  # noqa: E402

PROFILES = ["standard", "conservative", "aggressive"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    pool, week = load_decision_pool(cfg, limit=args.limit)
    _, model = load_models(cfg)
    thresholds = Thresholds.from_config(cfg)
    contexts = contexts_from_frame(model, pool, promotion_share=promotion_share(cfg, pool))
    print(f"decision week {week}: {len(contexts):,} contexts")

    frames, violations, profile_summary = {}, {}, {}
    for profile in PROFILES:
        policy = cfg.policy(profile)
        evs = [evaluate(c, policy, thresholds) for c in contexts]
        df = pd.DataFrame({
            "upc": [c.upc for c in contexts],
            "store": [c.store for c in contexts],
            "current_price": [c.current_price for c in contexts],
            "risk_level": [e.risk_level for e in evs],
            "decision": [e.decision for e in evs],
            "actionable": [e.actionable for e in evs],
            "proposed_candidate_price": [e.proposed_candidate_price for e in evs],
            "final_recommended_price": [e.final_recommended_price for e in evs],
            "reason_codes": [",".join(e.reason_codes) for e in evs],
        })
        df["proposal_differs_from_current"] = (
            (df["proposed_candidate_price"] - df["current_price"]).abs() > 1e-9
        )
        df["final_differs_from_current"] = (
            (df["final_recommended_price"] - df["current_price"]).abs() > 1e-9
        )
        frames[profile] = df

        checks = {
            "HIGH risk is never actionable":
                int((df["risk_level"].eq("HIGH") & df["actionable"]).sum()),
            "actionable iff RECOMMEND_CHANGE":
                int((df["actionable"] != df["decision"].eq("RECOMMEND_CHANGE")).sum()),
            "REVIEW_REQUIRED keeps the current price":
                int((df["decision"].eq("REVIEW_REQUIRED") & df["final_differs_from_current"]).sum()),
            "KEEP_CURRENT keeps the current price":
                int((df["decision"].eq("KEEP_CURRENT") & df["final_differs_from_current"]).sum()),
            "KEEP_CURRENT proposes nothing":
                int((df["decision"].eq("KEEP_CURRENT") & df["proposal_differs_from_current"]).sum()),
            "RECOMMEND_CHANGE actually changes the price":
                int((df["decision"].eq("RECOMMEND_CHANGE") & ~df["final_differs_from_current"]).sum()),
        }
        violations[profile] = checks
        profile_summary[profile] = {
            "decision_counts": df["decision"].value_counts().to_dict(),
            "risk_counts": df["risk_level"].value_counts().to_dict(),
            "n_high_risk": int(df["risk_level"].eq("HIGH").sum()),
            "n_high_risk_actionable": int((df["risk_level"].eq("HIGH") & df["actionable"]).sum()),
            "n_actionable": int(df["actionable"].sum()),
            "n_proposal_suppressed": int(
                (df["proposal_differs_from_current"] & ~df["final_differs_from_current"]).sum()
            ),
        }

    std = frames["standard"]

    # ---- the full cross-tabulation ------------------------------------------
    combo = (
        std.groupby(
            ["risk_level", "decision", "actionable", "proposal_differs_from_current",
             "final_differs_from_current"],
            observed=True,
        )
        .size()
        .reset_index(name="contexts")
        .sort_values("contexts", ascending=False)
    )
    combo_rows = [
        [r["risk_level"], r["decision"], str(r["actionable"]),
         "yes" if r["proposal_differs_from_current"] else "no",
         "yes" if r["final_differs_from_current"] else "no",
         f"{r['contexts']:,}", f"{100*r['contexts']/len(std):.1f}%"]
        for _, r in combo.iterrows()
    ]

    # ---- why do HIGH-risk contexts split? -----------------------------------
    high = std[std["risk_level"].eq("HIGH")]
    high_split = high["decision"].value_counts()
    reasons = high["reason_codes"].str.get_dummies(sep=",")
    high_reason_rows = [
        [code, f"{int(reasons[code].sum()):,}", f"{100*reasons[code].mean():.1f}%"]
        for code in reasons.columns
        if reasons[code].sum() > 0
    ]
    high_reason_rows.sort(key=lambda r: -int(r[1].replace(",", "")))

    high_keep = high[high["decision"].eq("KEEP_CURRENT")]
    keep_causes = {
        "screened out by the eligibility rules (never reaches the optimizer)":
            int(high_keep["reason_codes"].str.contains(
                "INSUFFICIENT_HISTORY|INSUFFICIENT_PRICE_VARIATION|COST_UNAVAILABLE").sum()),
        "no feasible price inside the guardrails":
            int(high_keep["reason_codes"].str.contains("NO_FEASIBLE_PRICE").sum()),
        "the optimum IS the current price":
            int((~high_keep["proposal_differs_from_current"]
                 & ~high_keep["reason_codes"].str.contains("NON_MATERIAL_UPLIFT")).sum()),
        "estimated uplift below the materiality threshold":
            int(high_keep["reason_codes"].str.contains("NON_MATERIAL_UPLIFT").sum()),
    }
    keep_rows = [[k, f"{v:,}", f"{100*v/max(len(high_keep),1):.1f}%"] for k, v in keep_causes.items()]

    # The one expected non-zero cell is the DEMO `aggressive` profile acting on
    # HIGH risk, which it is explicitly configured to do. Everything else is a
    # genuine violation.
    total_violations = sum(
        n
        for profile, checks in violations.items()
        for name, n in checks.items()
        if not (profile == "aggressive" and name == "HIGH risk is never actionable")
    )
    check_rows = []
    for name in violations["standard"]:
        row = [name]
        for profile in PROFILES:
            n = violations[profile][name]
            expected_exception = (
                profile == "aggressive" and name == "HIGH risk is never actionable"
            )
            row.append(f"{n:,} (DEMO exception)" if expected_exception and n else ("PASS" if n == 0 else f"FAIL ({n:,})"))
        check_rows.append(row)

    summary = {
        "decision_week": int(week),
        "n_contexts": int(len(std)),
        "profiles": profile_summary,
        "invariant_violations": violations,
        "high_risk_decision_split": high_split.to_dict(),
        "high_risk_keep_current_causes": keep_causes,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "decision_state_audit.json", summary)

    agg = profile_summary["aggressive"]
    report = f"""# 19. Decision-state consistency audit

The dangerous failure mode of a pricing engine is not a wrong number - it is a
proposal that gets read as a decision. This report enumerates every combination
of risk level, decision state, actionable flag and price that occurs in the real
decision pool, and checks the invariants on all of them.

Pool: all {len(std):,} `UPC x store` contexts of week {week}.

## 1. The three prices, kept apart

| Field | Meaning |
| --- | --- |
| `current_price` | what is charged today |
| `proposed_candidate_price` | what the optimizer wanted to do. **Not a business price.** |
| `final_recommended_price` | what would actually be charged: the proposal only when `actionable`, otherwise `current_price` |

Phase M renamed these. Before, a single `recommended_price` field held the
proposal even for REVIEW_REQUIRED rows, so any consumer that did not also read
`actionable` would show a price that the policy had refused to authorise. The
API, the dashboard, the audit log and every report now carry both fields, and
`price_change_pct` refers to the **final** price (it is 0 whenever the decision
is not actionable).

## 2. Invariants

{md_table(check_rows, ["Invariant", *PROFILES], ["---", "---", "---", "---"])}

**Genuine violations across all three profiles: {total_violations}.** The one
non-`PASS` cell is the `aggressive` profile acting on HIGH risk, which is what
that profile is configured to do (`high_risk_action: recommend`), is labelled
DEMO ONLY in `configs/config.yaml`, and is never a default. It is shown here
rather than hidden so the exception stays visible.

Property-style tests over 245 parameter combinations pin the same invariants in
`tests/test_decision_states.py`.

## 3. Every observed combination

{md_table(combo_rows, ["Risk", "Decision", "Actionable", "Proposal moves?", "Final moves?", "Contexts", "Share"], ["---", "---", "---", "---", "---", "---:", "---:"])}

The row that matters is `HIGH / REVIEW_REQUIRED / False / yes / no`: a proposal
exists, and the final price does not move. That is the human-in-the-loop path
working.

## 4. Why HIGH-risk contexts split between REVIEW_REQUIRED and KEEP_CURRENT

Under the `standard` profile there are **{len(high):,}** HIGH-risk contexts, and
they do not all become REVIEW_REQUIRED:

{md_table([[k, f"{v:,}", f"{100*v/len(high):.1f}%"] for k, v in high_split.items()], ["Decision", "Contexts", "Share of HIGH risk"], ["---", "---:", "---:"])}

REVIEW_REQUIRED means *"the optimizer produced a material, feasible proposal and
the risk gate refuses to auto-apply it"*. A HIGH-risk context ends at
KEEP_CURRENT instead whenever there was **never a proposal to gate** - the
context failed earlier in the pipeline, so the risk gate has nothing to
suppress:

{md_table(keep_rows, ["Why the HIGH-risk context is KEEP_CURRENT rather than REVIEW_REQUIRED", "Contexts", "Share of HIGH-risk KEEP_CURRENT"], ["---", "---:", "---:"])}

(The categories overlap: a context can be screened out *and* have an immaterial
uplift.) The ordering is what produces the split - eligibility screen, then risk
gate, then optimisation, then materiality, then the final risk gate. The risk
gate only ever sees contexts that survived everything before it.

Reason codes present on HIGH-risk contexts:

{md_table(high_reason_rows, ["Reason code", "Contexts", "Share of HIGH risk"], ["---", "---:", "---:"])}

## 5. HIGH-risk actionable count

| Profile | HIGH-risk contexts | HIGH-risk actionable |
| --- | ---: | ---: |
""" + "\n".join(
        f"| `{p}` | {profile_summary[p]['n_high_risk']:,} | {profile_summary[p]['n_high_risk_actionable']:,} |"
        for p in PROFILES
    ) + f"""

Zero under both default profiles. The `aggressive` profile makes
{agg['n_high_risk_actionable']:,} of them actionable, which is precisely what it
is documented to do and why it is labelled DEMO ONLY in
`configs/config.yaml`.

## 6. Suppressed proposals

Under `standard`, **{profile_summary['standard']['n_proposal_suppressed']:,}**
contexts carry a proposal that the policy layer refuses to apply. Those are the
rows a human reviewer would work through. They must never be counted in a
portfolio profit figure, and they are not: `realisable_profit_uplift_pct` is
forced to 0 for every non-actionable recommendation, and
`scripts/optimize.py` sums the current-price economics for them.

---

*Generated by `scripts/audit_decision_states.py` in {summary['runtime_seconds']}s.*
"""
    out = cfg.path("reports_dir") / "19_DECISION_STATE_AUDIT.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    if total_violations:
        print(f"FAIL: {total_violations} decision-state invariant violations")
    return 0 if total_violations == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
