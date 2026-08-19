"""Feature construction under a strict decision-time availability contract.

The rule enforced everywhere in this module:

    A feature may only use information that a pricing analyst would already
    have when setting next week's price for a given UPC in a given store.

Concretely:

* the candidate **price** itself is a decision variable (known by
  construction);
* the **planned promotion** flag is treated as known at decision time (a Bonus
  Buy is scheduled, not discovered);
* everything else about the target week - realised units, revenue, gross
  profit, that week's accounting margin and the implied AAC - is **outcome
  information** and is never used as an input;
* all lag / rolling features are shifted by at least one week, so week *t*
  never sees its own value.

`docs/FEATURE_AVAILABILITY.md` and `docs/DATA_LEAKAGE_AUDIT.md` classify every
column; `tests/test_features.py` asserts the shifting numerically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pricing_engine.config import Config, load_config

SERIES_KEYS = ["upc", "store"]

#: Features that change when a candidate price changes. The counterfactual
#: simulator MUST recompute exactly these and nothing else.
PRICE_DEPENDENT_FEATURES: tuple[str, ...] = (
    "effective_unit_price",
    "log_price",
    "price_vs_last_week",
    "price_vs_series_reference",
    "price_vs_recent_mean",
)

#: Context features (fixed while a candidate price is varied).
CONTEXT_FEATURES: tuple[str, ...] = (
    "store",
    "upc",
    "com_code",
    "week_of_year",
    "month",
    "quarter",
    "time_index",
    "recorded_promotion_flag",
    "lag_move_1",
    "lag_move_2",
    "lag_move_3",
    "lag_move_4",
    "roll_mean_move_4",
    "roll_mean_move_8",
    "roll_mean_move_13",
    "roll_std_move_4",
    "lag_price_1",
    "lag_price_2",
    "roll_mean_price_4",
    "series_reference_price",
    "decision_time_unit_cost",
    "lag_promotion_1",
    "series_age_weeks",
    "package_size_oz",
)

FEATURE_COLUMNS: tuple[str, ...] = PRICE_DEPENDENT_FEATURES + CONTEXT_FEATURES

CATEGORICAL_FEATURES: tuple[str, ...] = ("store", "upc", "com_code")

TARGET = "move"


class FeatureError(ValueError):
    """Raised when a feature cannot be built from the available context."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def parse_package_size(size: pd.Series) -> pd.Series:
    """Extract a numeric ounce size from strings such as '15.5 OZ'."""
    s = size.astype("string").str.upper().str.strip()
    number = s.str.extract(r"([0-9]+(?:\.[0-9]+)?)", expand=False).astype("float64")
    is_lb = s.str.contains("LB", na=False)
    return np.where(is_lb, number * 16.0, number)


def _safe_ratio(numerator, denominator) -> np.ndarray:
    num = np.asarray(numerator, dtype="float64")
    den = np.asarray(denominator, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, num / den, np.nan)


# ---------------------------------------------------------------------------
# price-dependent block
# ---------------------------------------------------------------------------
def recompute_price_features(
    frame: pd.DataFrame, candidate_price: np.ndarray | pd.Series | float
) -> pd.DataFrame:
    """Recompute every price-dependent feature for a candidate price.

    ``frame`` supplies the *fixed* context columns (``lag_price_1``,
    ``series_reference_price``, ``roll_mean_price_4``). This is the single
    implementation used by training, the simulator, the optimizer and the API,
    so a candidate price can never be scored with stale price features.
    """
    out = frame.copy()
    price = np.asarray(candidate_price, dtype="float64")
    if price.ndim == 0:
        price = np.full(len(out), float(price))
    if len(price) != len(out):
        raise FeatureError(
            f"candidate price array length {len(price)} does not match frame length {len(out)}"
        )
    if np.any(price <= 0):
        raise FeatureError("candidate prices must be strictly positive")

    out["effective_unit_price"] = price
    out["log_price"] = np.log(price)
    out["price_vs_last_week"] = _safe_ratio(price, out["lag_price_1"]) - 1.0
    out["price_vs_series_reference"] = _safe_ratio(price, out["series_reference_price"]) - 1.0
    out["price_vs_recent_mean"] = _safe_ratio(price, out["roll_mean_price_4"]) - 1.0
    return out


# ---------------------------------------------------------------------------
# full feature table
# ---------------------------------------------------------------------------
def build_feature_table(df: pd.DataFrame, cfg: Config | None = None) -> pd.DataFrame:
    """Build the modelling table from the canonical Dominick's table.

    Returns one row per (upc, store, week) with the target, the features and
    the economic context needed downstream (cost, observed outcomes).
    """
    cfg = cfg or load_config()
    lags = list(cfg.get("modeling.lags", [1, 2, 3, 4]))
    windows = list(cfg.get("modeling.rolling_windows", [4, 8, 13]))

    d = df.sort_values(SERIES_KEYS + ["week"]).copy()
    g = d.groupby(SERIES_KEYS, observed=True, sort=False)

    # --- lagged demand (shifted: week t never sees its own units) ----------
    for lag in lags:
        d[f"lag_move_{lag}"] = g[TARGET].shift(lag)

    shifted_move = g[TARGET].shift(1)
    d["_shifted_move"] = shifted_move
    gs = d.groupby(SERIES_KEYS, observed=True, sort=False)["_shifted_move"]
    for w in windows:
        d[f"roll_mean_move_{w}"] = gs.transform(lambda s, w=w: s.rolling(w, min_periods=2).mean())
    d["roll_std_move_4"] = gs.transform(lambda s: s.rolling(4, min_periods=2).std())

    # --- lagged prices ------------------------------------------------------
    d["lag_price_1"] = g["effective_unit_price"].shift(1)
    d["lag_price_2"] = g["effective_unit_price"].shift(2)
    d["_shifted_price"] = g["effective_unit_price"].shift(1)
    gp = d.groupby(SERIES_KEYS, observed=True, sort=False)["_shifted_price"]
    d["roll_mean_price_4"] = gp.transform(lambda s: s.rolling(4, min_periods=1).mean())
    # Reference price: expanding median of past prices for that series.
    d["series_reference_price"] = gp.transform(lambda s: s.expanding(min_periods=1).median())

    # --- decision-time cost -------------------------------------------------
    # The contemporaneous accounting margin is an OUTCOME. The cost we are
    # allowed to use when setting next week's price is the latest known implied
    # AAC, i.e. lagged by one week and forward-filled within the series.
    d["_lag_aac"] = g["estimated_unit_aac"].shift(1)
    d["decision_time_unit_cost"] = d.groupby(SERIES_KEYS, observed=True, sort=False)[
        "_lag_aac"
    ].ffill()

    # --- promotion ----------------------------------------------------------
    d["lag_promotion_1"] = g["recorded_promotion_flag"].shift(1)

    # --- calendar / series age ---------------------------------------------
    d["time_index"] = d["week"].astype("int32")
    d["series_age_weeks"] = g.cumcount()

    # --- product metadata ---------------------------------------------------
    if "size" in d.columns:
        d["package_size_oz"] = parse_package_size(d["size"])
    else:
        d["package_size_oz"] = np.nan

    # --- price-dependent block ---------------------------------------------
    d = recompute_price_features(d, d["effective_unit_price"].to_numpy(dtype="float64"))

    d = d.drop(columns=["_shifted_move", "_shifted_price", "_lag_aac"])
    return d


def training_frame(
    features: pd.DataFrame, *, dropna_target: bool = True, require_lags: bool = True
) -> pd.DataFrame:
    """Rows usable for supervised training (enough history, valid cost)."""
    d = features
    if dropna_target:
        d = d[d[TARGET].notna()]
    if require_lags:
        d = d[d["lag_move_1"].notna() & d["lag_price_1"].notna()]
    return d.copy()


def temporal_split(
    features: pd.DataFrame, cfg: Config | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Chronological train / validation / test split by Dominick's week index.

    Random splitting is never used: retail panels are time-indexed, and a
    random split would let the model see the future of the same series.
    """
    cfg = cfg or load_config()
    train_frac = float(cfg.get("modeling.train_frac", 0.7))
    valid_frac = float(cfg.get("modeling.valid_frac", 0.15))

    weeks = np.sort(features["week"].unique())
    n = len(weeks)
    i_train = int(np.floor(n * train_frac))
    i_valid = int(np.floor(n * (train_frac + valid_frac)))
    train_weeks = set(weeks[:i_train].tolist())
    valid_weeks = set(weeks[i_train:i_valid].tolist())
    test_weeks = set(weeks[i_valid:].tolist())

    train = features[features["week"].isin(train_weeks)]
    valid = features[features["week"].isin(valid_weeks)]
    test = features[features["week"].isin(test_weeks)]

    info = {
        "n_weeks": int(n),
        "train_weeks": [int(weeks[0]), int(weeks[i_train - 1])],
        "valid_weeks": [int(weeks[i_train]), int(weeks[i_valid - 1])],
        "test_weeks": [int(weeks[i_valid]), int(weeks[-1])],
        "train_dates": [
            str(train["week_start_date"].min().date()),
            str(train["week_start_date"].max().date()),
        ],
        "valid_dates": [
            str(valid["week_start_date"].min().date()),
            str(valid["week_start_date"].max().date()),
        ],
        "test_dates": [
            str(test["week_start_date"].min().date()),
            str(test["week_start_date"].max().date()),
        ],
        "n_train": int(len(train)),
        "n_valid": int(len(valid)),
        "n_test": int(len(test)),
    }
    return train, valid, test, info
