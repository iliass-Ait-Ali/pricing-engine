"""Generate the figures used by reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md.

Every value plotted here is read from an artifact that the pipeline produced -
nothing is typed in. Run after `make all`.

    python scripts/make_report_figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
METRICS = ROOT / "artifacts" / "metrics"
OUT = ROOT / "artifacts" / "report_figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update(
    {
        "figure.dpi": 130,
        "savefig.dpi": 130,
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

BLUE, ORANGE, GREY, RED, GREEN = "#2b6cb0", "#dd6b20", "#718096", "#c53030", "#2f855a"


def load(name: str) -> dict:
    return json.loads((METRICS / name).read_text(encoding="utf-8"))


def save(fig, name: str) -> None:
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {name}")


# --------------------------------------------------------------------------
# 1. temporal split
# --------------------------------------------------------------------------
def fig_temporal_split() -> None:
    split = load("feature_build.json")["split"]
    fig, ax = plt.subplots(figsize=(9, 2.3))
    blocks = [
        ("TRAIN", split["train_weeks"], split["train_dates"], split["n_train"], BLUE),
        ("VALIDATION", split["valid_weeks"], split["valid_dates"], split["n_valid"], ORANGE),
        ("TEST", split["test_weeks"], split["test_dates"], split["n_test"], GREEN),
    ]
    for label, (w0, w1), (d0, d1), n, colour in blocks:
        ax.barh(0, w1 - w0, left=w0, height=0.45, color=colour, alpha=0.85)
        ax.text(
            (w0 + w1) / 2,
            0,
            f"{label}\nweeks {w0}-{w1}\n{n:,} rows",
            ha="center",
            va="center",
            color="white",
            fontsize=8.5,
            fontweight="bold",
        )
        ax.text(
            (w0 + w1) / 2, -0.42, f"{d0}  ..  {d1}", ha="center", va="center", fontsize=8,
            color="#2d3748",
        )
    ax.axvline(257.5, color=RED, ls="--", lw=1.2)
    ax.text(257.5, 0.45, "  elasticity estimation stops here (week 257)", color=RED,
            fontsize=8, va="bottom")
    ax.set_ylim(-0.75, 0.75)
    ax.set_yticks([])
    ax.set_xlabel("Dominick's week index")
    ax.set_title("Chronological train / validation / test split (no shuffling anywhere)")
    ax.grid(False)
    save(fig, "fig_01_temporal_split.png")


# --------------------------------------------------------------------------
# 2. elasticity ladder
# --------------------------------------------------------------------------
def fig_elasticity_ladder() -> None:
    e = load("elasticity.json")
    est = load("elasticity_estimation.json")
    pr = load("price_response.json")["summary"]
    rows = [(m["model"], m["price_elasticity"]) for m in e["loglog"]]
    rows += [
        ("Pricing pooled (training weeks only)", est["pooled_elasticity"]),
        ("Pricing UPC x store FE (training only)", est["fe_elasticity"]),
        ("Native ML implied (finite difference)", pr["median_local_elasticity"]),
    ]
    labels = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    colours = [GREY] * 4 + [BLUE, BLUE, RED]
    fig, ax = plt.subplots(figsize=(8.4, 3.5))
    y = np.arange(len(rows))
    ax.barh(y, vals, color=colours, alpha=0.9)
    for i, v in enumerate(vals):
        ax.text(v - 0.06, i, f"{v:.3f}", va="center", ha="right", fontsize=8.5,
                color="white", fontweight="bold")
    ax.set_yticks(y, labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.axvline(-1.0, color=ORANGE, ls=":", lw=1.2)
    ax.text(-1.0, -0.75, " unit elastic", color=ORANGE, fontsize=8)
    ax.set_xlabel("estimated own-price elasticity")
    ax.set_title("From naive association to the elasticity actually used for pricing")
    save(fig, "fig_02_elasticity_ladder.png")


# --------------------------------------------------------------------------
# 3. robust inference
# --------------------------------------------------------------------------
def fig_inference() -> None:
    inf = load("elasticity_inference.json")
    spec = inf["pooled_specifications"][0]
    coef = spec["coefficient"]
    names, ses, los, his = [], [], [], []
    for name, s in spec["specifications"].items():
        names.append(name)
        ses.append(s["std_error"])
        los.append(s["ci_low"])
        his.append(s["ci_high"])
    y = np.arange(len(names))
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(10.5, 3.4), gridspec_kw={"width_ratios": [1.35, 1]}
    )
    ax1.errorbar(
        np.full(len(names), coef),
        y,
        xerr=[np.array([coef - lo for lo in los]), np.array([hi - coef for hi in his])],
        fmt="o",
        color=BLUE,
        capsize=4,
        ms=4,
    )
    ax1.axvline(coef, color=GREY, ls="--", lw=1)
    ax1.set_yticks(y, names, fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlabel("pooled elasticity, 95% interval")
    ax1.set_title("Same point estimate, seven covariance assumptions")
    ax2.barh(y, ses, color=[RED if "two-way" in n else BLUE for n in names], alpha=0.9)
    ax2.set_yticks(y, [""] * len(names))
    ax2.invert_yaxis()
    ax2.set_xlabel("standard error")
    ax2.set_title(f"Standard error (classical = {ses[0]:.4f})", fontsize=9)
    for i, s in enumerate(ses):
        ax2.text(s, i, f"  {s:.4f}", va="center", fontsize=8)
    save(fig, "fig_03_inference_standard_errors.png")


# --------------------------------------------------------------------------
# 4. empirical-Bayes shrinkage
# --------------------------------------------------------------------------
def fig_shrinkage() -> None:
    aud = load("shrinkage_audit.json")
    est = load("elasticity_estimation.json")
    shipped = aud["variants"]["Phase M (shipped): REML tau^2, pooled prior, ROBUST se, filtered"]
    phase_l = aud["variants"]["Phase L: moment tau^2, sample-mean prior, HC1 se, filtered"]
    tau2_m, tau2_l = shipped["tau2"], phase_l["tau2"]
    se = np.linspace(0.01, 2.0, 400)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.4))
    ax1.plot(se, tau2_m / (tau2_m + se**2), color=BLUE, lw=2,
             label=f"shipped: panel-robust SE, tau2={tau2_m:.3f}")
    ax1.plot(se, tau2_l / (tau2_l + se**2), color=RED, lw=1.6, ls="--",
             label=f"Phase L: HC1 SE, tau2={tau2_l:.3f}")
    ax1.axvline(shipped["median_se"], color=BLUE, ls=":", lw=1.2)
    ax1.text(shipped["median_se"], 0.06, f"  median robust SE {shipped['median_se']:.3f}",
             fontsize=8, color=BLUE)
    ax1.axvline(phase_l["median_se"], color=RED, ls=":", lw=1.2)
    ax1.text(phase_l["median_se"], 0.16, f"  median HC1 SE {phase_l['median_se']:.3f}",
             fontsize=8, color=RED)
    ax1.set_xlabel("per-product standard error  se_i")
    ax1.set_ylabel("shrinkage weight  w_i = tau2/(tau2 + se_i^2)")
    ax1.set_ylim(0, 1.02)
    ax1.legend(fontsize=7.5, loc="lower left")
    ax1.set_title("Understated SEs silently switch the shrinkage off")
    bands = aud["weight_bands"]
    ax2.bar(list(bands), list(bands.values()), color=BLUE, alpha=0.9)
    for i, v in enumerate(bands.values()):
        ax2.text(i, v, f"{v}", ha="center", va="bottom", fontsize=8.5)
    ax2.set_xlabel("shrinkage weight band")
    ax2.set_ylabel("products")
    ax2.set_title(
        f"Shipped weights, {est['n_products_usable']} usable products "
        f"(mean {est['mean_shrinkage_weight']:.3f})",
        fontsize=9,
    )
    save(fig, "fig_04_shrinkage.png")


# --------------------------------------------------------------------------
# 5. eligibility funnel
# --------------------------------------------------------------------------
def fig_funnel() -> None:
    f = load("elasticity_funnel.json")
    stages = f["funnel"]
    labels = [s["stage"] for s in stages]
    vals = [s["n_upcs"] for s in stages]
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    y = np.arange(len(stages))
    ax.barh(y, vals, color=[BLUE] * (len(stages) - 1) + [GREEN], alpha=0.9)
    for i, v in enumerate(vals):
        drop = v - vals[i - 1] if i else 0
        txt = f"{v}" + (f"   ({drop:+d})" if i and drop else "")
        ax.text(v + 4, i, txt, va="center", fontsize=8.5)
    ax.set_yticks(y, labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("UPCs")
    ax.set_xlim(0, max(vals) * 1.22)
    ax.set_title("Product-level elasticity eligibility funnel (training weeks 2-257)")
    save(fig, "fig_05_elasticity_funnel.png")


# --------------------------------------------------------------------------
# 6. constraint attribution
# --------------------------------------------------------------------------
def fig_constraints() -> None:
    ca = load("constraint_attribution.json")
    n = ca["n_contexts"]
    det = ca["final_determinant_counts"]
    first = ca["first_binding_constraint_counts"]
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.5, 3.8), gridspec_kw={"width_ratios": [1.15, 1]}
    )
    keys = list(det)
    vals = [det[k] for k in keys]
    colours = [RED if "learned" not in k else GREEN for k in keys]
    y = np.arange(len(keys))
    ax1.barh(y, vals, color=colours, alpha=0.9)
    for i, v in enumerate(vals):
        ax1.text(v + n * 0.01, i, f"{v:,}  ({100*v/n:.1f}%)", va="center", fontsize=8.5)
    ax1.set_yticks(y, [k.replace(" (", "\n(") for k in keys], fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlim(0, max(vals) * 1.28)
    ax1.set_xlabel(f"decision contexts (n = {n:,}, week 399, standard profile)")
    ax1.set_title("What actually determines the final recommendation")
    keys2 = sorted(first, key=first.get, reverse=True)
    vals2 = [first[k] for k in keys2]
    ax2.bar(keys2, vals2, color=BLUE, alpha=0.9)
    for i, v in enumerate(vals2):
        ax2.text(i, v + n * 0.008, f"{100*v/n:.1f}%", ha="center", fontsize=8)
    ax2.set_xticks(range(len(keys2)), keys2, rotation=30, ha="right", fontsize=8)
    ax2.set_ylabel("contexts")
    ax2.set_title("First binding constraint")
    save(fig, "fig_06_constraint_attribution.png")


# --------------------------------------------------------------------------
# 7. guardrail ablation
# --------------------------------------------------------------------------
def fig_guardrails() -> None:
    g = load("guardrail_ablation.json")
    layers = list(g["layers"])
    med = [100 * g["layers"][k]["median_price_variation_across_scenarios_pct"] for k in layers]
    p90 = [100 * g["layers"][k]["p90_price_variation_across_scenarios_pct"] for k in layers]
    share = [100 * g["layers"][k]["share_elasticity_scenario_changes_price"] for k in layers]
    x = np.arange(len(layers))
    fig, ax = plt.subplots(figsize=(9.4, 3.6))
    ax.bar(x - 0.2, med, 0.38, label="median price variation across the 4 elasticity scenarios (%)",
           color=BLUE, alpha=0.9)
    ax.bar(x + 0.2, p90, 0.38, label="p90 price variation (%)", color=ORANGE, alpha=0.85)
    for i, (m, p) in enumerate(zip(med, p90, strict=False)):
        ax.text(i - 0.2, m + 1.2, f"{m:.1f}", ha="center", fontsize=8)
        ax.text(i + 0.2, p + 1.2, f"{p:.1f}", ha="center", fontsize=8)
    ax2 = ax.twinx()
    ax2.plot(x, share, color=RED, marker="o", lw=1.6,
             label="share of contexts where the elasticity changes the price (%)")
    ax2.set_ylabel("share of contexts (%)", color=RED)
    ax2.set_ylim(0, 105)
    ax2.grid(False)
    ax.set_xticks(x, [k.replace(" ", "\n", 1) for k in layers], fontsize=8)
    ax.set_ylabel("price variation across elasticity scenarios (%)")
    ax.set_title("At which policy layer does the pricing signal stop deciding the price?")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7.5, loc="upper right")
    save(fig, "fig_07_guardrail_ablation.png")


# --------------------------------------------------------------------------
# 8. real decision context: demand / revenue / gross-profit curves
# --------------------------------------------------------------------------
def fig_decision_context() -> dict:
    from pricing_engine.config import load_config
    from pricing_engine.models.demand_model import load_model
    from pricing_engine.models.hybrid import load_pricing_model
    from pricing_engine.optimization.optimizer import optimize_price
    from pricing_engine.simulation.counterfactual import simulate_price_grid

    cfg = load_config()
    feats = pd.read_parquet(cfg.path("features_table"))
    stats = pd.read_csv(METRICS / "price_variation_upc_store.csv")
    upc, store = 3000006560, 86
    row = feats[(feats["upc"] == upc) & (feats["store"] == store)].sort_values("week").tail(1)
    model = load_pricing_model(
        load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg), cfg=cfg
    )
    srow = stats[(stats["upc"] == upc) & (stats["store"] == store)].iloc[0].to_dict()
    rec = optimize_price(model, row, cfg=cfg, policy_profile="standard",
                         objective="gross_profit", series_stats=srow)
    d = rec.as_dict()
    cost = d["unit_cost_used"]
    p0 = d["current_price"]
    grid = np.round(np.arange(p0 * 0.55, p0 * 1.55, 0.02), 2)
    sim = simulate_price_grid(model, row, grid, unit_cost=cost)
    lo, hi = d["constraints"]["bounds"]

    fig, axes = plt.subplots(1, 3, figsize=(12.4, 3.5), sharex=True)
    series = [
        ("predicted units  Q(p)", sim["predicted_units"], BLUE, "units"),
        ("expected revenue  R(p) = p Q(p)", sim["expected_revenue"], ORANGE, "$"),
        ("expected gross profit  GP(p) = (p-c) Q(p)", sim["expected_gross_profit"], GREEN, "$"),
    ]
    for ax, (title, ydata, colour, unit) in zip(axes, series, strict=False):
        ax.plot(sim["candidate_price"], ydata, color=colour, lw=2)
        ax.axvspan(lo, hi, color=GREEN, alpha=0.10)
        ax.axvline(p0, color=GREY, ls="--", lw=1.2)
        ax.axvline(d["final_recommended_price"], color=RED, ls=":", lw=1.5)
        ax.axvline(cost, color="black", ls="-.", lw=1.0)
        arr = np.asarray(ydata)
        best_i = int(np.argmax(arr))
        ax.plot([float(sim["candidate_price"].iloc[best_i])], [float(arr[best_i])], "o",
                color=colour, ms=6)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("candidate unit price ($)")
        ax.set_ylabel(unit)
    axes[0].text(cost, axes[0].get_ylim()[1] * 0.95, " cost", fontsize=8, rotation=90, va="top")
    axes[0].text(p0, axes[0].get_ylim()[1] * 0.95, " current", fontsize=8, rotation=90,
                 va="top", color=GREY)
    axes[2].text(d["final_recommended_price"], axes[2].get_ylim()[1] * 0.55, " recommended",
                 fontsize=8, rotation=90, va="top", color=RED)
    fig.suptitle(
        f"{d['product_description']} ({upc}), store {store}, week {d['decision_week']} "
        f"({d['decision_week_start_date']}) - elasticity {d['elasticity_used']:.3f} "
        f"({d['elasticity_source']}); shaded band = feasible interval "
        f"${lo:.2f}-${hi:.2f}. MODEL-INTERNAL estimates.",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(OUT / "fig_08_decision_context_curves.png", bbox_inches="tight")
    plt.close(fig)
    print("wrote fig_08_decision_context_curves.png")
    return d


# --------------------------------------------------------------------------
# 9. out-of-time price response
# --------------------------------------------------------------------------
def fig_out_of_time() -> None:
    o = load("out_of_time_price_response.json")
    splits = ["all episodes", "no recorded promotion", "recorded promotion in either week"]
    methods = [
        "null (baseline only, epsilon = 0)",
        "pooled elasticity",
        "shrunk product elasticity",
        "native ML price response",
    ]
    colours = {methods[0]: GREY, methods[1]: ORANGE, methods[2]: GREEN, methods[3]: RED}
    x = np.arange(len(splits))
    w = 0.2
    fig, ax = plt.subplots(figsize=(9.4, 3.5))
    for i, m in enumerate(methods):
        vals = [o["results"][s][m]["wape"] for s in splits]
        ax.bar(x + (i - 1.5) * w, vals, w, label=m, color=colours[m], alpha=0.9)
        for xi, v in zip(x + (i - 1.5) * w, vals, strict=False):
            ax.text(xi, v + 0.008, f"{v:.3f}", ha="center", fontsize=7.5)
    ax.set_xticks(
        x, [s + f"\n(n = {o['results'][s][methods[0]]['n']:,})" for s in splits], fontsize=8.5
    )
    ax.set_ylabel("WAPE (lower is better)")
    ax.set_title(
        f"Out-of-time price-change episodes, weeks {o['evaluation_weeks'][0]}-"
        f"{o['evaluation_weeks'][1]} - predictive, NOT causal validation"
    )
    ax.legend(fontsize=7.5, ncol=2)
    ax.set_ylim(0, 1.0)
    save(fig, "fig_09_out_of_time_price_response.png")


# --------------------------------------------------------------------------
# 10. model comparison
# --------------------------------------------------------------------------
def fig_models() -> None:
    m = load("model_metrics.json")
    names = list(m["results"])
    valid = [m["results"][n]["valid"]["wape"] for n in names]
    test = [m["results"][n].get("test", {}).get("wape") for n in names]
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9.2, 3.4))
    price_aware = [m["results"][n].get("price_aware", False) for n in names]
    ax.bar(x - 0.19, valid, 0.36, color=[BLUE if pa else GREY for pa in price_aware],
           label="validation WAPE")
    ax.bar(x + 0.19, [t if t else 0 for t in test], 0.36,
           color=[GREEN if pa else "#cbd5e0" for pa in price_aware], label="test WAPE")
    for xi, v in zip(x - 0.19, valid, strict=False):
        ax.text(xi, v + 0.012, f"{v:.4f}", ha="center", fontsize=7.5)
    for xi, t in zip(x + 0.19, test, strict=False):
        if t:
            ax.text(xi, t + 0.012, f"{t:.4f}", ha="center", fontsize=7.5)
    ax.set_xticks(x, [n.replace(" ", "\n", 1) for n in names], fontsize=8)
    ax.set_ylabel("WAPE (lower is better)")
    ax.set_title(
        f"Demand-model comparison - selected: {m['selected']} "
        "(lowest validation WAPE among price-aware models)"
    )
    ax.legend(fontsize=8)
    ax.set_ylim(0, 0.95)
    save(fig, "fig_10_model_comparison.png")


# --------------------------------------------------------------------------
# 11. backtest WAPE by week
# --------------------------------------------------------------------------
def fig_backtest() -> None:
    b = load("backtest.json")
    acc = pd.DataFrame(b["accuracy_by_week"])
    fig, ax = plt.subplots(figsize=(9.2, 3.0))
    ax.plot(acc["week"], acc["wape"], marker="o", color=BLUE, lw=1.8)
    ax.axhline(b["accuracy_anchor"]["weekly_wape_mean"], color=RED, ls="--", lw=1.2,
               label=f"mean {b['accuracy_anchor']['weekly_wape_mean']:.4f}")
    for _, r in acc.iterrows():
        ax.text(r["week"], r["wape"] + 0.008, f"{r['wape']:.3f}", ha="center", fontsize=7.5)
    ax.set_xticks(
        acc["week"],
        [f"{int(w)}\n{d[:10]}" for w, d in zip(acc["week"], acc["week_start_date"], strict=False)],
        fontsize=7.5,
    )
    ax.set_ylabel("weekly WAPE")
    ax.set_title("Backtest window: demand accuracy per week (the only outcome-verifiable metric)")
    ax.legend(fontsize=8)
    save(fig, "fig_11_backtest_wape.png")


# --------------------------------------------------------------------------
# 12. elasticity stability
# --------------------------------------------------------------------------
def fig_stability() -> None:
    s = load("elasticity_stability.json")
    wins = list(s["windows"])
    pooled = [s["windows"][w]["pooled"] for w in wins]
    se = [s["windows"][w]["pooled_se_two_way"] for w in wins]
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(10.6, 3.3), gridspec_kw={"width_ratios": [1.25, 1]}
    )
    y = np.arange(len(wins))
    ax1.errorbar(pooled, y, xerr=[1.96 * v for v in se], fmt="o", color=BLUE, capsize=4, ms=5)
    ax1.set_yticks(y, [w.replace(" (", "\n(") for w in wins], fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlabel("pooled elasticity, 95% two-way clustered interval")
    ax1.set_title("Category elasticity is stable across windows")
    pw = s["pairwise"]
    pairs = list(pw)
    rc = [pw[p]["spearman_rank_corr_shrunk"] for p in pairs]
    ax2.bar(pairs, rc, color=[GREEN if v > 0.5 else RED for v in rc], alpha=0.9)
    for i, v in enumerate(rc):
        ax2.text(i, v + (0.02 if v >= 0 else -0.05), f"{v:+.3f}", ha="center", fontsize=8.5)
    ax2.axhline(0, color="black", lw=0.8)
    ax2.set_ylim(-0.25, 1.0)
    ax2.set_ylabel("Spearman rank correlation")
    ax2.set_title(
        "Product-level ordering is NOT stable\n(disjoint windows, 34 common products)", fontsize=9
    )
    save(fig, "fig_12_elasticity_stability.png")


# --------------------------------------------------------------------------
# 13. decision states and the risk gate
# --------------------------------------------------------------------------
def fig_decision_states() -> None:
    a = load("decision_state_audit.json")
    profiles = list(a["profiles"])
    states = ["RECOMMEND_CHANGE", "KEEP_CURRENT", "REVIEW_REQUIRED"]
    colours = {"RECOMMEND_CHANGE": GREEN, "KEEP_CURRENT": GREY, "REVIEW_REQUIRED": ORANGE}
    n = a["n_contexts"]
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(10.4, 3.3), gridspec_kw={"width_ratios": [1.3, 1]}
    )
    bottom = np.zeros(len(profiles))
    for st in states:
        vals = np.array(
            [100 * a["profiles"][p]["decision_counts"].get(st, 0) / n for p in profiles]
        )
        ax1.bar(profiles, vals, 0.55, bottom=bottom, label=st, color=colours[st], alpha=0.9)
        for i, v in enumerate(vals):
            if v > 3:
                ax1.text(i, bottom[i] + v / 2, f"{v:.1f}%", ha="center", va="center",
                         fontsize=8.5, color="white", fontweight="bold")
        bottom += vals
    ax1.set_ylabel("% of contexts")
    ax1.set_title(f"Decision states by policy profile (all {n:,} week-399 contexts)")
    ax1.legend(fontsize=8)
    hr = [a["profiles"][p]["n_high_risk"] for p in profiles]
    hra = [a["profiles"][p]["n_high_risk_actionable"] for p in profiles]
    x = np.arange(len(profiles))
    ax2.bar(x - 0.19, hr, 0.36, color=BLUE, label="HIGH-risk contexts")
    ax2.bar(x + 0.19, hra, 0.36, color=RED, label="HIGH-risk that auto-changed a price")
    for xi, v in zip(x - 0.19, hr, strict=False):
        ax2.text(xi, v + 90, f"{v:,}", ha="center", fontsize=8)
    for xi, v in zip(x + 0.19, hra, strict=False):
        ax2.text(xi, v + 90, f"{v:,}", ha="center", fontsize=8)
    ax2.set_xticks(x, profiles, fontsize=8.5)
    ax2.set_title("The risk gate (aggressive is a DEMO profile)", fontsize=9)
    ax2.legend(fontsize=7.5)
    save(fig, "fig_13_decision_states.png")


# --------------------------------------------------------------------------
# 14. rule-only ablation
# --------------------------------------------------------------------------
def fig_model_value() -> None:
    mv = load("model_value_ablation.json")
    rules = list(mv["rules"])
    within = [
        100 * (1 - mv["rules"][r]["share_final_price_differs_more_than_one_grid_step"])
        for r in rules
    ]
    same = [100 * mv["rules"][r]["share_same_decision_state"] for r in rules]
    x = np.arange(len(rules))
    fig, ax = plt.subplots(figsize=(9.6, 3.4))
    ax.bar(x - 0.19, within, 0.36, color=BLUE,
           label="final price within one 5c grid step of the engine")
    ax.bar(x + 0.19, same, 0.36, color=ORANGE, label="same decision state as the engine")
    for xi, v in zip(x - 0.19, within, strict=False):
        ax.text(xi, v + 1.2, f"{v:.1f}%", ha="center", fontsize=8)
    for xi, v in zip(x + 0.19, same, strict=False):
        ax.text(xi, v + 1.2, f"{v:.1f}%", ha="center", fontsize=8)
    ax.set_xticks(x, [r.replace(" ", "\n", 1) for r in rules], fontsize=8)
    ax.set_ylim(0, 108)
    ax.set_ylabel("% of contexts")
    ax.set_title("Rule-only policies vs the learned engine - decisions compared, never profits")
    ax.legend(fontsize=8)
    save(fig, "fig_14_model_value_ablation.png")


if __name__ == "__main__":
    fig_temporal_split()
    fig_elasticity_ladder()
    fig_inference()
    fig_shrinkage()
    fig_funnel()
    fig_constraints()
    fig_guardrails()
    fig_out_of_time()
    fig_models()
    fig_backtest()
    fig_stability()
    fig_decision_states()
    fig_model_value()
    d = fig_decision_context()
    keys = (
        "upc", "store", "decision_week", "current_price", "unit_cost_used",
        "proposed_candidate_price", "final_recommended_price", "price_change_pct",
        "decision", "risk_level", "elasticity_used", "elasticity_source",
        "predicted_units_current", "predicted_units_recommended",
        "expected_revenue_current", "expected_revenue_recommended",
        "expected_gross_profit_current", "expected_gross_profit_recommended",
        "model_internal_estimated_profit_uplift_pct",
        "model_internal_estimated_revenue_uplift_pct", "reason_codes",
    )
    print(json.dumps({k: d[k] for k in keys}, indent=1))
