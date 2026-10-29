from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from mt5bot.core.types import Regime, Timeframe
from mt5bot.features.pipeline import FeatureConfig, build_features
from mt5bot.regime.rules import classify_rules, frequencies, out_of_distribution, stability
from mt5bot.strategies.specialists import Breakout, MeanReversion, TrendFollowing, build_strategies
from mt5bot.testing.synthetic import resample_bars, synthetic_bars

CFG = FeatureConfig(vol_percentile_window=100)


@pytest.fixture(scope="module")
def feats():
    b = synthetic_bars(datetime(2024, 3, 4, tzinfo=UTC), 4000, seed=5)
    return build_features(b, Timeframe.M1, 1e-5, resample_bars(b, Timeframe.M5), CFG)


def test_rules_return_uncertain_on_missing(feats):
    r = classify_rules(feats)
    assert (r["regime"].iloc[:100] == Regime.UNCERTAIN.value).all()
    assert (r.loc[r["regime"] == Regime.UNCERTAIN.value, "regime_confidence"] <= 0.5).all()
    assert set(r["regime"]) <= {x.value for x in Regime}
    assert r["regime_confidence"].between(0, 1).all()


def test_rules_known_cases():
    base = {
        "adx_14": 35,
        "ema_20_50_atr": 1.2,
        "ema20_slope_atr": 0.4,
        "vol_pctile": 0.5,
        "range_expansion": 1.0,
        "breakout_up_20": -0.5,
        "breakout_dn_20": -0.5,
    }
    rows = [
        base,
        {**base, "ema_20_50_atr": -1.2, "ema20_slope_atr": -0.4},
        {**base, "adx_14": 12, "ema_20_50_atr": 0.1},
        {**base, "breakout_up_20": 0.3, "range_expansion": 2.0},
        {**base, "adx_14": 20, "ema_20_50_atr": 0.1, "vol_pctile": 0.97},
        {**base, "adx_14": np.nan},
    ]
    got = classify_rules(pd.DataFrame(rows))["regime"].tolist()
    assert got == ["TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT_EXPANSION", "HIGH_VOL", "UNCERTAIN"]


def test_stability_and_frequencies():
    s = pd.Series(["A"] * 10 + ["B"] * 2 + ["A"] * 10)
    st = stability(s, min_run=5)
    assert st["transitions"] == 2 and st["false_transition_rate"] == pytest.approx(1 / 3)
    assert frequencies(s)["A"] == pytest.approx(20 / 22)


def test_ood_flags_extreme_rows(feats):
    cols = ["adx_14", "rv_30"]
    ref = feats.dropna(subset=cols)
    test = ref.iloc[:5].copy()
    test.loc[test.index[0], "rv_30"] = 1.0  # absurd volatility
    flags = out_of_distribution(test, ref, cols)
    assert flags.iloc[0] and not flags.iloc[1:].any()


def test_strategies_only_eligible_in_their_regimes(feats):
    regimes = classify_rules(feats)["regime"]
    for strat in (TrendFollowing(), Breakout(), MeanReversion()):
        c = strat.candidates(feats, regimes, Timeframe.M1)
        if c.empty:
            continue
        allowed = {r.value for r in strat.spec.required_regimes}
        assert c.loc[c["eligible"], "regime"].isin(allowed).all()
        assert (c.loc[~c["regime"].isin(allowed), "eligibility_reason"] == "REGIME_NOT_ELIGIBLE").all()
        assert (c["stop_distance"] > 0).all() and (c["target_distance"] > 0).all()
        assert (c["available_at"] == c["time_utc"] + pd.Timedelta(minutes=1)).all()
    assert TrendFollowing().spec.reward_risk >= 1.0
    assert all(
        s.spec.required_regimes != frozenset(Regime)
        for s in build_strategies(["trend", "breakout", "mean_reversion"], 60)
    )


def test_max_hold_bars_by_timeframe():
    s = TrendFollowing(60).spec
    assert s.max_hold_bars(Timeframe.M1) == 60 and s.max_hold_bars(Timeframe.M5) == 12
    assert s.max_hold_bars(Timeframe.H1) == 1


def test_hmm_regime_model_optional(feats):
    pytest.importorskip("hmmlearn")
    from mt5bot.regime.hmm import HMMRegimeModel

    train = feats.iloc[:3000]
    m = HMMRegimeModel(n_states=3, seed=1).fit(train)
    p1 = m.predict(feats.iloc[3000:3500])
    p2 = HMMRegimeModel(n_states=3, seed=1).fit(train).predict(feats.iloc[3000:3500])
    pd.testing.assert_frame_equal(p1, p2)  # reproducible with a fixed seed
    # filtering only: adding future rows does not change earlier predictions
    p3 = m.predict(feats.iloc[3000:3800]).iloc[:500]
    pd.testing.assert_frame_equal(p1, p3)
