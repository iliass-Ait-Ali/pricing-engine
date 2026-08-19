"""The demand model: a thin, serialisable wrapper with a stable contract.

Two model families are supported behind one interface, so the simulator, the
optimizer, the API and the dashboard never care which one was selected:

* ``ridge_loglog`` - interpretable regularised regression on log demand. Its
  coefficient on ``log_price`` is directly readable as an elasticity.
* ``hgb_poisson``  - HistGradientBoostingRegressor with a Poisson loss, which
  is the right likelihood for weekly unit counts and guarantees non-negative
  predictions.

Contract:

    model.predict(frame) -> non-negative predicted units, one per row

``frame`` must contain the feature columns produced by
``pricing_engine.features.build``. The model owns its own preprocessing, so a
candidate price simulated at inference time flows through exactly the same
transformations as training data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from pricing_engine.config import Config, load_config
from pricing_engine.features.build import TARGET

NUMERIC_FEATURES: tuple[str, ...] = (
    "log_price",
    "price_vs_last_week",
    "price_vs_series_reference",
    "price_vs_recent_mean",
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
    "recorded_promotion_flag",
    "time_index",
    "series_age_weeks",
    "package_size_oz",
    "upc_demand_prior",
    "sin52",
    "cos52",
)

CATEGORICAL_FEATURES: tuple[str, ...] = ("store", "com_code")

#: Smoothing weight for the UPC demand prior (higher = closer to global mean).
UPC_PRIOR_SMOOTHING = 50.0

#: The linear model must not see an unbounded time trend: standardising
#: ``time_index`` and then extrapolating it into the future window pushes the
#: log-demand prediction outside any observed range once it is exponentiated.
#: Trees saturate instead, so the boosting model keeps it.
LINEAR_EXCLUDED_FEATURES: tuple[str, ...] = ("time_index",)

#: Demand-level features are heavy tailed (weekly units run from 1 to >20,000).
#: The linear model works on their logs, which is what a multiplicative demand
#: specification implies anyway; the tree model uses the raw values.
LOG1P_FEATURES: tuple[str, ...] = (
    "lag_move_1",
    "lag_move_2",
    "lag_move_3",
    "lag_move_4",
    "roll_mean_move_4",
    "roll_mean_move_8",
    "roll_mean_move_13",
    "roll_std_move_4",
)

#: Safety bound for the log-target model: exponentiating a linear prediction can
#: explode on extreme inputs, so predictions are capped at a multiple of the
#: largest weekly demand ever observed in training. The cap is recorded and
#: reported rather than hidden.
PREDICTION_CAP_MULTIPLE = 5.0


class ModelError(RuntimeError):
    """Raised when a demand model is used outside its contract."""


@dataclass
class ModelMetadata:
    """Everything needed to reproduce and audit a trained model."""

    name: str
    kind: str
    version: str
    trained_at_utc: str
    seed: int
    feature_columns: list[str]
    categorical_features: list[str]
    target: str
    params: dict[str, Any]
    train_weeks: list[int] = field(default_factory=list)
    valid_weeks: list[int] = field(default_factory=list)
    test_weeks: list[int] = field(default_factory=list)
    train_dates: list[str] = field(default_factory=list)
    valid_dates: list[str] = field(default_factory=list)
    test_dates: list[str] = field(default_factory=list)
    n_train_rows: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    data_fingerprint: str | None = None
    environment: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def add_derived_model_inputs(df: pd.DataFrame) -> pd.DataFrame:
    """Add the cheap trigonometric seasonality terms used by both models."""
    out = df
    if "sin52" not in out.columns:
        out = out.copy()
        woy = out["week_of_year"].astype("float64")
        out["sin52"] = np.sin(2 * np.pi * woy / 52.0)
        out["cos52"] = np.cos(2 * np.pi * woy / 52.0)
    return out


class DemandModel:
    """Fit / predict / persist a weekly unit-demand model."""

    def __init__(
        self,
        kind: str = "hgb_poisson",
        *,
        cfg: Config | None = None,
        name: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.kind = kind
        self.name = name or kind
        self.params = params or {}
        self.seed = self.cfg.seed
        self.numeric_features: tuple[str, ...] = (
            tuple(c for c in NUMERIC_FEATURES if c not in LINEAR_EXCLUDED_FEATURES)
            if kind == "ridge_loglog"
            else NUMERIC_FEATURES
        )
        self.estimator = self._build_estimator()
        self.upc_prior_: pd.Series | None = None
        self.global_prior_: float = 0.0
        self.prediction_cap_: float | None = None
        self.metadata: ModelMetadata | None = None
        self.price_aware = True

    # -- construction --------------------------------------------------------
    def _build_estimator(self):
        if self.kind == "hgb_poisson":
            hp = dict(self.cfg.get("modeling.hgb", {}))
            hp.update(self.params)
            return HistGradientBoostingRegressor(
                loss="poisson",
                learning_rate=float(hp.get("learning_rate", 0.06)),
                max_iter=int(hp.get("max_iter", 400)),
                max_leaf_nodes=int(hp.get("max_leaf_nodes", 63)),
                min_samples_leaf=int(hp.get("min_samples_leaf", 40)),
                l2_regularization=float(hp.get("l2_regularization", 1.0)),
                early_stopping=bool(hp.get("early_stopping", False)),
                categorical_features="from_dtype",
                random_state=self.seed,
            )
        if self.kind == "ridge_loglog":
            alpha = float(self.params.get("alpha", 1.0))
            numeric = Pipeline(
                [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
            )
            categorical = OneHotEncoder(handle_unknown="ignore", min_frequency=50)
            pre = ColumnTransformer(
                [
                    ("num", numeric, list(self.numeric_features)),
                    ("cat", categorical, list(CATEGORICAL_FEATURES)),
                ]
            )
            return Pipeline([("pre", pre), ("ridge", Ridge(alpha=alpha, random_state=self.seed))])
        raise ModelError(f"Unknown demand-model kind: {self.kind!r}")

    # -- feature preparation -------------------------------------------------
    def _fit_upc_prior(self, df: pd.DataFrame) -> None:
        """Smoothed historical demand level per UPC, learned on training rows only."""
        y = np.log1p(df[TARGET].to_numpy(dtype="float64"))
        tmp = pd.DataFrame({"upc": df["upc"].to_numpy(), "y": y})
        stats = tmp.groupby("upc", observed=True)["y"].agg(["sum", "count"])
        self.global_prior_ = float(y.mean())
        self.upc_prior_ = (stats["sum"] + UPC_PRIOR_SMOOTHING * self.global_prior_) / (
            stats["count"] + UPC_PRIOR_SMOOTHING
        )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return the model matrix for ``df`` (no target required)."""
        if self.upc_prior_ is None:
            raise ModelError("Model has not been fitted: the UPC demand prior is missing.")
        out = add_derived_model_inputs(df)
        cols = list(self.numeric_features) + list(CATEGORICAL_FEATURES)
        missing = [c for c in cols if c not in out.columns and c != "upc_demand_prior"]
        if missing:
            raise ModelError(
                f"Cannot score: the frame is missing feature column(s) {missing}. "
                "Build features with pricing_engine.features.build.build_feature_table."
            )
        X = pd.DataFrame(index=out.index)
        for col in self.numeric_features:
            if col == "upc_demand_prior":
                X[col] = (
                    out["upc"].map(self.upc_prior_).astype("float64").fillna(self.global_prior_)
                )
            else:
                X[col] = out[col].astype("float64")
        for col in CATEGORICAL_FEATURES:
            X[col] = out[col].astype("category")
        if self.kind == "ridge_loglog":
            for col in LOG1P_FEATURES:
                if col in X.columns:
                    X[col] = np.log1p(X[col].clip(lower=0.0))
        return X

    # -- fit / predict -------------------------------------------------------
    def fit(self, train: pd.DataFrame) -> DemandModel:
        if TARGET not in train.columns:
            raise ModelError(f"Training frame must contain the target column {TARGET!r}.")
        self._fit_upc_prior(train)
        X = self.prepare(train)
        y = train[TARGET].to_numpy(dtype="float64")
        self.prediction_cap_ = float(np.nanmax(y) * PREDICTION_CAP_MULTIPLE)
        if self.kind == "ridge_loglog":
            self.estimator.fit(X, np.log1p(y))
        else:
            self.estimator.fit(X, y)
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X = self.prepare(df)
        raw = np.asarray(self.estimator.predict(X), dtype="float64")
        if self.kind == "ridge_loglog":
            raw = np.expm1(raw)
        upper = self.prediction_cap_ if self.prediction_cap_ else None
        return np.clip(raw, 0.0, upper)

    # -- interpretability ----------------------------------------------------
    def raw_price_coefficient(self) -> float | None:
        """Ridge coefficient on ``log_price``, back on the raw scale.

        This is NOT the model's price response: ``log_price`` is collinear with
        the relative-price features and with lagged prices, so the response is
        spread across several coefficients. Use :meth:`implied_elasticity` for
        the number that actually matters.
        """
        if self.kind != "ridge_loglog":
            return None
        ridge: Ridge = self.estimator.named_steps["ridge"]
        scaler: StandardScaler = (
            self.estimator.named_steps["pre"].named_transformers_["num"].named_steps["scale"]
        )
        idx = list(self.numeric_features).index("log_price")
        return float(ridge.coef_[idx] / scaler.scale_[idx])

    def implied_elasticity(
        self, frame: pd.DataFrame, *, rel: float = 0.02, sample: int | None = 50_000
    ) -> dict[str, float]:
        """Measure the model's own price response by central finite difference.

        Every price-dependent feature is recomputed at (1 +/- rel) x price, so
        this is the elasticity the optimizer will actually exploit, whatever the
        model family. Works identically for the linear and the boosted model.
        """
        from pricing_engine.features.build import recompute_price_features

        d = frame
        if sample and len(d) > sample:
            d = d.sample(sample, random_state=self.seed)
        base = d["effective_unit_price"].to_numpy(dtype="float64")
        q_up = self.predict(recompute_price_features(d, base * (1.0 + rel)))
        q_dn = self.predict(recompute_price_features(d, base * (1.0 - rel)))
        eps = 1e-9
        elasticity = (np.log(q_up + eps) - np.log(q_dn + eps)) / (
            np.log(1.0 + rel) - np.log(1.0 - rel)
        )
        finite = elasticity[np.isfinite(elasticity)]
        return {
            "median": float(np.median(finite)),
            "mean": float(np.mean(finite)),
            "p10": float(np.percentile(finite, 10)),
            "p90": float(np.percentile(finite, 90)),
            "share_negative": float((finite < 0).mean()),
            "n": int(finite.size),
            "rel_step": rel,
        }

    # -- persistence ---------------------------------------------------------
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "kind": self.kind,
                "name": self.name,
                "params": self.params,
                "estimator": self.estimator,
                "upc_prior": self.upc_prior_,
                "global_prior": self.global_prior_,
                "prediction_cap": self.prediction_cap_,
                "metadata": self.metadata.as_dict() if self.metadata else None,
            },
            p,
            compress=3,
        )
        return p


def load_model(path: str | Path, cfg: Config | None = None) -> DemandModel:
    """Reload a serialised demand model."""
    payload = joblib.load(Path(path))
    model = DemandModel(payload["kind"], cfg=cfg, name=payload.get("name"), params=payload.get("params"))
    model.estimator = payload["estimator"]
    model.upc_prior_ = payload["upc_prior"]
    model.global_prior_ = payload["global_prior"]
    model.prediction_cap_ = payload.get("prediction_cap")
    meta = payload.get("metadata")
    model.metadata = ModelMetadata(**meta) if meta else None
    return model
