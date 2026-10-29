"""Optuna hyper-parameter search on validation folds only; every trial counts toward n_trials."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mt5bot.evaluation.splits import train_val_test
from mt5bot.ml.dataset import MetaDataset
from mt5bot.ml.training import evaluate_on, fit_meta_model

SEARCH_SPACES = {
    "lightgbm": lambda t: {
        "num_leaves": t.suggest_int("num_leaves", 7, 63),
        "learning_rate": t.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "min_child_samples": t.suggest_int("min_child_samples", 10, 100),
    },
    "xgboost": lambda t: {
        "max_depth": t.suggest_int("max_depth", 2, 6),
        "learning_rate": t.suggest_float("learning_rate", 0.01, 0.1, log=True),
    },
    "catboost": lambda t: {
        "depth": t.suggest_int("depth", 3, 6),
        "learning_rate": t.suggest_float("learning_rate", 0.01, 0.1, log=True),
    },
    "random_forest": lambda t: {
        "max_depth": t.suggest_int("max_depth", 3, 10),
        "min_samples_leaf": t.suggest_int("min_samples_leaf", 5, 50),
    },
    "logreg": lambda t: {"C": t.suggest_float("C", 0.01, 10, log=True)},
}


def tune(
    ds: MetaDataset,
    *,
    model_type: str,
    n_trials: int,
    seed: int,
    threshold: float,
    confidence: float,
    val_fraction: float,
    test_fraction: float,
    embargo: pd.Timedelta,
) -> tuple[dict[str, Any], int]:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    val_split, _ = train_val_test(ds.meta, val_fraction, test_fraction, embargo)
    train, val = ds.subset(val_split.train_idx), ds.subset(val_split.eval_idx)

    def objective(trial: Any) -> float:
        params = SEARCH_SPACES[model_type](trial)
        try:
            m = fit_meta_model(
                train,
                model_type=model_type,
                seed=seed,
                threshold=threshold,
                confidence=confidence,
                embargo=embargo,
                params=params,
            )
        except ValueError:
            return -np.inf
        sel = evaluate_on(m, val, cost_multiplier=1.0, n_trials=1)["selected"]
        # Penalise tiny selections so the search does not reward luck on a handful of trades.
        return float(sel["expectancy_r"] * min(1.0, sel["trades"] / 30))

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials)
    return dict(study.best_params), len(study.trials)
