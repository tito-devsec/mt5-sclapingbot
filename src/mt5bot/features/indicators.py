"""Causal technical indicators in pandas; warm-up rows stay NaN and are never filled."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[:1] = np.nan  # first bar has no previous close
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder((-delta).clip(lower=0), n)
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    out = out.where(loss != 0, 100.0)
    return out.where(gain.notna() & loss.notna())


def dmi_adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    plus_dm.iloc[:1] = np.nan
    minus_dm.iloc[:1] = np.nan
    a = atr(high, low, close, n)
    plus_di = 100 * wilder(plus_dm, n) / a
    minus_di = 100 * wilder(minus_dm, n) / a
    denom = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / denom.where(denom != 0)
    adx = wilder(dx, n)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return (2 * k * sd) / mid


def donchian_prev(high: pd.Series, low: pd.Series, n: int) -> pd.DataFrame:
    """Highest high / lowest low of the previous ``n`` bars (excluding the current one)."""
    return pd.DataFrame(
        {
            "upper": high.shift(1).rolling(n, min_periods=n).max(),
            "lower": low.shift(1).rolling(n, min_periods=n).min(),
        }
    )


def realized_vol(close: pd.Series, n: int) -> pd.Series:
    lr = np.log(close).diff()
    return lr.rolling(n, min_periods=n).std(ddof=1)


def rolling_percentile(x: pd.Series, window: int) -> pd.Series:
    """Rank of the current value within the trailing ``window`` values (0..1)."""
    return x.rolling(window, min_periods=window).rank(pct=True)


def run_length(cond: pd.Series) -> pd.Series:
    """Consecutive count of True values ending at each row."""
    c = cond.fillna(False).astype(bool)
    groups = (~c).cumsum()
    return c.groupby(groups).cumsum().astype(float)
