"""Deterministic synthetic bars for tests only; they are not market data or evidence of performance."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from mt5bot.core.types import Timeframe


def synthetic_bars(
    start: datetime,
    n: int,
    timeframe: Timeframe = Timeframe.M1,
    *,
    seed: int = 7,
    start_price: float = 1.1000,
    point: float = 0.00001,
    base_spread_points: int = 12,
    skip_weekends: bool = True,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = timeframe.delta
    times: list[pd.Timestamp] = []
    t = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    while len(times) < n:
        # Forex week: closed from Friday 21:00 UTC to Sunday 21:00 UTC (approximation).
        wd, hr = t.weekday(), t.hour
        closed = skip_weekends and ((wd == 4 and hr >= 21) or wd == 5 or (wd == 6 and hr < 21))
        if not closed:
            times.append(t)
        t = t + step
    times_idx = pd.DatetimeIndex(times)

    # Alternate regimes: trending blocks and mean-reverting blocks.
    block = max(50, n // 12)
    drift = np.zeros(n)
    vol = np.zeros(n)
    for i in range(0, n, block):
        kind = (i // block) % 3
        drift[i : i + block] = {0: 0.6, 1: 0.0, 2: -0.6}[kind]
        vol[i : i + block] = {0: 1.0, 1: 0.6, 2: 1.6}[kind]
    scale = start_price * 0.00025 * np.sqrt(timeframe.minutes)
    rets = (drift * 0.08 + rng.standard_normal(n) * vol) * scale
    close = start_price + np.cumsum(rets)
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.standard_normal((n, 2))) * scale * vol[:, None] * 0.6
    high = np.maximum(open_, close) + wick[:, 0]
    low = np.minimum(open_, close) - wick[:, 1]
    decimals = round(-np.log10(point))
    spread = (base_spread_points * (0.7 + 0.6 * rng.random(n)) * vol).round().astype(int)
    return pd.DataFrame(
        {
            "time_utc": times_idx,
            "open": open_.round(decimals),
            "high": high.round(decimals),
            "low": low.round(decimals),
            "close": close.round(decimals),
            "tick_volume": rng.integers(20, 400, n),
            "spread_points": spread,
            "real_volume": np.zeros(n, dtype=int),
        }
    )


def resample_bars(m1: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Aggregate M1 bars to a higher timeframe (for consistent multi-TF fixtures)."""
    g = m1.set_index("time_utc").resample(f"{timeframe.minutes}min", label="left", closed="left")
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "tick_volume": g["tick_volume"].sum(),
            "spread_points": g["spread_points"].median(),
            "real_volume": g["real_volume"].sum(),
        }
    ).dropna(subset=["open"])
    out["spread_points"] = out["spread_points"].round().astype(int)
    return out.reset_index()
