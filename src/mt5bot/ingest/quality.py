"""Bar validation. Bad rows are rejected and reported - never repaired or filled."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from mt5bot.core.types import Timeframe


@dataclass
class QualityResult:
    clean: pd.DataFrame
    issues: list[dict[str, Any]] = field(default_factory=list)
    rejected: int = 0
    forming_dropped: int = 0


def _issue(symbol: str, tf: Timeframe, ts: Any, issue: str, severity: str, detail: str = "") -> dict[str, Any]:
    t = None if ts is None or pd.isna(ts) else pd.Timestamp(ts).isoformat()
    return {
        "symbol": symbol,
        "timeframe": tf.value,
        "time_utc": t,
        "issue": issue,
        "severity": severity,
        "detail": detail,
    }


def is_weekend_gap(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    """True if a gap overlaps the FX weekend close (approx. Fri 20:00 to Mon 00:00 UTC)."""
    if end - start > pd.Timedelta(days=4):
        return False
    t = start
    while t <= end:
        wd, hr = t.weekday(), t.hour
        if wd == 5 or (wd == 4 and hr >= 20) or (wd == 6):
            return True
        t += pd.Timedelta(hours=1)
    return False


def validate_bars(
    df: pd.DataFrame, symbol: str, tf: Timeframe, now_utc: pd.Timestamp, previous_last: pd.Timestamp | None = None
) -> QualityResult:
    issues: list[dict[str, Any]] = []
    if df.empty:
        return QualityResult(df.copy(), issues)
    work = df.copy()
    n0 = len(work)

    # 1. Unmappable server times (DST gap/overlap).
    nat = work["time_utc"].isna()
    if nat.any():
        issues.append(
            _issue(
                symbol,
                tf,
                None,
                "UNMAPPABLE_SERVER_TIME",
                "ERROR",
                f"{int(nat.sum())} bar(s) fell in a DST gap/overlap",
            )
        )
        work = work[~nat]

    # 2. Ordering.
    if not work["time_utc"].is_monotonic_increasing:
        issues.append(
            _issue(
                symbol,
                tf,
                work["time_utc"].iloc[0],
                "OUT_OF_ORDER",
                "WARNING",
                "records were not in time order; sorted",
            )
        )
        work = work.sort_values("time_utc", kind="stable")

    # 3. Duplicates: identical copies collapse; conflicting copies are all rejected.
    value_cols = ["open", "high", "low", "close"]
    dup_mask = work.duplicated("time_utc", keep=False)
    if dup_mask.any():
        bad_times = []
        for t, grp in work[dup_mask].groupby("time_utc"):
            if len(grp[value_cols].drop_duplicates()) > 1:
                bad_times.append(t)
                issues.append(
                    _issue(symbol, tf, t, "CONFLICTING_DUPLICATE", "ERROR", f"{len(grp)} different records for one bar")
                )
            else:
                issues.append(_issue(symbol, tf, t, "DUPLICATE", "INFO", f"{len(grp)} identical copies"))
        work = work[~work["time_utc"].isin(bad_times)].drop_duplicates("time_utc", keep="first")

    # 4. Timeframe alignment.
    minutes = work["time_utc"].dt.hour * 60 + work["time_utc"].dt.minute
    misaligned = (minutes % tf.minutes != 0) | (work["time_utc"].dt.second != 0)
    for t in work.loc[misaligned, "time_utc"]:
        issues.append(_issue(symbol, tf, t, "MISALIGNED_TIMESTAMP", "ERROR", f"not on a {tf.value} boundary"))
    work = work[~misaligned]

    # 5-7. Values.
    prices = work[value_cols]
    bad_values = prices.isna().any(axis=1) | (prices <= 0).any(axis=1)
    bad_ohlc = ~bad_values & (
        (work["high"] < work[["open", "close", "low"]].max(axis=1))
        | (work["low"] > work[["open", "close", "high"]].min(axis=1))
    )
    bad_meta = (work["spread_points"].isna()) | (work["spread_points"] < 0) | (work["tick_volume"] < 0)
    for t in work.loc[bad_values, "time_utc"]:
        issues.append(_issue(symbol, tf, t, "INVALID_PRICE", "ERROR", "missing or non-positive price"))
    for t in work.loc[bad_ohlc, "time_utc"]:
        issues.append(_issue(symbol, tf, t, "INVALID_OHLC", "ERROR", "high/low inconsistent with open/close"))
    for t in work.loc[bad_meta & ~bad_values & ~bad_ohlc, "time_utc"]:
        issues.append(_issue(symbol, tf, t, "INVALID_SPREAD_OR_VOLUME", "ERROR", "negative or missing"))
    work = work[~(bad_values | bad_ohlc | bad_meta)]

    # 8. Only completed candles are stored.
    forming = work["time_utc"] + tf.delta > now_utc
    forming_dropped = int(forming.sum())
    work = work[~forming]

    # 9. Gaps (reported, never filled).
    times = work["time_utc"]
    if previous_last is not None and len(times):
        times = pd.concat([pd.Series([previous_last]), times], ignore_index=True)
    diffs = times.diff()
    for i in np.flatnonzero((diffs > tf.delta).to_numpy()):
        g_start, g_end = times.iloc[i - 1], times.iloc[i]
        missing = int((g_end - g_start) / tf.delta) - 1
        if is_weekend_gap(g_start, g_end):
            kind, sev = "GAP_EXPECTED_WEEKEND", "INFO"
        elif (g_end - g_start) <= pd.Timedelta(minutes=10) + tf.delta and g_start.hour in (20, 21, 22, 23):
            kind, sev = "GAP_DAILY_ROLLOVER", "INFO"
        else:
            kind, sev = "GAP_UNEXPECTED", "WARNING"
        issues.append(_issue(symbol, tf, g_start, kind, sev, f"{missing} bar(s) missing until {g_end.isoformat()}"))

    # 10. Spread outliers (kept, flagged).
    if len(work) >= 20:
        med = float(work["spread_points"].median())
        outl = work["spread_points"] > max(5 * med, med + 50)
        for t in work.loc[outl, "time_utc"]:
            issues.append(_issue(symbol, tf, t, "SPREAD_OUTLIER", "WARNING", f"spread > 5x median ({med:g})"))

    rejected = n0 - len(work) - forming_dropped
    return QualityResult(work.reset_index(drop=True), issues, rejected, forming_dropped)
