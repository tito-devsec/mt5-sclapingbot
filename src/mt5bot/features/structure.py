"""Causal price-structure features: candlestick patterns, confirmed swing levels, structure trend and trendline."""

from __future__ import annotations

import numpy as np
import pandas as pd

SWING_K = 3
TRENDLINE_N = 30


def candlesticks(o: pd.Series, h: pd.Series, lo: pd.Series, c: pd.Series) -> pd.DataFrame:
    """Single and two-bar patterns as 0/1 flags, using only the current and previous bar."""
    rng = (h - lo).where((h - lo) > 0)
    body = (c - o).abs()
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - lo
    po, pc, ph, pl = o.shift(1), c.shift(1), h.shift(1), lo.shift(1)
    out = pd.DataFrame(index=o.index)
    out["cs_bull_engulf"] = ((pc < po) & (c > o) & (c >= po) & (o <= pc)).astype(float).where(po.notna())
    out["cs_bear_engulf"] = ((pc > po) & (c < o) & (c <= po) & (o >= pc)).astype(float).where(po.notna())
    out["cs_pin_bull"] = ((lower >= 2 * body) & (lower >= 0.6 * rng) & (upper <= 0.25 * rng)).astype(float)
    out["cs_pin_bear"] = ((upper >= 2 * body) & (upper >= 0.6 * rng) & (lower <= 0.25 * rng)).astype(float)
    out["cs_doji"] = (body <= 0.1 * rng).astype(float)
    out["cs_inside"] = ((h <= ph) & (lo >= pl)).astype(float).where(ph.notna())
    return out


def _confirmed_pivots(x: pd.Series, k: int, high: bool) -> tuple[pd.Series, pd.Series]:
    """Last and previous confirmed pivot values; a pivot at bar j is only known k bars later."""
    win = 2 * k + 1
    extreme = x.rolling(win, min_periods=win).max() if high else x.rolling(win, min_periods=win).min()
    centre = x.shift(k)
    is_pivot = centre.notna() & (centre == extreme)
    values = centre.where(is_pivot)
    pivots = values.dropna()
    last = values.ffill()
    prev = pivots.shift(1).reindex(x.index).ffill()
    return last, prev


def swing_structure(h: pd.Series, lo: pd.Series, c: pd.Series, atr: pd.Series, k: int = SWING_K) -> pd.DataFrame:
    """Distance to the last confirmed swing levels and a higher-high/higher-low structure trend."""
    last_hi, prev_hi = _confirmed_pivots(h, k, True)
    last_lo, prev_lo = _confirmed_pivots(lo, k, False)
    out = pd.DataFrame(index=h.index)
    out["sr_res_dist_atr"] = (last_hi - c) / atr
    out["sr_sup_dist_atr"] = (c - last_lo) / atr
    up = (last_hi > prev_hi) & (last_lo > prev_lo)
    down = (last_hi < prev_hi) & (last_lo < prev_lo)
    known = prev_hi.notna() & prev_lo.notna()
    out["struct_trend"] = (up.astype(float) - down.astype(float)).where(known)
    return out


def trendline(c: pd.Series, atr: pd.Series, n: int = TRENDLINE_N) -> pd.DataFrame:
    """Rolling least-squares line over the last n closes: slope per bar and deviation of the close, in ATRs."""
    t = np.arange(n, dtype=float)
    t_mean, t_var = t.mean(), ((t - t.mean()) ** 2).sum()
    weights = (t - t_mean) / t_var
    slope = c.rolling(n, min_periods=n).apply(lambda w: float(np.dot(weights, w)), raw=True)
    mean = c.rolling(n, min_periods=n).mean()
    fitted_last = mean + slope * (n - 1 - t_mean)
    return pd.DataFrame({"tl_slope_atr": slope / atr, "tl_dev_atr": (c - fitted_last) / atr})
