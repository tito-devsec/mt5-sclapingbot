from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from mt5bot.core.types import Timeframe
from mt5bot.features import indicators as ind
from mt5bot.features.pipeline import (
    FeatureConfig,
    build_features,
    feature_names,
    feature_set_hash,
    feature_specs,
    features_markdown,
)
from mt5bot.testing.synthetic import resample_bars, synthetic_bars

T0 = datetime(2024, 3, 4, tzinfo=UTC)
CFG = FeatureConfig(vol_percentile_window=100)


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(T0, 2500, seed=3)


# ---------- independent reference implementations (plain loops) ----------
def ref_wilder(values, n):
    out, avg, count = [], None, 0
    for v in values:
        if np.isnan(v):
            out.append(np.nan if avg is None else avg)
            continue
        count += 1
        avg = v if avg is None else avg + (v - avg) / n
        out.append(avg if count >= n else np.nan)
    return np.array(out)


def ref_rsi(close, n=14):
    d = np.diff(close, prepend=np.nan)
    g = ref_wilder(np.where(np.isnan(d), np.nan, np.maximum(d, 0)), n)
    lo = ref_wilder(np.where(np.isnan(d), np.nan, np.maximum(-d, 0)), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + g / lo)
    return np.where(lo == 0, 100.0, r)


def ref_atr(h, lo, c, n=14):
    tr = [np.nan] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]
    return ref_wilder(np.array(tr), n)


def ref_ema(x, n):
    a, out, e = 2 / (n + 1), [], None
    for i, v in enumerate(x):
        e = v if e is None else a * v + (1 - a) * e
        out.append(e if i >= n - 1 else np.nan)
    return np.array(out)


def test_rsi_matches_reference(bars):
    np.testing.assert_allclose(
        ind.rsi(bars["close"]).to_numpy(), ref_rsi(bars["close"].to_numpy()), rtol=1e-9, equal_nan=True
    )


def test_atr_matches_reference(bars):
    got = ind.atr(bars["high"], bars["low"], bars["close"]).to_numpy()
    exp = ref_atr(bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy())
    np.testing.assert_allclose(got, exp, rtol=1e-9, equal_nan=True)


def test_ema_matches_reference(bars):
    np.testing.assert_allclose(
        ind.ema(bars["close"], 20).to_numpy(), ref_ema(bars["close"].to_numpy(), 20), rtol=1e-12, equal_nan=True
    )


def test_donchian_excludes_current_bar():
    h = pd.Series([1, 2, 3, 10.0])
    lo = pd.Series([0.5, 1, 2, 9.0])
    d = ind.donchian_prev(h, lo, 3)
    assert d["upper"].iloc[3] == 3 and np.isnan(d["upper"].iloc[2])


def test_run_length():
    assert ind.run_length(pd.Series([True, True, False, True])).tolist() == [1, 2, 0, 1]


def test_warmup_rows_are_nan_and_match_registry(bars):
    f = build_features(bars, Timeframe.M1, 1e-5, None, CFG)
    for spec in feature_specs(CFG):
        if spec.name.startswith("htf_"):
            continue
        assert f[spec.name].first_valid_index() == spec.warmup_bars, spec.name


def test_no_lookahead_truncation_invariance(bars):
    """Features at t computed on data up to t equal features at t on the full series."""
    m5 = resample_bars(bars, Timeframe.M5)
    full = build_features(bars, Timeframe.M1, 1e-5, m5, CFG)
    for cut in (300, 777, 1500, 2499):
        t_cut = bars["time_utc"].iloc[cut]
        avail = t_cut + Timeframe.M1.delta
        part_bars = bars.iloc[: cut + 1]
        part_m5 = m5[m5["time_utc"] + Timeframe.M5.delta <= avail]  # only closed M5 bars existed then
        part = build_features(part_bars, Timeframe.M1, 1e-5, part_m5, CFG)
        a = part.iloc[-1][feature_names(CFG)].astype(float).to_numpy()
        b = full.iloc[cut][feature_names(CFG)].astype(float).to_numpy()
        np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True)


def test_future_bar_change_does_not_affect_past(bars):
    altered = bars.copy()
    altered.loc[2000:, ["open", "high", "low", "close"]] *= 1.05
    a = build_features(bars, Timeframe.M1, 1e-5, None, CFG).iloc[:2000]
    b = build_features(altered, Timeframe.M1, 1e-5, None, CFG).iloc[:2000]
    pd.testing.assert_frame_equal(a, b)


def test_higher_timeframe_uses_only_closed_bars(bars):
    m5 = resample_bars(bars, Timeframe.M5)
    f = build_features(bars, Timeframe.M1, 1e-5, m5, CFG)
    from mt5bot.features.pipeline import _htf_frame

    hf = _htf_frame(m5, Timeframe.M5).set_index("available_at")
    for i in (400, 401, 402, 403, 404, 1203):
        avail = f["available_at"].iloc[i]
        closed = hf[hf.index <= avail]
        expected = closed["htf_rsi_14"].iloc[-1]
        assert f["htf_rsi_14"].iloc[i] == pytest.approx(expected, nan_ok=True)
        # the next (still forming) M5 bar is never used
        assert (closed.index <= avail).all()


def test_missing_bar_does_not_fabricate_rows(bars):
    gappy = bars.drop(index=range(500, 510)).reset_index(drop=True)
    f = build_features(gappy, Timeframe.M1, 1e-5, None, CFG)
    assert len(f) == len(gappy)
    assert not f["time_utc"].isin(bars["time_utc"].iloc[500:510]).any()


def test_reproducible_and_hash_stable(bars):
    a = build_features(bars, Timeframe.M1, 1e-5, None, CFG)
    b = build_features(bars, Timeframe.M1, 1e-5, None, CFG)
    pd.testing.assert_frame_equal(a, b)
    assert feature_set_hash(CFG) == feature_set_hash(FeatureConfig(vol_percentile_window=100))
    assert feature_set_hash(CFG) != feature_set_hash(FeatureConfig(vol_percentile_window=200))


def test_zero_range_bar_ratios_defined():
    b = synthetic_bars(T0, 60)
    b.loc[30, ["open", "high", "low", "close"]] = b.loc[30, "open"]
    f = build_features(b, Timeframe.M1, 1e-5, None, CFG)
    assert f.loc[30, "body_ratio"] == 0.0


def test_markdown_lists_every_feature():
    md = features_markdown(CFG)
    for n in feature_names(CFG):
        assert f"`{n}`" in md
