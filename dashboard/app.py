"""Streamlit dashboard for the AI Pricing & Revenue Optimization Engine.

    streamlit run dashboard/app.py

Ten pages, all driven by artifacts produced by the pipeline - no numbers are
typed into this file. Anything the pipeline has not produced yet is reported as
missing rather than faked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from pricing_engine.audit import RecommendationLog, RecommendationTransitions  # noqa: E402
from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import recompute_price_features  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import attach_reference_price, load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price  # noqa: E402
from pricing_engine.simulation.counterfactual import simulate_price_grid  # noqa: E402

st.set_page_config(page_title="Pricing & Revenue Optimization Engine", layout="wide", page_icon="💲")

CFG = load_config()
PANEL_COLUMNS = [
    "upc", "store", "week", "week_start_date", "move", "effective_unit_price",
    "revenue", "gross_profit", "gross_margin_rate", "estimated_unit_aac",
    "recorded_promotion_flag", "recorded_promotion_type", "descrip", "size",
    "decision_time_unit_cost", "lag_price_1", "lag_price_2", "roll_mean_price_4",
    "series_reference_price", "lag_move_1", "lag_move_2", "lag_move_3", "lag_move_4",
    "roll_mean_move_4", "roll_mean_move_8", "roll_mean_move_13", "roll_std_move_4",
    "lag_promotion_1", "time_index", "series_age_weeks", "package_size_oz",
    "week_of_year", "month", "quarter", "com_code",
    "log_price", "price_vs_last_week", "price_vs_series_reference", "price_vs_recent_mean",
]

DISCLAIMER = (
    "All counterfactual economics on this dashboard are **model-internal "
    "estimates**: the same fitted price-response model both proposes and scores "
    "candidate prices. Demand at prices that were never charged was never "
    "observed, so none of these figures are realised or causal uplift."
)


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading metrics ...")
def load_metric(name: str) -> dict | None:
    path = CFG.path("metrics_dir") / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(show_spinner="Loading panel ...")
def load_panel() -> pd.DataFrame:
    path = CFG.path("features_table")
    if not path.exists():
        return pd.DataFrame()
    cols = [c for c in PANEL_COLUMNS]
    return pd.read_parquet(path, columns=cols)


@st.cache_data(show_spinner="Loading series statistics ...")
def load_stats() -> pd.DataFrame:
    path = CFG.path("metrics_dir") / "price_variation_upc_store.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


@st.cache_data(show_spinner="Loading elasticities ...")
def load_elasticities() -> pd.DataFrame:
    path = CFG.path("metrics_dir") / "elasticity_by_upc.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


@st.cache_data(show_spinner="Loading recommendation review queue ...")
def load_recommendation_log_with_state() -> pd.DataFrame:
    log = RecommendationLog(CFG.path("recommendation_log"))
    transitions = RecommendationTransitions(CFG.path("recommendation_transitions_log"))
    return log.read_with_state(transitions)


@st.cache_resource(show_spinner="Loading pricing model ...")
def load_demand_model():
    """The configured PRICING model: baseline forecaster + price response."""
    path = CFG.path("models_dir") / "demand_model.joblib"
    if not path.exists():
        return None
    base = load_model(path, cfg=CFG)
    try:
        return load_pricing_model(base, cfg=CFG)
    except Exception:  # noqa: BLE001 - fall back to the native ML response
        return load_pricing_model(base, cfg=CFG, method="ml")


@st.cache_data
def load_report(name: str) -> str | None:
    path = CFG.path("reports_dir") / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def figure(name: str):
    path = CFG.path("figures_dir") / name
    if path.exists():
        st.image(str(path), use_container_width=True)
    else:
        st.info(f"Figure not generated yet: {name}")


def missing(what: str, command: str) -> None:
    st.warning(f"{what} is not available yet. Run `{command}` first.")


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
GLOSSARY = {
    "Elasticity": "How much demand moves when price moves. -2 means a 1% price rise "
                  "loses about 2% of units.",
    "Model-internal estimate": "A number scored by the same model that chose the price. "
                               "Useful for ranking options; not a measured result.",
    "Guardrail": "A business rule the price must respect: maximum change per week, "
                 "minimum margin, staying close to prices seen before.",
    "RECOMMEND_CHANGE / REVIEW_REQUIRED / KEEP_CURRENT": "Change the price / a person must "
        "approve first (thin or risky evidence) / leave it alone.",
    "Traffic driver": "A high-volume product almost every store carries; shoppers judge "
                      "the store's prices by it (often called a key-value item).",
    "WAPE": "Forecast error: total absolute error divided by total actual units.",
    "AAC": "Average acquisition cost: the unit cost implied by the retailer's accounting "
           "margin. A proxy for the true replacement cost.",
}


def page_business() -> None:
    st.title("Business impact")
    st.caption(
        "The commercial answer in one page. Every dollar figure is a **model-internal "
        "estimate**: a range across the demand responses this project has evidence for, "
        "not a measured or causal result."
    )
    value = load_metric("value_sizing.json")
    roles = load_metric("product_roles.json")
    attribution = load_metric("constraint_attribution.json")
    if not value:
        missing("The value sizing", "python scripts/size_value.py")
        return

    rng, base = value["range"], value["baseline"]
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Estimated annual gross-profit gain",
        f"${rng['low_annual_usd'] / 1e3:,.0f}k - ${rng['high_annual_usd'] / 1e3:,.0f}k",
        help="Low and high ends of the elasticity scenarios below.",
    )
    c2.metric("As a share of category gross profit",
              f"{100 * rng['low_uplift_pct']:+.1f}% to {100 * rng['high_uplift_pct']:+.1f}%")
    c3.metric("Category gross profit today (per year)",
              f"${base['annual_category_gross_profit_usd'] / 1e6:,.2f}M",
              help=f"{base['stores']} stores, observed weeks scaled to 52.")

    st.subheader("The same recommended prices, under every evidenced demand response")
    scen = pd.DataFrame(value["scenarios"])
    scen["label"] = [
        f"assumed elasticity {e}" if str(name).startswith("sensitivity") else f"{name} ({e})"
        for name, e in zip(scen["scenario"], scen["assumed_elasticity"], strict=True)
    ]
    scen["kind"] = np.where(scen["scenario"].str.startswith("engine"), "engine's own estimate",
                            "re-scored scenario")
    scen = scen.sort_values("annual_gross_profit_usd")
    fig = px.bar(
        scen, x="annual_gross_profit_usd", y="label", orientation="h", color="kind",
        labels={"annual_gross_profit_usd": "estimated annual gross-profit gain ($)", "label": "",
                "kind": ""},
    )
    fig.update_yaxes(categoryorder="total ascending")
    fig.update_layout(height=320, margin=dict(t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Most recommended changes are price increases, so the more price-sensitive buyers "
        "really are, the less the recommendations are worth. Only the store-randomised "
        "pilot in docs/PRICING_EXPERIMENT.md can say which bar is right."
    )

    if attribution:
        st.subheader("What actually sets the price")
        det = pd.Series(attribution["final_determinant_counts"]).sort_values()
        fig = px.bar(x=det.to_numpy(), y=det.index, orientation="h",
                     labels={"x": "decision contexts", "y": ""})
        fig.update_layout(height=280, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.info(
            f"Only **{100 * attribution['share_determined_by_learned_signal']:.1f}%** of final "
            "recommendations come from the model's own best price. Business guardrails set "
            "the size of most changes; the model mainly sets their direction. Describe this "
            "as rule-bounded pricing with a learned direction, not as AI-set prices."
        )

    if roles:
        st.subheader("By product role")
        tab = pd.DataFrame(roles["roles"])
        show = tab[["role", "products", "revenue_share", "share_actionable",
                    "share_increases_among_actionable", "share_of_estimated_gain"]].copy()
        for col in ("revenue_share", "share_actionable", "share_increases_among_actionable",
                    "share_of_estimated_gain"):
            show[col] = (100 * show[col]).round(1).astype(str) + "%"
        show.columns = ["role", "products", "revenue share", "get a price change",
                        "of which increases", "share of estimated gain"]
        st.dataframe(show, hide_index=True, use_container_width=True)
        w = roles["traffic_cap_what_if"]
        st.warning(
            f"Traffic drivers are the products shoppers judge prices by. Capping their "
            f"increases at +{100 * w['cap']:.0f}% would give up "
            f"{100 * w['share_of_gain_given_up']:.0f}% of the estimated gain "
            f"({100 * w['uplift_full_pct']:+.1f}% -> {100 * w['uplift_capped_pct']:+.1f}%). "
            "That is a price-image decision for the category manager, not the model."
        )

    st.subheader("Recommended next step")
    st.markdown(
        "1. **Pilot, don't roll out.** Randomise stores, apply the engine's actionable "
        "prices on a block of cereal products in treatment stores, compare gross profit per "
        "store-week (design: `docs/PRICING_EXPERIMENT.md`).\n"
        "2. **Staff the review queue.** "
        f"{100 * value['share_review_required']:.0f}% of decisions need a person; the "
        "*Review queue* page is the tool for it.\n"
        "3. **Decide the traffic-driver policy** before the pilot, and stratify the pilot "
        "by product role."
    )
    with st.expander("Glossary"):
        for term, meaning in GLOSSARY.items():
            st.markdown(f"**{term}.** {meaning}")


def page_overview() -> None:
    st.title("Executive overview")
    st.caption(DISCLAIMER)

    eda = load_metric("eda_summary.json")
    model = load_metric("model_metrics.json")
    recs = load_metric("recommendations.json")
    if not eda:
        missing("The EDA summary", "python scripts/run_eda.py")
        return

    s = eda["scale"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Historical observed revenue", f"${s['total_revenue']/1e6:,.1f}M")
    c2.metric("Historical observed gross profit", f"${s['total_gross_profit']/1e6:,.1f}M")
    c3.metric("Products x stores analysed", f"{s['upcs']:,} x {s['stores']:,}")
    c4.metric("Weeks covered", f"{s['weeks']:,}")

    c1, c2, c3, c4 = st.columns(4)
    if model:
        sel = model["selected"]
        test = model["results"][sel].get("test", {})
        c1.metric("Selected model", sel)
        c2.metric("Test WAPE", f"{test.get('wape', float('nan')):.4f}")
    pv = eda["price_variation"]["upc_store_series"]
    c3.metric("Series eligible for pricing", f"{pv['n_eligible']:,} / {pv['n_series']:,}")
    if recs:
        c4.metric(
            "Model-internal estimated portfolio uplift",
            f"{100*recs['portfolio_model_internal_estimated_profit_uplift_pct']:+.2f}%",
            help="Internal simulation only. Not realised, not causal, not an unbiased policy value.",
        )

    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("Weekly observed revenue and gross profit")
        figure("eda_weekly_revenue_profit.png")
    with right:
        st.subheader("Weekly observed units")
        figure("eda_weekly_units.png")

    if recs:
        st.subheader("Latest batch recommendation run")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contexts scored", f"{recs['n_contexts']:,}")
        c2.metric("Actionable changes", f"{100*recs['share_actionable']:.1f}%")
        c3.metric("Review required", f"{100*recs['share_review_required']:.1f}%")
        c4.metric("Keep current price", f"{100*recs['share_keep_current']:.1f}%")
        st.caption(
            f"Price response: **{recs.get('price_response_method', 'n/a')}** | "
            f"HIGH-risk contexts that still auto-changed a price: "
            f"**{recs.get('high_risk_actionable', 0)}** of {recs.get('high_risk_contexts', 0):,} "
            "(must be zero under the default policy profiles)."
        )
        codes = pd.Series(recs["reason_code_counts"]).sort_values(ascending=False)
        # Plotly is used throughout instead of st.bar_chart / st.line_chart: those
        # helpers pull in Altair, which does not import on Python 3.13 here.
        st.plotly_chart(
            px.bar(x=codes.index, y=codes.to_numpy(), labels={"x": "reason code", "y": "count"}),
            use_container_width=True,
        )


def page_explorer(panel: pd.DataFrame) -> None:
    st.title("Product / store explorer")
    if panel.empty:
        missing("The feature panel", "python scripts/build_features.py")
        return

    products = (
        panel.groupby("upc", observed=True)
        .agg(descrip=("descrip", "first"), units=("move", "sum"))
        .sort_values("units", ascending=False)
        .reset_index()
    )
    labels = {int(r.upc): f"{r.descrip} ({int(r.upc)})" for r in products.itertuples()}
    upc = st.selectbox("Product", options=list(labels), format_func=lambda u: labels[u])
    stores = sorted(panel.loc[panel["upc"] == upc, "store"].unique())
    store = st.selectbox("Store", options=stores)

    series = panel[(panel["upc"] == upc) & (panel["store"] == store)].sort_values("week")
    if series.empty:
        st.info("No observations for this combination.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Observations", f"{len(series):,}")
    c2.metric("Observed revenue", f"${series['revenue'].sum():,.0f}")
    c3.metric("Observed gross profit", f"${series['gross_profit'].sum():,.0f}")
    c4.metric("Mean gross margin", f"{100*series['gross_margin_rate'].mean():.1f}%")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=series["week_start_date"], y=series["effective_unit_price"],
                             name="effective unit price", yaxis="y1"))
    fig.add_trace(go.Scatter(x=series["week_start_date"], y=series["estimated_unit_aac"],
                             name="estimated unit AAC", yaxis="y1", line=dict(dash="dot")))
    fig.add_trace(go.Bar(x=series["week_start_date"], y=series["move"], name="units sold",
                         yaxis="y2", opacity=0.35))
    promo = series[series["recorded_promotion_flag"] == 1]
    fig.add_trace(go.Scatter(x=promo["week_start_date"], y=promo["effective_unit_price"],
                             mode="markers", name="recorded promotion",
                             marker=dict(size=7, symbol="triangle-down")))
    fig.update_layout(
        height=470,
        yaxis=dict(title="price / cost ($)"),
        yaxis2=dict(title="units", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.12),
        margin=dict(t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Triangles mark weeks with a recorded promotion code. Absence of a marker "
        "does NOT prove there was no promotion - the coding is incomplete."
    )
    with st.expander("Raw series"):
        st.dataframe(series[["week", "week_start_date", "effective_unit_price", "move",
                             "revenue", "gross_profit", "gross_margin_rate",
                             "estimated_unit_aac", "recorded_promotion_type"]], height=320)


def page_pricing_demand(panel: pd.DataFrame, stats: pd.DataFrame) -> None:
    st.title("Pricing and demand")
    eda = load_metric("eda_summary.json")
    if not eda or panel.empty:
        missing("EDA outputs", "python scripts/run_eda.py")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Mean effective unit price", f"${eda['scale']['mean_effective_unit_price']:.2f}")
    c2.metric("Rows with a promotion code", f"{100*eda['scale']['share_recorded_promotion_rows']:.2f}%")
    c3.metric("Median cross-store price spread",
              f"{eda['store_price_dispersion']['median_cross_store_spread_pct']:.1f}%")

    figure("eda_price_distribution.png")
    figure("eda_price_vs_demand.png")
    st.caption("The scatter above is an association, not a demand curve: price and demand are "
               "jointly determined by promotions, seasonality and retailer decisions.")

    if not stats.empty:
        st.subheader("Within-series price variation")
        fig = px.histogram(stats, x="price_cv", nbins=80, range_x=[0, 0.4],
                           labels={"price_cv": "price coefficient of variation"})
        fig.add_vline(x=float(CFG.get("eligibility.min_price_cv")), line_dash="dash", line_color="crimson")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            stats.sort_values("total_units", ascending=False)
            .head(200)[["upc", "store", "n_obs", "n_distinct_prices", "price_cv",
                        "price_min", "price_max", "n_price_changes", "eligible"]],
            height=320,
        )


def page_elasticity(elas: pd.DataFrame) -> None:
    st.title("Elasticity analysis")
    data = load_metric("elasticity.json")
    if not data:
        missing("Elasticity results", "python scripts/run_elasticity.py")
        return

    st.subheader("Log-log demand models")
    fits = pd.DataFrame(data["loglog"])
    st.dataframe(
        fits[["model", "price_elasticity", "std_error", "ci_low", "ci_high", "r_squared", "n_obs", "controls"]],
        use_container_width=True,
    )
    st.info(
        "The naive pooled coefficient and the fixed-effects coefficient differ by a factor of "
        f"{abs(fits['price_elasticity'].iloc[2] / fits['price_elasticity'].iloc[0]):.1f}. "
        "That gap is the clearest evidence that the raw association is not a causal effect."
    )

    c1, c2, c3 = st.columns(3)
    het = data["heterogeneity"]
    c1.metric("Median per-UPC elasticity", f"{het['upc_elasticity_median']:.2f}")
    c2.metric("p10 / p90", f"{het['upc_elasticity_p10']:.2f} / {het['upc_elasticity_p90']:.2f}")
    c3.metric("UPCs with wrong-signed significant coefficient",
              f"{100*het['share_upcs_wrong_sign_significant']:.1f}%")

    figure("elasticity_by_upc_hist.png")
    figure("elasticity_arc_hist.png")
    if not elas.empty:
        st.dataframe(elas.sort_values("elasticity")[["upc", "descrip", "elasticity", "std_error",
                                                     "t_stat", "n_obs", "r_squared"]], height=320)


def page_simulator(panel: pd.DataFrame, stats: pd.DataFrame, model) -> None:
    st.title("Price simulator")
    st.caption(DISCLAIMER)
    if model is None or panel.empty:
        missing("The trained model", "python scripts/train.py")
        return

    latest_week = int(panel["week"].max())
    recent = panel[panel["week"] == latest_week]
    products = (
        recent.groupby("upc", observed=True).agg(descrip=("descrip", "first")).reset_index()
    )
    labels = {int(r.upc): f"{r.descrip} ({int(r.upc)})" for r in products.itertuples()}
    upc = st.selectbox("Product", options=list(labels), format_func=lambda u: labels[u])
    store = st.selectbox("Store", options=sorted(recent.loc[recent["upc"] == upc, "store"].unique()))

    row = recent[(recent["upc"] == upc) & (recent["store"] == store)].head(1)
    if row.empty:
        st.info("No decision context for this combination in the latest week.")
        return

    current_price = float(row["effective_unit_price"].iloc[0])
    cost = float(row["decision_time_unit_cost"].iloc[0])
    srow = stats[(stats["upc"] == upc) & (stats["store"] == store)]
    hist_min = float(srow["price_min"].iloc[0]) if len(srow) else np.nan
    hist_max = float(srow["price_max"].iloc[0]) if len(srow) else np.nan

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current price", f"${current_price:.2f}")
    c2.metric("Decision-time unit cost (lagged AAC)", f"${cost:.2f}" if np.isfinite(cost) else "unavailable")
    c3.metric("Observed price support", f"${hist_min:.2f} - ${hist_max:.2f}" if np.isfinite(hist_min) else "n/a")
    method = getattr(getattr(model, "method", None), "value", "ml")
    if method != "ml":
        eps = float(model.elasticity_for(row)[0])
        src = str(model.elasticity_source(row)[0])
        c4.metric("Elasticity applied", f"{eps:.2f}", help=f"source: {src} | method: {method}")
    else:
        c4.metric("Price response", "native ML")

    candidate = st.slider(
        "Candidate price ($)",
        min_value=float(round(current_price * 0.6, 2)),
        max_value=float(round(current_price * 1.4, 2)),
        value=float(current_price),
        step=0.05,
    )
    grid = np.round(np.arange(current_price * 0.6, current_price * 1.4 + 0.01, 0.05), 2)
    sim = simulate_price_grid(model, row, grid, unit_cost=cost if np.isfinite(cost) and cost > 0 else None)

    # Stamp the reference price (the observed price) before substituting the
    # candidate: the elasticity response is measured relative to p0.
    point = recompute_price_features(attach_reference_price(row), np.array([candidate]))
    units_at_candidate = float(model.predict(point)[0])
    rev = candidate * units_at_candidate
    gp = (candidate - cost) * units_at_candidate if np.isfinite(cost) else float("nan")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Predicted units", f"{units_at_candidate:,.1f}")
    c2.metric("Expected revenue", f"${rev:,.2f}")
    c3.metric("Expected gross profit", f"${gp:,.2f}" if np.isfinite(gp) else "n/a")
    c4.metric("Price change", f"{100*(candidate/current_price - 1):+.1f}%")

    if np.isfinite(hist_min) and (candidate < hist_min or candidate > hist_max):
        st.error(
            f"Extrapolation warning: ${candidate:.2f} is outside this series' observed price "
            f"support (${hist_min:.2f} - ${hist_max:.2f}). The model has no data there."
        )
    if np.isfinite(cost) and candidate < cost:
        st.error(f"Constraint violated: candidate price is below the decision-time unit cost (${cost:.2f}).")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sim["candidate_price"], y=sim["predicted_units"], name="predicted units"))
    fig.add_trace(go.Scatter(x=sim["candidate_price"], y=sim["expected_revenue"],
                             name="expected revenue", yaxis="y2"))
    fig.add_trace(go.Scatter(x=sim["candidate_price"], y=sim["expected_gross_profit"],
                             name="expected gross profit", yaxis="y2"))
    fig.add_vline(x=current_price, line_dash="dash", line_color="gray", annotation_text="current")
    fig.add_vline(x=candidate, line_dash="dot", line_color="crimson", annotation_text="candidate")
    fig.update_layout(height=460, yaxis=dict(title="units"),
                      yaxis2=dict(title="$", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=1.12), margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)


def page_recommendation(panel: pd.DataFrame, stats: pd.DataFrame, model) -> None:
    st.title("Recommendation engine")
    st.caption(DISCLAIMER)
    if model is None or panel.empty:
        missing("The trained model", "python scripts/train.py")
        return

    latest_week = int(panel["week"].max())
    recent = panel[panel["week"] == latest_week]
    products = recent.groupby("upc", observed=True).agg(descrip=("descrip", "first")).reset_index()
    labels = {int(r.upc): f"{r.descrip} ({int(r.upc)})" for r in products.itertuples()}

    c1, c2, c3 = st.columns(3)
    upc = c1.selectbox("Product", options=list(labels), format_func=lambda u: labels[u])
    store = c2.selectbox("Store", options=sorted(recent.loc[recent["upc"] == upc, "store"].unique()))
    profile = c3.selectbox("Policy profile (DEMO setting)", ["conservative", "standard", "aggressive"], index=1)
    objective = st.radio("Objective", ["gross_profit", "revenue"], horizontal=True)

    row = recent[(recent["upc"] == upc) & (recent["store"] == store)].head(1)
    if row.empty:
        st.info("No decision context available.")
        return
    srow = stats[(stats["upc"] == upc) & (stats["store"] == store)]
    series_stats = srow.iloc[0].to_dict() if len(srow) else {}

    rec = optimize_price(model, row, cfg=CFG, policy_profile=profile,
                         objective=objective, series_stats=series_stats)
    d = rec.as_dict()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current price", f"${d['current_price']:.2f}")
    c2.metric(
        "Proposed candidate price",
        f"${d['proposed_candidate_price']:.2f}",
        f"{100*d['proposed_price_change_pct']:+.2f}%",
        help="The optimizer's proposal. It only becomes a business price when "
             "the decision state is RECOMMEND_CHANGE.",
    )
    c3.metric(
        "FINAL recommended price",
        f"${d['final_recommended_price']:.2f}",
        f"{100*d['price_change_pct']:+.2f}%",
        help="What would actually be charged under this policy: the current "
             "price whenever the decision is not actionable.",
    )
    c4.metric("Decision state", d["decision"], help="Only RECOMMEND_CHANGE is actionable.")
    st.caption(
        f"Risk level: **{d['risk_level']}** - heuristic risk score (HIGH = risky), "
        "NOT calibrated confidence. HIGH risk is gated under the default policy profiles."
    )

    if d["decision"] == "REVIEW_REQUIRED":
        st.warning(
            "**REVIEW_REQUIRED** - this context is HIGH risk, so the proposal is "
            "NOT applied automatically. A human must approve it. The final price "
            f"stays at ${d['final_recommended_price']:.2f}."
        )
    elif d["decision"] == "KEEP_CURRENT":
        st.info("**KEEP_CURRENT** - the engine recommends leaving this price alone.")
    else:
        st.success("**RECOMMEND_CHANGE** - actionable under the selected policy profile.")

    if d.get("price_response_method"):
        detail = f"price response: **{d['price_response_method']}**"
        if d.get("elasticity_used") is not None:
            detail += f" | elasticity **{d['elasticity_used']:.3f}** ({d['elasticity_source']})"
        st.caption(detail)

    c1, c2, c3 = st.columns(3)
    c1.metric("Predicted units",
              f"{d['predicted_units_current']:.1f} -> {d['predicted_units_recommended']:.1f}")
    c2.metric("Expected revenue",
              f"${d['expected_revenue_current']:.2f} -> ${d['expected_revenue_recommended']:.2f}")
    if d["expected_gross_profit_current"] is not None:
        c3.metric(
            "Expected gross profit",
            f"${d['expected_gross_profit_current']:.2f} -> ${d['expected_gross_profit_recommended']:.2f}",
            f"{100*(d['model_internal_estimated_profit_uplift_pct'] or 0):+.2f}% (model-internal estimate)",
        )

    st.write("**Reason codes:** " + ", ".join(f"`{c}`" for c in d["reason_codes"]))
    if d.get("risk_notes"):
        for note in d["risk_notes"]:
            st.write(f"- _{note}_")
    bounds = d["constraints"].get("bounds")
    if bounds:
        st.write(f"**Feasible price range:** ${bounds[0]:.2f} - ${bounds[1]:.2f}")
    with st.expander("Constraints and diagnostics"):
        st.json({"constraints": d["constraints"], "diagnostics": d["diagnostics"]})

    cost = float(row["decision_time_unit_cost"].iloc[0])
    grid = np.round(np.arange(d["current_price"] * 0.7, d["current_price"] * 1.3 + 0.01, 0.05), 2)
    sim = simulate_price_grid(model, row, grid, unit_cost=cost if np.isfinite(cost) and cost > 0 else None)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sim["candidate_price"], y=sim["predicted_units"], name="predicted demand"))
    fig.add_trace(go.Scatter(x=sim["candidate_price"], y=sim["expected_revenue"], name="expected revenue", yaxis="y2"))
    fig.add_trace(go.Scatter(x=sim["candidate_price"], y=sim["expected_gross_profit"], name="expected gross profit", yaxis="y2"))
    fig.add_vline(x=d["current_price"], line_dash="dash", line_color="gray", annotation_text="current")
    fig.add_vline(x=d["proposed_candidate_price"], line_dash="dot", line_color="crimson",
                  annotation_text="proposed candidate")
    if not d["actionable"]:
        fig.add_annotation(x=d["proposed_candidate_price"], y=0, yshift=-28, showarrow=False,
                           text="not actionable", font=dict(color="crimson", size=11))
    if bounds:
        fig.add_vrect(x0=bounds[0], x1=bounds[1], fillcolor="green", opacity=0.06, line_width=0,
                      annotation_text="feasible range")
    fig.update_layout(height=460, yaxis=dict(title="units"),
                      yaxis2=dict(title="$", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=1.12), margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    log_path = CFG.path("recommendation_log")
    if log_path.exists():
        with st.expander("Recommendation audit log (most recent 200 rows)"):
            st.dataframe(RecommendationLog(log_path).read().tail(200), height=320)


@st.cache_resource(show_spinner="Starting the pricing copilot ...")
def load_copilot():
    """The live copilot, or None when no OPENAI_API_KEY / genai extra is available."""
    from pricing_engine.copilot.agent import build_copilot
    from pricing_engine.serving import build_state

    try:
        state = build_state(CFG)
    except FileNotFoundError:
        return None
    return build_copilot(state)


COPILOT_EXAMPLES = [
    "Which products can you price? Show me a few.",
    "What price do you recommend for the first product in its first store, and why?",
    "Compare the conservative, standard and aggressive policies for that product.",
    "Across all products this week, how many recommendations need a human review?",
    "Prove that raising the price will increase profit.",
]


def page_copilot() -> None:
    st.title("Pricing Copilot")
    st.caption(
        "Ask in plain English. The language model never prices anything and never supplies "
        "a number: it calls the same engine tools as the API, and an automatic check rejects "
        "any answer containing a number that did not come from a tool, or a causal or "
        "guaranteed-outcome claim. See docs/COPILOT_CARD.md."
    )
    copilot = load_copilot()
    if copilot is None:
        st.info(
            "The copilot is switched off here: it needs an OpenAI API key "
            "(`OPENAI_API_KEY`) and `pip install -e \".[genai]\"`. Everything else in this "
            "dashboard works without it."
        )
        st.markdown("**Questions it is built and evaluated for:**\n"
                    + "\n".join(f"* {q}" for q in COPILOT_EXAMPLES))
        return

    history = st.session_state.setdefault("copilot_history", [])
    for turn in history:
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            st.markdown(turn["answer"])
    st.caption("Try: " + " | ".join(COPILOT_EXAMPLES[:3]))
    question = st.chat_input("Ask about a product, a store, or the portfolio")
    if not question:
        return
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Calling the engine ..."):
            out = copilot.ask(question[:1000])
        st.markdown(out.answer)
        badge = {"passed": "Checks passed", "passed_after_regeneration":
                 "Checks passed after one rewrite", "fallback_template":
                 "Model answer rejected; showing a template built from the engine's results"}
        st.caption(f"{badge[out.status]} | model {out.model} | {out.latency_ms} ms")
        with st.expander("Engine calls behind this answer (the facts)"):
            for call in out.tool_calls:
                st.markdown(f"**{call.name}** `{json.dumps(call.arguments)}`")
                st.json(call.result, expanded=False)
    history.append({"question": question, "answer": out.answer})


def page_review_queue() -> None:
    st.title("Review queue")
    st.caption(
        "Human-in-the-loop workflow for the recommendation audit log: "
        "GENERATED -> REVIEWED -> APPROVED / REJECTED -> PUBLISHED. "
        "`recommendation_log.csv` is never rewritten - every decision here is "
        "recorded as a new row in `recommendation_transitions.csv`."
    )
    log_path = CFG.path("recommendation_log")
    if not log_path.exists():
        missing("The recommendation log", "python scripts/optimize.py --batch 3000")
        return

    frame = load_recommendation_log_with_state()
    if frame.empty:
        st.info("The recommendation log exists but has no rows yet.")
        return

    pending = frame[~frame["lifecycle_state_current"].isin({"REJECTED", "PUBLISHED"})]
    c1, c2, c3 = st.columns(3)
    c1.metric("Total recommendations", f"{len(frame):,}")
    c2.metric("Pending review/action", f"{len(pending):,}")
    c3.metric("Published", f"{int((frame['lifecycle_state_current'] == 'PUBLISHED').sum()):,}")

    show_cols = [
        "rec_id", "lifecycle_state_current", "upc", "store", "decision_week",
        "decision", "risk_level", "final_recommended_price", "price_change_pct",
    ]
    show_cols = [c for c in show_cols if c in pending.columns]
    st.dataframe(pending[show_cols].head(500), height=320, use_container_width=True)

    if pending.empty:
        st.info("Nothing pending - every recommendation has reached a terminal state.")
        return

    st.subheader("Act on one recommendation")
    rec_id = st.selectbox("rec_id", options=pending["rec_id"].tolist())
    current_row = pending[pending["rec_id"] == rec_id].iloc[0]
    st.caption(
        f"UPC {current_row['upc']} | store {current_row['store']} | "
        f"decision {current_row['decision']} | current state "
        f"**{current_row['lifecycle_state_current']}**"
    )

    actor = st.text_input("Actor (your name)", key="review_actor")
    note = st.text_input("Note (required to reject)", key="review_note")
    transitions = RecommendationTransitions(CFG.path("recommendation_transitions_log"))
    known_ids = set(frame["rec_id"])

    def _act(to_state: str) -> None:
        try:
            transitions.append_transition(
                rec_id, to_state=to_state, actor=actor or None, note=note or None,
                valid_rec_ids=known_ids,
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        st.success(f"{rec_id}: -> {to_state}")
        load_recommendation_log_with_state.clear()
        st.rerun()

    b1, b2, b3, b4 = st.columns(4)
    if b1.button("Mark reviewed"):
        _act("REVIEWED")
    if b2.button("Approve"):
        _act("APPROVED")
    if b3.button("Reject"):
        if not note:
            st.error("A note is required to reject a recommendation.")
        else:
            _act("REJECTED")
    if b4.button("Publish"):
        _act("PUBLISHED")

    with st.expander("Transition history for this rec_id"):
        hist = transitions.read()
        hist = hist[hist["rec_id"] == rec_id] if not hist.empty else hist
        st.dataframe(hist, use_container_width=True)


def page_model_performance() -> None:
    st.title("Model performance")
    metrics = load_metric("model_metrics.json")
    if not metrics:
        missing("Model metrics", "python scripts/train.py")
        return

    split = metrics["split"]
    st.write(
        f"**Temporal split** - train weeks {split['train_weeks']} ({split['train_dates'][0]} .. "
        f"{split['train_dates'][1]}), validation {split['valid_weeks']}, test {split['test_weeks']}. "
        "No random splitting is used anywhere."
    )

    rows = []
    for name, r in metrics["results"].items():
        rows.append(
            {
                "model": name,
                "price aware": r.get("price_aware", False),
                "valid WAPE": r["valid"]["wape"],
                "valid MAE": r["valid"]["mae"],
                "test WAPE": r.get("test", {}).get("wape"),
                "test MAE": r.get("test", {}).get("mae"),
                "implied elasticity": (r.get("implied_elasticity") or {}).get("median"),
            }
        )
    st.dataframe(pd.DataFrame(rows).sort_values("valid WAPE"), use_container_width=True)
    st.success(f"Selected model: **{metrics['selected']}** (lowest validation WAPE among price-aware models)")

    figure("model_actual_vs_predicted_weekly.png")
    c1, c2 = st.columns(2)
    with c1:
        figure("model_scatter.png")
    with c2:
        figure("model_residuals.png")

    seg = metrics["results"][metrics["selected"]].get("segments", {})
    if seg:
        st.subheader("Error by segment (test window)")
        c1, c2 = st.columns(2)
        with c1:
            st.write("By recorded promotion state")
            st.dataframe(pd.DataFrame(seg["by_promo"]), use_container_width=True)
        with c2:
            st.write("By within-series price variation")
            st.dataframe(pd.DataFrame(seg["by_price_variation"]), use_container_width=True)

    pr = load_metric("price_response.json")
    if pr:
        st.subheader("Price-response validation")
        s = pr["summary"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Curves monotone decreasing", f"{100*s['share_monotone_decreasing']:.1f}%")
        c2.metric("Median implied elasticity", f"{s['median_local_elasticity']:.2f}")
        c3.metric("Flat (price-insensitive) curves", f"{100*s['share_flat_response']:.2f}%")
        figure("price_response_demand_curves.png")

    cmp = load_metric("price_response_comparison.json")
    if cmp:
        st.subheader("Price-response method comparison (Phase L)")
        st.dataframe(pd.DataFrame(cmp["per_method"]).T, use_container_width=True)
        st.dataframe(pd.DataFrame(cmp["pairwise_disagreement"]), use_container_width=True)
        st.caption(
            "The same baseline demand model and constraints produce different prices "
            "depending only on the price-response assumption. See "
            "reports/08_PRICE_RESPONSE_COMPARISON.md."
        )
        figure("compare_demand_profit_curves.png")
        figure("compare_elasticity_sensitivity.png")

    elas_est = load_metric("elasticity_estimation.json")
    if elas_est:
        st.subheader("Elasticity estimation (training weeks only)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pooled controlled", f"{elas_est['pooled_elasticity']:.3f}")
        c2.metric("UPC x store FE", f"{elas_est['fe_elasticity']:.3f}")
        c3.metric("Products usable", f"{elas_est['n_products_usable']:,}/{elas_est['n_products']:,}")
        c4.metric("Mean shrinkage weight", f"{elas_est['mean_shrinkage_weight']:.2f}")

    bt = load_metric("backtest.json")
    if bt:
        st.subheader("Offline policy comparison (model-internal estimate)")
        st.dataframe(pd.DataFrame(bt["policy_totals"]), use_container_width=True)
        figure("backtest_wape_by_week.png")


def page_data_quality() -> None:
    st.title("Data quality")
    audit = load_metric("build_audit.json")
    raw = load_metric("raw_audit.json")
    fp = load_metric("dataset_fingerprint.json")
    if not audit:
        missing("The data audit", "python scripts/build_dataset.py")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw rows", f"{audit['raw_rows']:,}")
    c2.metric("Canonical rows", f"{audit['processed_rows']:,}")
    c3.metric("Rows removed", f"{audit['rows_removed']:,}",
              f"{100*audit['rows_removed']/audit['raw_rows']:.1f}%")
    c4.metric("Duplicate grain rows", f"{audit['valid_duplicate_grain_rows']:,}")

    st.subheader("Every exclusion rule, counted")
    st.dataframe(pd.DataFrame(audit["exclusions"]), use_container_width=True)

    st.subheader("Metadata join quality")
    st.json(audit["metadata_join"])

    if raw:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("ok flag (manual: 1 = valid, 0 = suspect)")
            st.dataframe(pd.Series(raw["movement"]["ok_value_counts"], name="rows"))
        with c2:
            st.subheader("Recorded sale codes")
            st.dataframe(pd.Series(raw["movement"]["sale_code_counts"], name="rows"))
            st.caption("B = Bonus Buy, C = Coupon, S = simple price reduction. "
                       "G and L are undocumented codes found in the Cereals file.")

    st.subheader("Formula and structural checks")
    checks = audit.get("checks", [])
    if checks:
        cf = pd.DataFrame(checks)
        st.dataframe(cf, use_container_width=True)
        st.success(f"{int(cf['passed'].sum())} / {len(cf)} checks pass")

    zero = load_metric("zero_price_audit.json")
    if zero:
        st.subheader("Zero-price exclusion audit (Phase L)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Rows with price = 0", f"{zero['zero_price_rows']:,}",
                  f"{100*zero['zero_price_share']:.1f}% of raw")
        c2.metric("...of which recorded any sales", f"{zero['zero_price_and_move_gt_0']:,}")
        pos = zero["position_within_series"]
        c3.metric("Leading / trailing runs",
                  f"{100*(pos['share_leading'] + pos['share_trailing']):.0f}%",
                  help="Share of zero-price rows before the first or after the last priced week.")
        st.caption("Full evidence: reports/09_ZERO_PRICE_AUDIT.md")

    leak = load_metric("cost_leakage_audit.json")
    if leak:
        st.subheader("Decision-time cost leakage audit (Phase L)")
        st.json(
            {
                "identity_check_passed": leak["identity_check"]["passed"],
                "future_poisoning_passed": leak["poisoning_check"]["passed"],
                "recommendation_check_passed": leak["recommendation_check"]["passed"],
                "all_passed": leak["all_passed"],
            }
        )

    if fp:
        st.subheader("Reproducibility")
        st.json({"parquet_sha256": fp["parquet_sha256"],
                 "dataframe_fingerprint": fp["dataframe_fingerprint"],
                 "environment": fp["environment"]})


def page_methodology() -> None:
    st.title("Methodology and limitations")
    st.error(
        "**This engine does not measure causal price effects.** Dominick's prices were set by "
        "the retailer, not randomised. Every counterfactual number here is a model estimate."
    )

    tabs = st.tabs(["Limitations", "Causal", "Pricing science", "Data source", "Model card", "Reports"])
    docs_dir = REPO_ROOT / "docs"

    def show(path: Path, fallback: str) -> None:
        if path.exists():
            st.markdown(path.read_text(encoding="utf-8"))
        else:
            st.info(fallback)

    with tabs[0]:
        show(REPO_ROOT / "KNOWN_LIMITATIONS.md", "KNOWN_LIMITATIONS.md not found.")
    with tabs[1]:
        show(docs_dir / "CAUSAL_LIMITATIONS.md", "docs/CAUSAL_LIMITATIONS.md not found.")
    with tabs[2]:
        show(docs_dir / "PRICING_SCIENCE.md", "docs/PRICING_SCIENCE.md not found.")
    with tabs[3]:
        show(docs_dir / "DATA_SOURCE.md", "docs/DATA_SOURCE.md not found.")
    with tabs[4]:
        show(docs_dir / "MODEL_CARD.md", "docs/MODEL_CARD.md not found.")
    with tabs[5]:
        name = st.selectbox(
            "Report",
            [
                "01_DATA_AUDIT.md", "02_PRICING_EDA.md", "03_ELASTICITY_ANALYSIS.md",
                "04_MODEL_COMPARISON.md", "05_PRICE_RESPONSE_VALIDATION.md",
                "06_BACKTEST.md", "07_RECOMMENDATION_SUMMARY.md",
                "08_PRICE_RESPONSE_COMPARISON.md", "09_ZERO_PRICE_AUDIT.md",
                "10_COST_LEAKAGE_AUDIT.md", "MONITORING_DESIGN.md", "VALIDATION_SUMMARY.md",
            ],
        )
        text = load_report(name)
        if text:
            st.markdown(text)
        else:
            st.info(f"{name} has not been generated yet.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
PAGES = {
    "1. Business impact": "business",
    "2. Executive overview": "overview",
    "3. Product / store explorer": "explorer",
    "4. Pricing & demand": "pricing",
    "5. Elasticity analysis": "elasticity",
    "6. Price simulator": "simulator",
    "7. Recommendation engine": "recommendation",
    "8. Model performance": "model",
    "9. Data quality": "quality",
    "10. Methodology & limitations": "methodology",
    "11. Review queue": "review_queue",
    "12. Pricing Copilot": "copilot",
}


def data_banner() -> None:
    """Persistent provenance label: synthetic demo data is never unlabelled."""
    if CFG.is_synthetic:
        st.error(
            f"**{CFG.data_label}.** This public demo runs the unchanged engine on a "
            "generated panel with known elasticities, because the Dominick's licence "
            "forbids redistributing the real data. Numbers here describe the synthetic "
            "panel only; the real results are in the repository's reports."
        )


def main() -> None:
    st.sidebar.title("Pricing engine")
    if CFG.is_synthetic:
        st.sidebar.error(CFG.data_label)
    else:
        st.sidebar.caption("Dominick's Finer Foods - Cereals\nKilts Center, Chicago Booth")
    choice = st.sidebar.radio("Page", list(PAGES))
    st.sidebar.divider()
    st.sidebar.warning(
        "Counterfactual economics shown anywhere in this app are MODEL-INTERNAL "
        "estimates - never realised or causal uplift. HIGH risk means risky: "
        "those contexts are gated to REVIEW_REQUIRED by default."
    )

    data_banner()
    key = PAGES[choice]
    if key == "business":
        page_business()
    elif key == "overview":
        page_overview()
    elif key == "explorer":
        page_explorer(load_panel())
    elif key == "pricing":
        page_pricing_demand(load_panel(), load_stats())
    elif key == "elasticity":
        page_elasticity(load_elasticities())
    elif key == "simulator":
        page_simulator(load_panel(), load_stats(), load_demand_model())
    elif key == "recommendation":
        page_recommendation(load_panel(), load_stats(), load_demand_model())
    elif key == "model":
        page_model_performance()
    elif key == "quality":
        page_data_quality()
    elif key == "methodology":
        page_methodology()
    elif key == "copilot":
        page_copilot()
    else:
        page_review_queue()


main()
