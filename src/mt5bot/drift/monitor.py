"""Turns detector outputs into persisted alerts with conservative responses; never retrains or promotes."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from mt5bot.drift.detectors import (
    PageHinkley,
    adwin_change,
    brier_degradation,
    missed_bars,
    psi,
    regime_frequency_pvalue,
    spread_shift,
)
from mt5bot.storage.repositories import DriftRepository, StrategyStatusRepository

PSI_WARN, PSI_CRIT = 0.10, 0.25
REGIME_P = 0.01
BRIER_DEGRADE = 0.25
SPREAD_P95_RATIO = 1.5
FEED_MISSED = 3
MONITORED_FEATURES = ["ret_5", "atr_pct", "rv_30", "adx_14", "rsi_14", "spread_atr", "range_expansion"]


@dataclass
class DriftAlert:
    detected_at: pd.Timestamp
    kind: str
    severity: str
    scope: str
    metric: str
    value: float | None
    threshold: float | None
    evidence: dict[str, Any] = field(default_factory=dict)
    response: str = ""


def check_inputs(ref: pd.DataFrame, cur: pd.DataFrame, scope: str, now: pd.Timestamp) -> list[DriftAlert]:
    out = []
    for col in MONITORED_FEATURES:
        if col not in ref or col not in cur:
            continue
        v = psi(ref[col].to_numpy(), cur[col].to_numpy())
        if math.isfinite(v) and v >= PSI_WARN:
            sev = "CRITICAL" if v >= PSI_CRIT else "WARNING"
            out.append(
                DriftAlert(
                    now,
                    "INPUT_DRIFT",
                    sev,
                    f"{scope}:{col}",
                    "psi",
                    v,
                    PSI_WARN,
                    {"n_ref": len(ref), "n_cur": len(cur)},
                    "increase uncertainty",
                )
            )
    return out


def check_regimes(ref: pd.Series, cur: pd.Series, scope: str, now: pd.Timestamp) -> list[DriftAlert]:
    p = regime_frequency_pvalue(ref, cur)
    if math.isfinite(p) and p < REGIME_P:
        return [
            DriftAlert(
                now,
                "REGIME_CHANGE",
                "WARNING",
                scope,
                "chi2_pvalue",
                p,
                REGIME_P,
                {"current": cur.value_counts(normalize=True).round(3).to_dict()},
                "increase uncertainty",
            )
        ]
    return []


def check_prediction_quality(
    baseline_brier: float, y: np.ndarray, p: np.ndarray, scope: str, now: pd.Timestamp
) -> list[DriftAlert]:
    d = brier_degradation(baseline_brier, y, p)
    if math.isfinite(d) and d > BRIER_DEGRADE and len(y) >= 30:
        return [
            DriftAlert(
                now,
                "PREDICTION_QUALITY",
                "WARNING",
                scope,
                "brier_degradation",
                d,
                BRIER_DEGRADE,
                {"n": len(y)},
                "keep champion unchanged; request re-evaluation",
            )
        ]
    return []


def check_strategy(rewards: np.ndarray, scope: str, now: pd.Timestamp) -> list[DriftAlert]:
    idx = PageHinkley().detect(rewards)
    if idx is None:
        return []
    second = adwin_change(rewards)
    sev = "CRITICAL" if second is not None else "WARNING"
    return [
        DriftAlert(
            now,
            "STRATEGY_PERFORMANCE",
            sev,
            scope,
            "page_hinkley_index",
            float(idx),
            None,
            {
                "n": len(rewards),
                "recent_mean": float(np.mean(rewards[idx:])) if idx < len(rewards) else None,
                "adwin_index": second,
            },
            "suspend strategy" if sev == "CRITICAL" else "increase uncertainty",
        )
    ]


def check_feed(
    last_bar: pd.Timestamp | None, now: pd.Timestamp, tf_minutes: int, scope: str, market_open: bool = True
) -> list[DriftAlert]:
    m = missed_bars(last_bar, now, tf_minutes)
    if market_open and (m is None or m >= FEED_MISSED):
        return [
            DriftAlert(
                now,
                "FEED_FAILURE",
                "CRITICAL",
                scope,
                "missed_bars",
                None if m is None else float(m),
                FEED_MISSED,
                {"last_bar": str(last_bar)},
                "NO_TRADE until data is fresh",
            )
        ]
    return []


def check_costs(ref_spread: np.ndarray, cur_spread: np.ndarray, scope: str, now: pd.Timestamp) -> list[DriftAlert]:
    ratio, p = spread_shift(ref_spread, cur_spread)
    if math.isfinite(ratio) and ratio > SPREAD_P95_RATIO:
        return [
            DriftAlert(
                now,
                "EXECUTION_COST",
                "WARNING",
                scope,
                "p95_spread_ratio",
                ratio,
                SPREAD_P95_RATIO,
                {"ks_pvalue": p},
                "raise local alert; risk spread limits still apply",
            )
        ]
    return []


def persist(conn: sqlite3.Connection, alerts: list[DriftAlert]) -> int:
    """Store alerts, skipping ones already open for the same kind and scope. Returns the number stored."""
    repo = DriftRepository(conn)
    status = StrategyStatusRepository(conn)
    open_keys = {(r["kind"], r["scope"]) for r in repo.open_alerts()}
    stored = 0
    for a in alerts:
        if (a.kind, a.scope) in open_keys:
            continue
        repo.add(a)
        open_keys.add((a.kind, a.scope))
        stored += 1
        if a.kind == "STRATEGY_PERFORMANCE" and a.severity == "CRITICAL":
            strategy, symbol, timeframe = a.scope.split(":")[:3]
            status.set_status(strategy, symbol, timeframe, "SUSPENDED", f"drift alert: {a.metric}")
    return stored


def resolve(conn: sqlite3.Connection, kind: str, scope: str) -> int:
    repo = DriftRepository(conn)
    ids = [r["id"] for r in repo.open_alerts() if r["kind"] == kind and r["scope"] == scope]
    for i in ids:
        repo.resolve(i)
    return len(ids)
