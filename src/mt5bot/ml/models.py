"""Model factory. Every model is seeded; optional libraries are imported lazily."""

from __future__ import annotations

import contextlib
from typing import Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_TYPES = ("logreg", "random_forest", "lightgbm", "xgboost", "catboost")


def default_params(model_type: str) -> dict[str, Any]:
    table: dict[str, dict[str, Any]] = {
        "logreg": {"C": 0.5, "max_iter": 2000},
        "random_forest": {"n_estimators": 300, "max_depth": 6, "min_samples_leaf": 20},
        "lightgbm": {
            "n_estimators": 300,
            "learning_rate": 0.03,
            "num_leaves": 15,
            "min_child_samples": 30,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.8,
            "reg_lambda": 1.0,
        },
        "xgboost": {
            "n_estimators": 300,
            "learning_rate": 0.03,
            "max_depth": 4,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 5,
            "reg_lambda": 1.0,
        },
        "catboost": {"iterations": 300, "learning_rate": 0.03, "depth": 4, "l2_leaf_reg": 3.0},
    }
    return table[model_type]


def make_model(model_type: str, seed: int, params: dict[str, Any] | None = None) -> Any:
    p = {**default_params(model_type), **(params or {})}
    if model_type == "logreg":
        return Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(random_state=seed, **p))])
    if model_type == "random_forest":
        return RandomForestClassifier(random_state=seed, n_jobs=1, **p)
    if model_type == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(random_state=seed, n_jobs=1, verbose=-1, deterministic=True, **p)
    if model_type == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(random_state=seed, n_jobs=1, eval_metric="logloss", **p)
    if model_type == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(random_seed=seed, verbose=0, thread_count=1, allow_writing_files=False, **p)
    raise ValueError(f"unknown model type {model_type!r}")


def library_versions() -> dict[str, str]:
    import importlib.metadata as md

    out = {}
    for pkg in ("scikit-learn", "numpy", "pandas", "lightgbm", "xgboost", "catboost", "skops"):
        with contextlib.suppress(md.PackageNotFoundError):
            out[pkg] = md.version(pkg)
    return out
