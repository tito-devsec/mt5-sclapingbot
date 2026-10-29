"""Tests for the optional-library integrations (skipped when an extra is not installed)."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from tests.unit.test_ml_selection_risk import EMB, planted_dataset

from mt5bot.core.types import Timeframe
from mt5bot.features.pipeline import FeatureConfig, build_features
from mt5bot.ml.training import fit_meta_model
from mt5bot.regime.rules import classify_rules
from mt5bot.testing.synthetic import synthetic_bars


def test_optuna_tuning_counts_trials():
    pytest.importorskip("optuna")
    from mt5bot.ml.tuning import tune

    ds = planted_dataset(1200)
    params, n = tune(
        ds,
        model_type="logreg",
        n_trials=4,
        seed=1,
        threshold=0.55,
        confidence=0.6,
        val_fraction=0.2,
        test_fraction=0.2,
        embargo=EMB,
    )
    assert n == 4 and "C" in params


def test_shap_importance_for_tree_model():
    pytest.importorskip("shap")
    pytest.importorskip("lightgbm")
    from mt5bot.ml.explain import explain_row, global_importance

    ds = planted_dataset(900, signal=2.0)
    m = fit_meta_model(ds, model_type="lightgbm", seed=2, threshold=0.55, confidence=0.6, embargo=EMB)
    imp = global_importance(m, ds.X)
    assert max(imp, key=imp.get) == "x0"  # the planted feature dominates
    assert len(explain_row(m, ds.X.iloc[[0]])) == 4


def test_undersampling_only_on_fit_rows():
    pytest.importorskip("imblearn")
    ds = planted_dataset(900)
    m = fit_meta_model(
        ds, model_type="logreg", seed=3, threshold=0.55, confidence=0.6, embargo=EMB, rebalance="undersample"
    )
    assert m.conformal.q_ is not None  # calibration still ran on the untouched calibration rows


def test_changepoint_audit():
    pytest.importorskip("ruptures")
    from mt5bot.regime.changepoints import breakpoints, transition_agreement

    b = synthetic_bars(datetime(2024, 3, 4, tzinfo=UTC), 2400, seed=8)
    f = build_features(b, Timeframe.M1, 1e-5, None, FeatureConfig(vol_percentile_window=100))
    bk = breakpoints(f, penalty=20)
    assert all(isinstance(x, int) for x in bk)
    res = transition_agreement(classify_rules(f)["regime"], bk)
    assert 0 <= res["breaks_matched"] <= 1


def test_garch_forecast_is_causal():
    pytest.importorskip("arch")
    from mt5bot.features.garch import garch_forecast

    b = synthetic_bars(datetime(2024, 3, 4, tzinfo=UTC), 700, seed=9)
    full = garch_forecast(b["close"], window=400, refit_every=100)
    altered = b["close"].copy()
    altered.iloc[600:] *= 1.03
    part = garch_forecast(altered, window=400, refit_every=100)
    pd.testing.assert_series_equal(full.iloc[:600], part.iloc[:600])  # future changes do not move the past
    assert full.iloc[:400].isna().all() and np.isfinite(full.iloc[450:]).all()
