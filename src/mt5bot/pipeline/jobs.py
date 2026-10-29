"""Offline jobs for datasets, gated training and backtests; failed training registers nothing."""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mt5bot.backtest.engine import run_backtest
from mt5bot.backtest.metrics import breakdown, trade_metrics
from mt5bot.config.settings import Settings
from mt5bot.core.types import Timeframe
from mt5bot.features.pipeline import feature_names, feature_set_hash
from mt5bot.labeling.meta import label_candidates
from mt5bot.ml.dataset import MetaDataset, build_meta_dataset
from mt5bot.ml.explain import global_importance
from mt5bot.ml.promotion import evaluate_gates
from mt5bot.ml.registry import ModelRegistry
from mt5bot.ml.training import evaluate_on, train_and_evaluate
from mt5bot.pipeline.context import SpecProvider, config_hash, cost_model, feature_config, sizing
from mt5bot.pipeline.inference import META_KIND, ResearchPipeline
from mt5bot.regime.rules import classify_rules
from mt5bot.storage.repositories import BacktestRepository, ExperimentRepository, ModelRepository, iso
from mt5bot.strategies.specialists import build_strategies


def code_version() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - read-only, fixed arguments, git from PATH
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=Path(__file__).resolve().parent,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def candidates_for(
    settings: Settings, feats: pd.DataFrame, tf: Timeframe, strategies: list[str] | None = None
) -> pd.DataFrame:
    regimes = classify_rules(feats)["regime"]
    frames = [
        s.candidates(feats, regimes, tf)
        for s in build_strategies(strategies or list(settings.strategy.enabled), settings.strategy.max_hold_minutes)
    ]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_dataset(
    conn: sqlite3.Connection,
    settings: Settings,
    symbols: list[str],
    tf: Timeframe,
    start: datetime | None,
    end: datetime,
) -> MetaDataset:
    pipe = ResearchPipeline(conn, settings)
    specs = SpecProvider(conn)
    parts = []
    for sym in symbols:
        feats = pipe.feature_frame(sym, tf, start, end)
        if feats.empty:
            continue
        cands = candidates_for(settings, feats, tf)
        if cands.empty:
            continue
        bars = pipe.bars.load(sym, tf, (feats["time_utc"].iloc[0]).to_pydatetime(), end)
        labelled = label_candidates(bars, cands, tf, specs(sym), cost_model(settings, sym), sizing(settings))
        if labelled.empty:
            continue
        if start is not None:
            labelled = labelled[labelled["time_utc"] >= pd.Timestamp(start)]
        # labels that could not be known by `end` are dropped (never train on unfinished outcomes)
        labelled = labelled[labelled["label_available_at"] <= pd.Timestamp(end)]
        parts.append(build_meta_dataset(labelled, feats, feature_names(pipe.fcfg)))
    if not parts:
        return MetaDataset(pd.DataFrame(), pd.Series(dtype=int).to_numpy(), pd.DataFrame(), [])
    common = [c for c in parts[0].feature_names if all(c in p.feature_names for p in parts)]
    X = pd.concat([p.X[common] for p in parts], ignore_index=True)
    meta = pd.concat([p.meta for p in parts], ignore_index=True)
    y = pd.concat([pd.Series(p.y) for p in parts], ignore_index=True).to_numpy()
    order = meta["available_at"].argsort(kind="stable").to_numpy()
    return MetaDataset(X.iloc[order].reset_index(drop=True), y[order], meta.iloc[order].reset_index(drop=True), common)


@dataclass
class TrainOutcome:
    model_id: str | None
    action: str
    reason: str
    summary: dict[str, Any]


def train_job(
    conn: sqlite3.Connection,
    settings: Settings,
    symbols: list[str],
    tf: Timeframe,
    start: datetime | None,
    end: datetime,
    *,
    model_type: str | None = None,
    tune_trials: int = 0,
) -> TrainOutcome:
    ds = build_dataset(conn, settings, symbols, tf, start, end)
    ml, ev = settings.ml, settings.evaluation
    if len(ds) < ml.min_train_samples:
        return TrainOutcome(None, "SKIPPED", f"only {len(ds)} labelled candidates (< {ml.min_train_samples})", {})
    mtype = model_type or ml.model_type
    embargo = tf.delta * (settings.strategy.max_hold_minutes // tf.minutes + 1 + ev.embargo_bars)
    experiments = ExperimentRepository(conn)
    params: dict[str, Any] | None = None
    n_trials = 1
    if tune_trials:
        from mt5bot.ml.tuning import tune

        params, n_trials = tune(
            ds,
            model_type=mtype,
            n_trials=tune_trials,
            seed=ml.random_seed,
            threshold=ml.probability_threshold,
            confidence=ml.conformal_confidence,
            val_fraction=ev.validation_fraction,
            test_fraction=ev.test_fraction,
            embargo=embargo,
        )
    exp_id = experiments.add(
        kind="train",
        config_hash=config_hash(settings),
        code_version=code_version(),
        data_fingerprint=ds.fingerprint(),
        n_trials=n_trials,
        params={"model_type": mtype, "symbols": symbols, "timeframe": tf.value, "params": params},
    )
    total_trials = experiments.total_trials("train")
    try:
        report = train_and_evaluate(
            ds,
            model_type=mtype,
            seed=ml.random_seed,
            threshold=ml.probability_threshold,
            confidence=ml.conformal_confidence,
            val_fraction=ev.validation_fraction,
            test_fraction=ev.test_fraction,
            n_folds=ev.n_folds,
            embargo=embargo,
            cost_multiplier=ev.promotion_cost_multiplier,
            n_trials=total_trials,
            params=params,
        )
    except ValueError as exc:  # e.g. too few rows per split or a single-class window; nothing is registered
        return TrainOutcome(None, "SKIPPED", f"training not possible: {exc}", {"dataset_rows": len(ds)})
    summary = report.summary()
    summary["n_trials_total"] = total_trials
    summary["dataset"] = {"rows": len(ds), "fingerprint": ds.fingerprint(), "symbols": symbols, "timeframe": tf.value}
    try:
        summary["feature_importance"] = global_importance(report.model, ds.X)
    except Exception:
        summary["feature_importance"] = {}
    registry = ModelRegistry(conn, settings.storage.model_dir)
    assert report.train_start is not None and report.train_end is not None
    model_id = registry.register(
        report.model,
        kind=META_KIND,
        feature_set=feature_set_hash(feature_config(settings)),
        train_start=report.train_start,
        train_end=report.train_end,
        dataset_fingerprint=ds.fingerprint(),
        metrics=summary,
        experiment_id=exp_id,
    )
    repo = ModelRepository(conn)
    if report.test_start is not None and report.test_end is not None:
        repo.add_evaluation(model_id, "test", iso(report.test_start), iso(report.test_end), report.test)
    # Re-evaluate the current champion on the SAME untouched test window for a fair comparison.
    champion_on_test = None
    champ = registry.repo.champion(META_KIND)
    if champ is not None:
        try:
            from mt5bot.evaluation.splits import train_val_test

            cm = registry.load(champ["id"])
            _, test_split = train_val_test(ds.meta, ev.validation_fraction, ev.test_fraction, embargo)
            test = ds.subset(test_split.eval_idx)
            if set(cm.feature_names) <= set(test.X.columns):
                champion_on_test = evaluate_on(
                    cm, test, cost_multiplier=ev.promotion_cost_multiplier, n_trials=total_trials
                )
        except Exception as exc:
            summary["champion_comparison_error"] = type(exc).__name__
    gates = evaluate_gates(summary, champion_on_test, ev)
    action = registry.decide(model_id, META_KIND, gates)
    summary["gates"] = gates.gates
    return TrainOutcome(model_id, action, gates.reason(), summary)


def backtest_job(
    conn: sqlite3.Connection,
    settings: Settings,
    symbol: str,
    tf: Timeframe,
    start: datetime | None,
    end: datetime,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    pipe = ResearchPipeline(conn, settings)
    feats = pipe.feature_frame(symbol, tf, start, end)
    if feats.empty:
        return {"error": "no bars for this symbol/timeframe; run `mt5bot collect` first"}
    cands = candidates_for(settings, feats, tf, strategies)
    if start is not None and not cands.empty:
        cands = cands[cands["time_utc"] >= pd.Timestamp(start)]
    bars = pipe.bars.load(symbol, tf, feats["time_utc"].iloc[0].to_pydatetime(), end)
    spec = SpecProvider(conn)(symbol)
    exp = ExperimentRepository(conn).add(
        kind="backtest",
        config_hash=config_hash(settings),
        code_version=code_version(),
        params={"symbol": symbol, "timeframe": tf.value, "strategies": strategies},
    )
    out: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": tf.value,
        "provenance": "SIMULATED",
        "unverified_spec": symbol in pipe.specs.unverified,
        "runs": {},
    }
    s = pd.Timestamp(start) if start else feats["time_utc"].iloc[0]
    for m in settings.evaluation.cost_multipliers:
        res = run_backtest(
            bars,
            cands,
            tf,
            spec,
            cost_model(settings, symbol, m),
            sizing(settings),
            max_concurrent=settings.risk.max_open_positions,
        )
        metrics = trade_metrics(res.trades, settings.risk.reference_balance_usd)
        metrics["skipped"] = res.skipped
        metrics["by_strategy"] = breakdown(res.trades, "strategy")
        metrics["by_regime"] = breakdown(res.trades, "regime")
        run_id = BacktestRepository(conn).save(
            symbol=symbol,
            timeframe=tf.value,
            start=iso(s),
            end=iso(pd.Timestamp(end)),
            strategies=strategies or list(settings.strategy.enabled),
            cost_multiplier=m,
            config={"costs": settings.costs.model_dump(), "risk": settings.risk.model_dump()},
            metrics=metrics,
            trades=res.trades,
            experiment_id=exp,
        )
        out["runs"][f"x{m:g}"] = {"run_id": run_id, **{k: v for k, v in metrics.items() if not k.startswith("by_")}}
        if m == 1.0:
            out["by_strategy"], out["by_regime"] = metrics["by_strategy"], metrics["by_regime"]
    return out
