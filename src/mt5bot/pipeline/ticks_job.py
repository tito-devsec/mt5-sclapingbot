"""Tick collection through the Python MT5 API, either for a full range or only for ambiguous bars."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from mt5bot.backtest.engine import run_backtest
from mt5bot.config.settings import Settings
from mt5bot.core.types import Timeframe
from mt5bot.gateway.base import MarketGateway
from mt5bot.gateway.symbols import resolve_symbol
from mt5bot.pipeline.context import SpecProvider, cost_model, sizing
from mt5bot.storage.repositories import BarRepository, QuoteRepository


@dataclass
class TickReport:
    windows: int
    ticks_inserted: int
    ambiguous_trades: int = 0


def _broker(gw: MarketGateway, settings: Settings, symbol: str) -> str:
    return resolve_symbol(symbol, gw.symbols(), settings.mt5.symbol_map).name


def collect_ticks(
    gw: MarketGateway,
    conn: sqlite3.Connection,
    settings: Settings,
    symbol: str,
    start: datetime,
    end: datetime,
    chunk: timedelta = timedelta(hours=6),
) -> TickReport:
    broker = _broker(gw, settings, symbol)
    repo = QuoteRepository(conn)
    t, windows, inserted = pd.Timestamp(start), 0, 0
    while t < pd.Timestamp(end):
        e = min(t + chunk, pd.Timestamp(end))
        inserted += repo.insert(symbol, gw.quotes(broker, t.to_pydatetime(), e.to_pydatetime()), source="mt5_python")
        windows += 1
        t = e
    return TickReport(windows, inserted)


def collect_ambiguous_ticks(
    gw: MarketGateway,
    conn: sqlite3.Connection,
    settings: Settings,
    symbol: str,
    tf: Timeframe,
    start: datetime | None,
    end: datetime,
) -> TickReport:
    """Fetch ticks only for exit bars where a bar-level backtest saw both stop and target touched."""
    from mt5bot.pipeline.inference import ResearchPipeline
    from mt5bot.pipeline.jobs import candidates_for

    pipe = ResearchPipeline(conn, settings)
    feats = pipe.feature_frame(symbol, tf, start, end)
    if feats.empty:
        return TickReport(0, 0)
    cands = candidates_for(settings, feats, tf)
    bars = BarRepository(conn).load(symbol, tf, feats["time_utc"].iloc[0].to_pydatetime(), end)
    res = run_backtest(bars, cands, tf, SpecProvider(conn)(symbol), cost_model(settings, symbol), sizing(settings))
    amb = res.trades[res.trades["ambiguous"].astype(bool)] if not res.trades.empty else res.trades
    broker = _broker(gw, settings, symbol)
    repo = QuoteRepository(conn)
    inserted = 0
    for exit_time in sorted(set(amb["exit_time"])) if not amb.empty else []:
        s = pd.Timestamp(exit_time)
        inserted += repo.insert(
            symbol, gw.quotes(broker, s.to_pydatetime(), (s + tf.delta).to_pydatetime()), source="mt5_python"
        )
    return TickReport(int(amb["exit_time"].nunique()) if not amb.empty else 0, inserted, len(amb))
