"""Rule-based regime baseline; missing or out-of-range inputs give UNCERTAIN."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from mt5bot.core.types import Regime

REGIME_RULES_VERSION = "rules-1.0.0"
REQUIRED = [
    "adx_14",
    "ema_20_50_atr",
    "ema20_slope_atr",
    "vol_pctile",
    "range_expansion",
    "breakout_up_20",
    "breakout_dn_20",
]


@dataclass(frozen=True)
class RegimeThresholds:
    adx_trend: float = 25.0
    adx_range: float = 18.0
    ema_trend_atr: float = 0.5
    vol_high: float = 0.90
    vol_low: float = 0.10
    expansion: float = 1.6
    breakout_atr: float = 0.0


def classify_rules(features: pd.DataFrame, th: RegimeThresholds | None = None) -> pd.DataFrame:
    """Return columns ``regime`` and ``regime_confidence`` (0..1) per row."""
    th = th or RegimeThresholds()
    f = features
    missing = f[REQUIRED].isna().any(axis=1)
    adx, spread, slope = f["adx_14"], f["ema_20_50_atr"], f["ema20_slope_atr"]
    vol, exp_ = f["vol_pctile"], f["range_expansion"]
    broke = (f["breakout_up_20"] > th.breakout_atr) | (f["breakout_dn_20"] > th.breakout_atr)

    trend_up = (adx >= th.adx_trend) & (spread >= th.ema_trend_atr) & (slope > 0)
    trend_dn = (adx >= th.adx_trend) & (spread <= -th.ema_trend_atr) & (slope < 0)
    breakout = broke & (exp_ >= th.expansion)
    high_vol = vol >= th.vol_high
    low_vol = vol <= th.vol_low
    ranging = (adx <= th.adx_range) & (spread.abs() < th.ema_trend_atr)

    regime = np.full(len(f), Regime.UNCERTAIN.value, dtype=object)
    conf = np.zeros(len(f))
    # Priority: breakout > trend > extreme vol > range. Conflicts -> UNCERTAIN.
    order = [
        (breakout, Regime.BREAKOUT_EXPANSION, (exp_ - th.expansion) / th.expansion),
        (trend_up & ~trend_dn, Regime.TREND_UP, (adx - th.adx_trend) / th.adx_trend),
        (trend_dn & ~trend_up, Regime.TREND_DOWN, (adx - th.adx_trend) / th.adx_trend),
        (high_vol, Regime.HIGH_VOL, (vol - th.vol_high) / (1 - th.vol_high + 1e-9)),
        (low_vol, Regime.LOW_VOL, (th.vol_low - vol) / (th.vol_low + 1e-9)),
        (ranging, Regime.RANGE, (th.adx_range - adx) / th.adx_range),
    ]
    assigned = np.zeros(len(f), dtype=bool)
    for mask, label, strength in order:
        m = mask.fillna(False).to_numpy() & ~assigned
        regime[m] = label.value
        conf[m] = np.clip(0.5 + 0.5 * strength.fillna(0).to_numpy()[m], 0.5, 1.0)
        assigned |= m
    m = missing.to_numpy()
    regime[m] = Regime.UNCERTAIN.value
    conf[m] = 0.0
    return pd.DataFrame({"regime": regime, "regime_confidence": conf}, index=f.index)


def out_of_distribution(
    features: pd.DataFrame, reference: pd.DataFrame, cols: list[str], threshold: float = 6.0
) -> pd.Series:
    """Mahalanobis-distance OOD flag against a reference (training) sample."""
    ref = reference[cols].dropna()
    if len(ref) < len(cols) * 5:
        return pd.Series(False, index=features.index)
    mu = ref.mean().to_numpy()
    cov = np.cov(ref.to_numpy(), rowvar=False) + np.eye(len(cols)) * 1e-9
    inv = np.linalg.pinv(cov)
    x = features[cols].to_numpy() - mu
    d = np.sqrt(np.einsum("ij,jk,ik->i", x, inv, x))
    return pd.Series(np.nan_to_num(d, nan=np.inf) > threshold, index=features.index)


def stability(regimes: pd.Series, min_run: int = 5) -> dict[str, float]:
    """Persistence metrics: mean run length and share of runs shorter than ``min_run`` (false transitions)."""
    r = regimes.reset_index(drop=True)
    if r.empty:
        return {"mean_run": 0.0, "false_transition_rate": 0.0, "transitions": 0}
    runs = (r != r.shift()).cumsum()
    lengths = r.groupby(runs).size()
    return {
        "mean_run": float(lengths.mean()),
        "false_transition_rate": float((lengths < min_run).mean()),
        "transitions": int(len(lengths) - 1),
    }


def frequencies(regimes: pd.Series) -> dict[str, float]:
    return {k: float(v) for k, v in regimes.value_counts(normalize=True).items()}
