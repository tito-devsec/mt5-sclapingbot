"""Delayed outcome evaluation and rule-based attribution; rules are listed in docs/ML_GUIDE.md."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from mt5bot.backtest.simulator import BarArrays, CostModel, Sizing, simulate_trade
from mt5bot.config.settings import SelectionSettings
from mt5bot.core.types import Direction, InstrumentSpec, Timeframe
from mt5bot.features.pipeline import FeatureConfig, build_features
from mt5bot.labeling.meta import LABEL_VERSION
from mt5bot.regime.rules import classify_rules
from mt5bot.selection.context import bucket, session_name, vol_bucket
from mt5bot.selection.reward import reward
from mt5bot.storage.repositories import (
    BarRepository,
    DecisionRepository,
    NewsRepository,
    PerformanceRepository,
    parse_ts,
)
from mt5bot.strategies.specialists import REGISTRY

ATTRIBUTION_VERSION = "attr-1.0.0"


@dataclass
class OutcomeReport:
    evaluated: int = 0
    still_pending: int = 0
    not_applicable: int = 0


def _attribute(
    d: sqlite3.Row,
    r: Any,
    hold: pd.DataFrame,
    decision_spread: float,
    decision_atr: float,
    news_during: bool,
    exit_regime: str | None,
) -> tuple[str, dict[str, Any]]:
    ev: dict[str, Any] = {
        "exit_reason": r.exit_reason,
        "net_r": r.net_r,
        "gross_r": r.gross_r,
        "mfe_r": r.mfe_r,
        "mae_r": r.mae_r,
        "bars_held": r.bars_held,
        "attribution_version": ATTRIBUTION_VERSION,
    }
    if d["status"] == "REJECTED":
        return ("INVALID_WIN" if r.net_r > 0 else "RISK_REJECTED"), ev
    expected = int(d["max_hold_bars"])
    tf_delta = Timeframe(d["timeframe"]).delta
    span_bars = int((r.exit_time - r.entry_time) / tf_delta) + 1
    if span_bars > r.bars_held:
        ev["missing_bars"] = span_bars - r.bars_held
        return "STALE_DATA", ev
    entry_spread = float(hold["spread_points"].iloc[0]) if len(hold) else decision_spread
    if decision_spread > 0 and entry_spread > 2 * decision_spread:
        ev["entry_spread"], ev["decision_spread"] = entry_spread, decision_spread
        return "UNREALISTIC_FILL_ASSUMPTION", ev
    hold_range = float(hold["high"].max() - hold["low"].min()) if len(hold) else 0.0
    if news_during and decision_atr > 0 and hold_range > 3 * decision_atr:
        ev["hold_range_atr"] = hold_range / decision_atr
        return "NEWS_VOLATILITY", ev
    if r.gross_r > 0 and r.net_r <= 0:
        return "COST_DOMINATED", ev
    if r.net_r > 0:
        return "VALID_WIN", ev
    strategy = d["selected_strategy"]
    stopped = r.exit_reason in ("STOP", "STOP_GAP")
    if strategy == "breakout" and stopped and r.bars_held <= max(1, expected // 3):
        return "FAILED_BREAKOUT", ev
    if strategy == "trend" and stopped and r.mfe_r >= 1.0:
        return "TREND_REVERSAL", ev
    if r.mfe_r >= 1.0:
        return "POOR_TIMING", ev
    if exit_regime is not None and strategy in REGISTRY:
        required = {x.value for x in REGISTRY[strategy]().spec.required_regimes}
        if exit_regime not in required:
            ev["exit_regime"] = exit_regime
            return "REGIME_MISCLASSIFIED", ev
    if r.net_r >= -1.5:
        return "VALID_LOSS", ev
    return "UNATTRIBUTED", ev


def evaluate_pending(
    conn: sqlite3.Connection,
    *,
    spec_for: Callable[[str], InstrumentSpec],
    costs_for: Callable[[str], CostModel],
    sizing: Sizing,
    selection: SelectionSettings,
    now: pd.Timestamp,
    feature_cfg: FeatureConfig | None = None,
    limit: int = 500,
) -> OutcomeReport:
    """Evaluate decisions whose horizons closed by now; every rule-eligible candidate updates memory."""
    decisions = DecisionRepository(conn)
    bars_repo = BarRepository(conn)
    perf = PerformanceRepository(conn)
    news = NewsRepository(conn)
    rep = OutcomeReport()
    for d in decisions.pending(limit):
        cands = [c for c in decisions.candidates(d["id"]) if c["rule_eligible"] and c["stop_distance"] is not None]
        has_selection = d["selected_strategy"] is not None and d["direction"] is not None
        if not has_selection and not cands:
            decisions.save_outcome(
                d["id"],
                {
                    "label_version": LABEL_VERSION,
                    "attribution": "NOT_APPLICABLE",
                    "eval_status": "NOT_APPLICABLE",
                    "evidence": {"reason": f"no candidate for status {d['status']}"},
                },
            )
            rep.not_applicable += 1
            continue
        tf = Timeframe(d["timeframe"])
        bar_t = parse_ts(d["bar_time_utc"])
        assert bar_t is not None
        holds = [int(c["max_hold_bars"]) for c in cands] + ([int(d["max_hold_bars"])] if has_selection else [])
        horizon_end = bar_t + tf.delta * (max(holds) + 1)
        if horizon_end + tf.delta > now:
            rep.still_pending += 1
            continue
        window = bars_repo.load(d["symbol"], tf, bar_t - tf.delta * 600, horizon_end + tf.delta)
        if window.empty or window["time_utc"].iloc[-1] < horizon_end:
            rep.still_pending += 1  # data not collected yet - never label from incomplete data
            continue
        arrays = BarArrays(window, tf)
        i = arrays.index_of(bar_t)
        if i is None:
            rep.still_pending += 1
            continue
        spec, costs = spec_for(d["symbol"]), costs_for(d["symbol"])
        feats = json.loads(d["features_json"] or "{}")

        for c in cands:  # unbiased performance evidence from every rule-eligible candidate
            rc = simulate_trade(
                arrays,
                i,
                Direction(c["direction"]),
                float(c["stop_distance"]),
                float(c["target_distance"]),
                int(c["max_hold_bars"]),
                spec,
                costs,
                sizing,
                strategy=c["strategy"],
            )
            if rc.status != "FILLED" or rc.exit_time is None:
                continue
            rw = reward(rc.net_r, rc.mae_r, selection)
            decisions.save_candidate_outcome(
                c["id"],
                {
                    "label_version": LABEL_VERSION,
                    "exit_reason": rc.exit_reason,
                    "exit_time_utc": rc.exit_time.isoformat(),
                    "net_r": rc.net_r,
                    "mae_r": rc.mae_r,
                    "reward": rw,
                },
            )
            perf.update(context_bucket({**dict(d), "selected_strategy": c["strategy"]}, feats), rw)

        if not has_selection:
            decisions.save_outcome(
                d["id"],
                {
                    "label_version": LABEL_VERSION,
                    "attribution": "NOT_SELECTED",
                    "eval_status": "DONE",
                    "evidence": {"candidates_evaluated": len(cands), "status": d["status"]},
                },
            )
            rep.evaluated += 1
            continue

        r = simulate_trade(
            arrays,
            i,
            Direction(d["direction"]),
            float(d["stop_distance"]),
            float(d["target_distance"]),
            int(d["max_hold_bars"]),
            spec,
            costs,
            sizing,
            strategy=d["selected_strategy"],
        )
        if r.status != "FILLED":
            decisions.save_outcome(
                d["id"],
                {
                    "label_version": LABEL_VERSION,
                    "attribution": "STALE_DATA" if r.status == "NO_NEXT_BAR" else "UNATTRIBUTED",
                    "eval_status": "DONE",
                    "evidence": {"simulation_status": r.status},
                },
            )
            rep.evaluated += 1
            continue
        assert r.exit_time is not None
        hold = window[(window["time_utc"] > bar_t) & (window["time_utc"] <= r.exit_time)]
        news_rows = news.available_at(
            r.exit_time, int((r.exit_time - bar_t).total_seconds() // 60) + 1, {d["symbol"][:3], d["symbol"][3:6]}
        )
        news_during = any(parse_ts(n["available_at"]) > bar_t for n in news_rows)
        exit_regime = None
        try:
            fx = build_features(window[window["time_utc"] <= r.exit_time], tf, spec.point, None, feature_cfg)
            exit_regime = str(classify_rules(fx.tail(1))["regime"].iloc[-1])
        except Exception:
            exit_regime = None
        code, evidence = _attribute(
            d,
            r,
            hold,
            float(feats.get("spread_points", 0.0) or 0.0),
            float(feats.get("atr_14", 0.0) or 0.0),
            news_during,
            exit_regime,
        )
        decisions.save_outcome(
            d["id"],
            {
                "label_version": LABEL_VERSION,
                "exit_reason": r.exit_reason,
                "exit_time_utc": r.exit_time.isoformat(),
                "gross_r": r.gross_r,
                "net_r": r.net_r,
                "mae_r": r.mae_r,
                "mfe_r": r.mfe_r,
                "reward": reward(r.net_r, r.mae_r, selection),
                "attribution": code,
                "evidence": evidence,
                "eval_status": "DONE",
            },
        )
        rep.evaluated += 1
    return rep


def context_bucket(d: sqlite3.Row | dict[str, Any], feats: dict[str, Any]) -> dict[str, str]:
    return bucket(
        str(d["selected_strategy"]),
        str(d["symbol"]),
        str(d["timeframe"]),
        str(d["regime"]),
        session_name(feats),
        vol_bucket(feats.get("vol_pctile")),
        "news" if json.loads(d["news_ids_json"] or "[]") else "none",
    )
