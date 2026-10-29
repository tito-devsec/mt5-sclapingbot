"""The single research-decision path (features to risk to record); it contains no order submission."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from mt5bot.backtest.simulator import estimated_cost_r
from mt5bot.config.settings import Settings
from mt5bot.core.errors import ModelUnavailableError
from mt5bot.core.logging import log_event
from mt5bot.core.types import DecisionStatus, Direction, Regime, Timeframe
from mt5bot.features.pipeline import HIGHER_TF, build_features, feature_set_hash
from mt5bot.gateway.base import AccountSnapshot
from mt5bot.memory.outcomes import context_bucket
from mt5bot.ml.dataset import inference_frame
from mt5bot.ml.registry import ModelRegistry
from mt5bot.ml.training import FittedMetaModel
from mt5bot.news.event_filter import EventRiskFilter
from mt5bot.pipeline.context import SpecProvider, config_hash, cost_model, feature_config, sizing
from mt5bot.regime.rules import REGIME_RULES_VERSION, classify_rules, out_of_distribution
from mt5bot.risk.engine import RiskCandidate, RiskContext, RiskEngine
from mt5bot.selection.selector import select
from mt5bot.storage.repositories import (
    BarRepository,
    DecisionRepository,
    DriftRepository,
    PerformanceRepository,
    SimStateRepository,
    StrategyStatusRepository,
    iso,
)
from mt5bot.strategies.specialists import build_strategies

log = logging.getLogger(__name__)
META_KIND = "meta_label"
OOD_COLUMNS = ["atr_pct", "rv_30", "adx_14", "range_expansion", "spread_atr"]


@dataclass
class DecisionRecord:
    id: int | None
    status: DecisionStatus
    symbol: str
    timeframe: str
    bar_time: pd.Timestamp
    regime: str
    rationale: str
    strategy: str | None = None
    direction: str | None = None
    risk_rejections: list[str] = field(default_factory=list)
    model_id: str | None = None


class ResearchPipeline:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.s = settings
        self.fcfg = feature_config(settings)
        self.fs_hash = feature_set_hash(self.fcfg)
        self.specs = SpecProvider(conn)
        self.bars = BarRepository(conn)
        self.decisions = DecisionRepository(conn)
        self.perf = PerformanceRepository(conn)
        self.strategy_status = StrategyStatusRepository(conn)
        self.drift = DriftRepository(conn)
        self.sim_state = SimStateRepository(conn)
        self.registry = ModelRegistry(conn, settings.storage.model_dir)
        self.risk = RiskEngine(settings.risk)
        self.events = EventRiskFilter(conn, settings.news, settings.risk)
        self.strategies = build_strategies(list(settings.strategy.enabled), settings.strategy.max_hold_minutes)
        self.cfg_hash = config_hash(settings)
        self._model_cache: tuple[str, FittedMetaModel] | None = None

    # -- data -------------------------------------------------------------
    def warmup_bars(self) -> int:
        return self.fcfg.vol_percentile_window + 400

    def feature_frame(self, symbol: str, tf: Timeframe, start: datetime | None, end: datetime) -> pd.DataFrame:
        """Features for bars closed by end; start=None uses all stored history."""
        lookback = tf.delta * self.warmup_bars() * 2
        s = (pd.Timestamp(start) - lookback) if start is not None else None
        bars = self.bars.load(symbol, tf, None if s is None else s.to_pydatetime(), end)
        bars = bars[bars["time_utc"] + tf.delta <= pd.Timestamp(end)]
        htf = HIGHER_TF[tf]
        higher = None
        if htf is not None:
            hs = None if s is None else (s - htf.delta * 200).to_pydatetime()
            hb = self.bars.load(symbol, htf, hs, end)
            higher = hb[hb["time_utc"] + htf.delta <= pd.Timestamp(end)]
        if bars.empty:
            return pd.DataFrame()
        feats = build_features(bars, tf, self.specs(symbol).point, higher, self.fcfg)
        return feats.join(bars.set_index("time_utc")[["close"]], on="time_utc")

    # -- model ------------------------------------------------------------
    def champion(self, decision_time: pd.Timestamp, mode: str) -> tuple[str | None, FittedMetaModel | None, str]:
        row = self.registry.repo.champion(META_KIND)
        if row is None:
            return None, None, "no validated champion model; using the evidence-only baseline"
        if mode in ("HISTORICAL", "SIMULATED") and pd.Timestamp(row["train_end"]) >= decision_time:
            return None, None, "champion was trained on data after this decision time; not used (no look-ahead)"
        try:
            if self._model_cache is None or self._model_cache[0] != row["id"]:
                self._model_cache = (row["id"], self.registry.load(row["id"]))
            return row["id"], self._model_cache[1], f"champion {row['id']}"
        except ModelUnavailableError as exc:
            log_event(log, logging.ERROR, "champion unavailable", code=exc.code)
            return None, None, f"champion unavailable ({exc.code}); using the evidence-only baseline"

    # -- decisions ----------------------------------------------------------
    def decide(
        self,
        feats: pd.DataFrame,
        row_idx: int,
        symbol: str,
        tf: Timeframe,
        *,
        mode: str,
        decided_at: pd.Timestamp,
        account: AccountSnapshot | None = None,
        data_age_seconds: float | None = None,
    ) -> DecisionRecord:
        row = feats.iloc[row_idx]
        bar_time = pd.Timestamp(row["time_utc"])
        decision_time = pd.Timestamp(row["available_at"])
        spec = self.specs(symbol)
        history = feats.iloc[max(0, row_idx - 3000) : max(0, row_idx - 100)]
        regimes = classify_rules(feats.iloc[[row_idx]])
        regime = str(regimes["regime"].iloc[0])
        regime_conf = float(regimes["regime_confidence"].iloc[0])
        notes: list[str] = []
        if (
            regime != Regime.UNCERTAIN.value
            and len(history) > 200
            and bool(out_of_distribution(feats.iloc[[row_idx]], history, OOD_COLUMNS).iloc[0])
        ):
            regime, regime_conf = Regime.UNCERTAIN.value, 0.0
            notes.append("inputs out of distribution vs. recent history")

        open_critical = [
            a
            for a in self.drift.open_alerts()
            if a["severity"] == "CRITICAL"
            and a["kind"] in ("INPUT_DRIFT", "FEED_FAILURE")
            and a["scope"].startswith(f"{symbol}:{tf.value}")
        ]
        data_ok = not open_critical and (
            data_age_seconds is None or data_age_seconds <= self.s.risk.max_data_age_seconds
        )
        if open_critical:
            notes.append("open critical drift/feed alert")

        frames = [
            st.candidates(
                feats.iloc[max(0, row_idx - 1) : row_idx + 1],
                pd.Series([regime] * min(2, row_idx + 1), index=feats.index[max(0, row_idx - 1) : row_idx + 1]),
                tf,
            )
            for st in self.strategies
        ]
        cands = (
            pd.concat([f for f in frames if not f.empty], ignore_index=True)
            if any(not f.empty for f in frames)
            else pd.DataFrame()
        )
        if not cands.empty:
            cands = cands[cands["time_utc"] == bar_time].reset_index(drop=True)

        if self.s.selection.mode == "ml":
            model_id, model, model_note = self.champion(decision_time, mode)
        else:
            model_id, model, model_note = None, None, f"{self.s.selection.mode}-based selection (no ML model used)"
        notes.append(model_note)
        model_input = None
        if model is not None and not cands.empty:
            try:
                model_input = inference_frame(feats.iloc[[row_idx]], cands, model.feature_names)
            except KeyError as exc:
                model, model_id = None, None
                notes.append(f"champion feature mismatch ({exc}); evidence-only baseline")

        feats_dict = {
            k: (None if isinstance(v, float) and math.isnan(v) else v)
            for k, v in row.drop(["time_utc", "available_at"]).items()
        }
        news = self.events.evaluate(symbol, decision_time.to_pydatetime())
        news_flag = "news" if news.news_ids else "none"
        suspended = self.strategy_status.suspended(symbol, tf.value)

        def stats_for(c: Any) -> tuple[int, float, float] | None:
            b = context_bucket(
                {
                    "selected_strategy": c.strategy,
                    "symbol": symbol,
                    "timeframe": tf.value,
                    "regime": regime,
                    "news_ids_json": json.dumps(news.news_ids),
                },
                feats_dict,
            )
            b["news"] = news_flag
            return self.perf.get(b)

        result = select(
            cands,
            regime=regime,
            data_ok=data_ok,
            suspended=suspended,
            stats_for=stats_for,
            cfg=self.s.selection,
            model=model,
            model_input=model_input,
        )
        status = result.status
        risk_records: list[dict[str, Any]] = []
        chosen = cands.iloc[result.chosen] if result.chosen is not None else None
        est_cost = None
        if chosen is not None:
            costs = cost_model(self.s, symbol)
            est_cost = estimated_cost_r(
                spec, float(row["spread_points"]), float(chosen["stop_distance"]), costs, price=float(row["close"])
            )
            assert result.chosen is not None
            p = result.scores[result.chosen].probability
            uncertainty = (1.0 - p) if p is not None else 0.5  # rules and evidence modes: fixed, documented value
            realized = self.sim_state.realized(mode, decision_time.to_pydatetime())
            budget = sizing(self.s).risk_budget_usd
            day = (
                realized[
                    pd.to_datetime(realized["exit_time_utc"], utc=True, format="ISO8601").dt.date
                    == decision_time.date()
                ]
                if not realized.empty
                else realized
            )
            equity_curve = self.s.risk.reference_balance_usd + np.cumsum(realized["net_r"].to_numpy()[::-1]) * budget
            streak = 0
            for v in realized["net_r"].to_numpy():
                if v <= 0:
                    streak += 1
                else:
                    break
            ctx = RiskContext(
                mode=mode,
                expected_currency=self.s.mt5.expected_currency,
                account=account,
                data_age_seconds=data_age_seconds,
                data_quality_ok=data_ok,
                upcoming_high_impact_events=news.upcoming_high_impact,
                breaking_news_flag=news.breaking_news,
                news_stale=news.stale,
                daily_pnl_usd=float(day["net_r"].sum() * budget) if len(day) else 0.0,
                equity_usd=float(equity_curve[-1]) if len(equity_curve) else self.s.risk.reference_balance_usd,
                peak_equity_usd=float(max(self.s.risk.reference_balance_usd, equity_curve.max()))
                if len(equity_curve)
                else self.s.risk.reference_balance_usd,
                consecutive_losses=streak,
                open_positions=self.sim_state.open_positions(mode, decision_time.to_pydatetime()),
                suspended_strategies=suspended,
            )
            rc = RiskCandidate(
                symbol=symbol,
                strategy=str(chosen["strategy"]),
                direction=Direction(chosen["direction"]),
                stop_distance=float(chosen["stop_distance"]),
                target_distance=float(chosen["target_distance"]),
                price=float(row["close"]),
                spread_points=float(row["spread_points"]),
                atr=float(row["atr_14"]),
                vol_pctile=float(row["vol_pctile"]),
                est_cost_r=float(est_cost),
                model_uncertainty=float(uncertainty),
                spec=spec,
            )
            verdict = self.risk.evaluate(rc, ctx)
            risk_records = [c.as_record() for c in verdict.checks]
            if not verdict.approved:
                status = DecisionStatus.REJECTED

        rationale = "; ".join([result.rationale, *notes])
        if status is DecisionStatus.REJECTED:
            rationale = (
                "risk rejected: " + ", ".join(r["code"] for r in risk_records if not r["passed"]) + "; " + rationale
            )
        decision = {
            "decided_at": iso(decided_at),
            "bar_time_utc": iso(bar_time),
            "symbol": symbol,
            "timeframe": tf.value,
            "mode": mode,
            "status": status.value,
            "regime": regime,
            "regime_confidence": regime_conf,
            "feature_set": self.fs_hash,
            "features_json": json.dumps(feats_dict, default=float),
            "news_ids_json": json.dumps(news.news_ids),
            "selected_strategy": None if chosen is None else str(chosen["strategy"]),
            "strategy_version": None if chosen is None else str(chosen["strategy_version"]),
            "direction": None if chosen is None else str(chosen["direction"]),
            "entry_ref": float(row["close"]),
            "stop_distance": None if chosen is None else float(chosen["stop_distance"]),
            "target_distance": None if chosen is None else float(chosen["target_distance"]),
            "max_hold_bars": None if chosen is None else int(chosen["max_hold_bars"]),
            "est_cost_r": est_cost,
            "rationale": rationale[:2000],
            "model_versions_json": json.dumps({"meta_label": model_id, "regime": REGIME_RULES_VERSION}),
            "config_hash": self.cfg_hash,
        }
        cand_records = []
        for sc in result.scores:
            c = cands.iloc[sc.index]
            cand_records.append(
                {
                    **sc.as_record(),
                    "rule_eligible": bool(c["eligible"]),
                    "stop_distance": float(c["stop_distance"]),
                    "target_distance": float(c["target_distance"]),
                    "max_hold_bars": int(c["max_hold_bars"]),
                }
            )
        did = self.decisions.save(
            decision,
            cand_records,
            risk_records,
            [{"model": REGIME_RULES_VERSION, "regime": regime, "confidence": regime_conf}],
        )
        return DecisionRecord(
            did,
            status,
            symbol,
            tf.value,
            bar_time,
            regime,
            rationale,
            None if chosen is None else str(chosen["strategy"]),
            None if chosen is None else str(chosen["direction"]),
            [r["code"] for r in risk_records if not r["passed"]],
            model_id,
        )

    def recommend(
        self, symbol: str, tf: Timeframe, *, mode: str, now: datetime, account: AccountSnapshot | None = None
    ) -> DecisionRecord | None:
        """Decide on the most recent closed bar. Returns None if no usable bar exists."""
        recent = pd.Timestamp(now) - tf.delta * self.warmup_bars() * 2  # bounded window for live decisions
        feats = self.feature_frame(symbol, tf, recent.to_pydatetime(), now)
        if feats.empty:
            return None
        age = (pd.Timestamp(now) - pd.Timestamp(feats["available_at"].iloc[-1])).total_seconds()
        return self.decide(
            feats,
            len(feats) - 1,
            symbol,
            tf,
            mode=mode,
            decided_at=pd.Timestamp(now),
            account=account,
            data_age_seconds=age if mode == "OBSERVED_DEMO" else None,
        )

    def replay(
        self,
        symbol: str,
        tf: Timeframe,
        start: datetime,
        end: datetime,
        *,
        mode: str = "HISTORICAL",
        evaluate_every: int = 60,
        on_progress: Any = None,
    ) -> dict[str, int]:
        """Walk forward through history, evaluating outcomes on the replay clock so memory never sees the future."""
        from mt5bot.memory.outcomes import evaluate_pending

        feats = self.feature_frame(symbol, tf, start, end)
        regimes = classify_rules(feats)["regime"]
        fired = np.zeros(len(feats), dtype=bool)
        for st in self.strategies:
            c = st.candidates(feats, regimes, tf)
            if not c.empty:
                fired |= feats["time_utc"].isin(c["time_utc"]).to_numpy()
        fired &= (feats["time_utc"] >= pd.Timestamp(start)).to_numpy()
        counts: dict[str, int] = {}
        for k, i in enumerate(np.flatnonzero(fired)):
            t = pd.Timestamp(feats["available_at"].iloc[i])
            if k % evaluate_every == 0:
                evaluate_pending(
                    self.conn,
                    spec_for=self.specs,
                    costs_for=lambda sym: cost_model(self.s, sym),
                    sizing=sizing(self.s),
                    selection=self.s.selection,
                    now=t,
                    feature_cfg=self.fcfg,
                )
            rec = self.decide(feats, int(i), symbol, tf, mode=mode, decided_at=t)
            counts[rec.status.value] = counts.get(rec.status.value, 0) + 1
            if on_progress:
                on_progress(k)
        evaluate_pending(
            self.conn,
            spec_for=self.specs,
            costs_for=lambda sym: cost_model(self.s, sym),
            sizing=sizing(self.s),
            selection=self.s.selection,
            now=pd.Timestamp(end),
            feature_cfg=self.fcfg,
        )
        return counts
