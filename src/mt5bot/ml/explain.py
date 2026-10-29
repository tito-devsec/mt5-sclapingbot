"""Feature attributions with SHAP (optional ``explain`` extra)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mt5bot.ml.training import FittedMetaModel


def _base_estimator(model: FittedMetaModel) -> Any:
    cal = model.estimator.calibrated_classifiers_[0]
    est = cal.estimator
    return getattr(est, "estimator", est)  # unwrap FrozenEstimator


def global_importance(model: FittedMetaModel, X: pd.DataFrame, max_rows: int = 2000) -> dict[str, float]:
    """Mean |SHAP| per feature for tree models; empty dict if SHAP or the model type is unsupported."""
    try:
        import shap
    except ImportError:
        return {}
    if model.model_type not in ("lightgbm", "xgboost", "catboost", "random_forest"):
        return {}
    est = _base_estimator(model)
    sample = X[model.feature_names].iloc[-max_rows:]
    values = shap.TreeExplainer(est).shap_values(sample)
    if isinstance(values, list):
        values = values[1]
    values = np.asarray(values)
    if values.ndim == 3:
        values = values[:, :, 1]
    imp = np.abs(values).mean(axis=0)
    order = np.argsort(imp)[::-1]
    return {model.feature_names[i]: float(imp[i]) for i in order[:25]}


def explain_row(model: FittedMetaModel, x: pd.DataFrame, top: int = 5) -> list[tuple[str, float]]:
    try:
        import shap
    except ImportError:
        return []
    if model.model_type not in ("lightgbm", "xgboost", "catboost", "random_forest"):
        return []
    values = shap.TreeExplainer(_base_estimator(model)).shap_values(x[model.feature_names])
    if isinstance(values, list):
        values = values[1]
    values = np.asarray(values)
    if values.ndim == 3:
        values = values[:, :, 1]
    row = values[0]
    order = np.argsort(np.abs(row))[::-1][:top]
    return [(model.feature_names[i], float(row[i])) for i in order]
