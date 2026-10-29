"""Meta-label training: fit on train, calibrate on validation, evaluate once on untouched test."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from mt5bot.backtest.metrics import max_drawdown
from mt5bot.evaluation.splits import _purge, train_val_test, walk_forward
from mt5bot.evaluation.stats import block_bootstrap_ci, deflated_sharpe, ljung_box_pvalue, probabilistic_sharpe
from mt5bot.ml.dataset import MetaDataset
from mt5bot.ml.models import make_model


class ConformalAbstainer:
    """Split-conformal LAC prediction sets for a binary classifier, matching MAPIE's definition."""

    def __init__(self, confidence: float) -> None:
        self.confidence = confidence
        self.q_: float | None = None

    def fit(self, proba_pos: np.ndarray, y: np.ndarray) -> ConformalAbstainer:
        p_true = np.where(y == 1, proba_pos, 1 - proba_pos)
        scores = 1 - p_true
        n = len(scores)
        level = min(1.0, math.ceil((n + 1) * self.confidence) / n)
        self.q_ = float(np.quantile(scores, level, method="lower"))  # ceil((n+1)c)-th smallest score
        return self

    def sets(self, proba_pos: np.ndarray) -> np.ndarray:
        if self.q_ is None:
            raise RuntimeError("not fitted")
        return np.column_stack([(proba_pos) <= self.q_, (1 - proba_pos) <= self.q_])  # [has0, has1]

    def confident_positive(self, proba_pos: np.ndarray) -> np.ndarray:
        s = self.sets(proba_pos)
        return s[:, 1] & ~s[:, 0]


@dataclass
class FittedMetaModel:
    model_type: str
    estimator: Any  # calibrated classifier
    conformal: ConformalAbstainer
    feature_names: list[str]
    threshold: float
    params: dict[str, Any]
    seed: int

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict_proba(X[self.feature_names])[:, 1]


def _time_split(ds: MetaDataset, frac: float, embargo: pd.Timedelta) -> tuple[np.ndarray, np.ndarray]:
    t = ds.meta["available_at"]
    cut = t.iloc[int(len(t) * (1 - frac))]
    fit_mask = (t < cut).to_numpy()
    cal = np.flatnonzero((t >= cut).to_numpy())
    fit = np.flatnonzero(_purge(ds.meta, fit_mask, cut, embargo))
    return fit, cal


def fit_meta_model(
    train: MetaDataset,
    *,
    model_type: str,
    seed: int,
    threshold: float,
    confidence: float,
    embargo: pd.Timedelta,
    calib_fraction: float = 0.25,
    params: dict[str, Any] | None = None,
    rebalance: str = "none",
) -> FittedMetaModel:
    fit_idx, cal_idx = _time_split(train, calib_fraction, embargo)
    if len(fit_idx) < 20 or len(cal_idx) < 10:
        raise ValueError("not enough data to fit and calibrate")
    yf = train.y[fit_idx]
    if len(np.unique(yf)) < 2:
        raise ValueError("training labels contain a single class")
    base = make_model(model_type, seed, params)
    Xf = train.X.iloc[fit_idx]
    if rebalance == "undersample":
        # Optional (uncertainty extra). Applied to the fitting rows only - never to calibration or test rows.
        from imblearn.under_sampling import RandomUnderSampler

        Xf, yf = RandomUnderSampler(random_state=seed).fit_resample(Xf, yf)
    base.fit(Xf, yf)
    cal = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid")
    cal.fit(train.X.iloc[cal_idx], train.y[cal_idx])
    p_cal = cal.predict_proba(train.X.iloc[cal_idx])[:, 1]
    conf = ConformalAbstainer(confidence).fit(p_cal, train.y[cal_idx])
    return FittedMetaModel(model_type, cal, conf, list(train.feature_names), threshold, dict(params or {}), seed)


def decision_mask(model: FittedMetaModel, X: pd.DataFrame, use_conformal: bool = True) -> tuple[np.ndarray, np.ndarray]:
    p = model.predict_proba(X)
    take = p >= model.threshold
    if use_conformal:
        take &= model.conformal.confident_positive(p)
    return take, p


def selection_metrics(
    net_r: np.ndarray, cost_r: np.ndarray | None = None, cost_multiplier: float = 1.0, n_trials: int = 1
) -> dict[str, Any]:
    """Metrics of a set of selected candidates (ordered in time)."""
    r = np.asarray(net_r, float)
    if cost_r is not None and cost_multiplier != 1.0:
        r = r - (cost_multiplier - 1.0) * np.asarray(cost_r, float)  # linear cost scaling
    n = len(r)
    if n == 0:
        return {"trades": 0, "expectancy_r": 0.0, "net_r": 0.0, "max_drawdown_r": 0.0}
    lo, hi = block_bootstrap_ci(r)
    return {
        "trades": n,
        "net_r": float(r.sum()),
        "expectancy_r": float(r.mean()),
        "expectancy_ci95": [lo, hi],
        "win_rate": float((r > 0).mean()),
        "max_drawdown_r": max_drawdown(np.cumsum(r)),
        "psr": probabilistic_sharpe(r),
        "deflated_sharpe_prob": deflated_sharpe(r, n_trials),
        "ljung_box_pvalue": ljung_box_pvalue(r),
    }


def classification_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    out = {
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])),
    }
    out["auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else math.nan
    out["base_rate"] = float(np.mean(y))
    return out


@dataclass
class EvaluationReport:
    model: FittedMetaModel
    test: dict[str, Any]
    validation: dict[str, Any]
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    baselines: dict[str, Any] = field(default_factory=dict)
    train_start: pd.Timestamp | None = None
    train_end: pd.Timestamp | None = None
    test_start: pd.Timestamp | None = None
    test_end: pd.Timestamp | None = None

    def summary(self) -> dict[str, Any]:
        wf = [f["selected"]["expectancy_r"] for f in self.walk_forward if f["selected"]["trades"] > 0]
        return {
            "test": self.test,
            "validation": self.validation,
            "walk_forward": self.walk_forward,
            "walk_forward_mean_expectancy_r": float(np.mean(wf)) if wf else 0.0,
            "walk_forward_positive_share": float(np.mean([x > 0 for x in wf])) if wf else 0.0,
            "baselines": self.baselines,
        }


def evaluate_on(model: FittedMetaModel, ds: MetaDataset, *, cost_multiplier: float, n_trials: int) -> dict[str, Any]:
    take, p = decision_mask(model, ds.X)
    r, c = ds.meta["net_r"].to_numpy(float), ds.meta["cost_r"].to_numpy(float)
    eligible = ds.meta["eligible"].to_numpy(bool) if "eligible" in ds.meta else np.ones(len(r), bool)
    take &= eligible  # the model can only choose among rule-eligible candidates
    return {
        "classification": classification_metrics(ds.y, p),
        "selected": selection_metrics(r[take], c[take], 1.0, n_trials),
        "selected_stressed": selection_metrics(r[take], c[take], cost_multiplier, n_trials),
        "coverage": float(take.mean()) if len(take) else 0.0,
    }


def baselines_on(ds: MetaDataset, cost_multiplier: float) -> dict[str, Any]:
    r, c = ds.meta["net_r"].to_numpy(float), ds.meta["cost_r"].to_numpy(float)
    eligible = ds.meta["eligible"].to_numpy(bool) if "eligible" in ds.meta else np.ones(len(r), bool)
    out: dict[str, Any] = {
        "no_trade": {"trades": 0, "expectancy_r": 0.0, "net_r": 0.0},
        "rules_all_eligible": selection_metrics(r[eligible], c[eligible]),
    }
    out["rules_all_eligible_stressed"] = selection_metrics(r[eligible], c[eligible], cost_multiplier)
    for s in sorted(ds.meta["strategy"].unique()):
        m = eligible & (ds.meta["strategy"] == s).to_numpy()
        out[f"rule_{s}"] = selection_metrics(r[m], c[m])
    return out


def train_and_evaluate(
    ds: MetaDataset,
    *,
    model_type: str,
    seed: int,
    threshold: float,
    confidence: float,
    val_fraction: float,
    test_fraction: float,
    n_folds: int,
    embargo: pd.Timedelta,
    cost_multiplier: float,
    n_trials: int = 1,
    params: dict[str, Any] | None = None,
) -> EvaluationReport:
    val_split, test_split = train_val_test(ds.meta, val_fraction, test_fraction, embargo)
    # Final model: trained on everything before the test window (purged), evaluated once on test.
    pre_test = ds.subset(test_split.train_idx)
    test = ds.subset(test_split.eval_idx)
    model = fit_meta_model(
        pre_test,
        model_type=model_type,
        seed=seed,
        threshold=threshold,
        confidence=confidence,
        embargo=embargo,
        params=params,
    )
    # Validation report uses a model that never saw validation rows.
    val_model = fit_meta_model(
        ds.subset(val_split.train_idx),
        model_type=model_type,
        seed=seed,
        threshold=threshold,
        confidence=confidence,
        embargo=embargo,
        params=params,
    )
    validation = evaluate_on(
        val_model, ds.subset(val_split.eval_idx), cost_multiplier=cost_multiplier, n_trials=n_trials
    )
    test_metrics = evaluate_on(model, test, cost_multiplier=cost_multiplier, n_trials=n_trials)

    wf = []
    for fold in walk_forward(pre_test.meta, n_folds, embargo):
        tr, ev = pre_test.subset(fold.train_idx), pre_test.subset(fold.eval_idx)
        try:
            m = fit_meta_model(
                tr,
                model_type=model_type,
                seed=seed,
                threshold=threshold,
                confidence=confidence,
                embargo=embargo,
                params=params,
            )
        except ValueError as exc:
            wf.append({"fold": fold.name, "skipped": str(exc), "selected": {"trades": 0, "expectancy_r": 0.0}})
            continue
        res = evaluate_on(m, ev, cost_multiplier=cost_multiplier, n_trials=n_trials)
        wf.append({"fold": fold.name, "start": str(fold.eval_start), "end": str(fold.eval_end), **res})

    t = pre_test.meta["available_at"]
    return EvaluationReport(
        model=model,
        test=test_metrics,
        validation=validation,
        walk_forward=wf,
        baselines={"test": baselines_on(test, cost_multiplier)},
        train_start=t.iloc[0] if len(t) else None,
        train_end=t.iloc[-1] if len(t) else None,
        test_start=test.meta["available_at"].iloc[0] if len(test) else None,
        test_end=test.meta["available_at"].iloc[-1] if len(test) else None,
    )
