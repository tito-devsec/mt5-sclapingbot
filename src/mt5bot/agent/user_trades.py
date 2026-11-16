"""Opt-in, read-only study of the user's own closed MT5 trades; nothing is copied, changed or shared."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from mt5bot.agent.core import TradingAgent
from mt5bot.config.settings import Settings
from mt5bot.core.types import Timeframe
from mt5bot.gateway.base import MarketGateway
from mt5bot.regime.rules import classify_rules
from mt5bot.selection.reward import reward as reward_fn
from mt5bot.storage.repositories import now_iso

if TYPE_CHECKING:
    from mt5bot.pipeline.inference import ResearchPipeline

DEAL_BUY, DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 0, 1


@dataclass
class UserTradeReport:
    enabled: bool
    trades_found: int = 0
    trades_stored: int = 0
    episodes_added: int = 0
    skipped_no_bars: int = 0


def pair_deals(deals: pd.DataFrame) -> pd.DataFrame:
    """Join each position's opening and closing deals into one closed trade (positions still open are ignored)."""
    rows = []
    for pid, g in deals.sort_values("time_utc").groupby("position_id"):
        opens, closes = g[g["entry"] == DEAL_ENTRY_IN], g[g["entry"] == DEAL_ENTRY_OUT]
        if opens.empty or closes.empty or closes["volume"].sum() + 1e-9 < opens["volume"].sum():
            continue
        first = opens.iloc[0]
        rows.append(
            {
                "ticket": int(pid),
                "broker_symbol": first["symbol"],
                "direction": "LONG" if int(first["type"]) == DEAL_BUY else "SHORT",
                "entry_time": first["time_utc"],
                "exit_time": closes["time_utc"].iloc[-1],
                "entry_price": float((opens["price"] * opens["volume"]).sum() / opens["volume"].sum()),
                "exit_price": float((closes["price"] * closes["volume"]).sum() / closes["volume"].sum()),
                "volume": float(opens["volume"].sum()),
                "profit_usd": float(g["profit"].sum()),
            }
        )
    return pd.DataFrame(rows)


def import_user_trades(
    gw: MarketGateway,
    conn: sqlite3.Connection,
    settings: Settings,
    agent: TradingAgent,
    pipeline: ResearchPipeline,
    now: datetime,
) -> UserTradeReport:
    """Store the user's closed trades and add them to episodic memory with the market state at entry."""
    if not settings.agent.observe_user_trades:
        return UserTradeReport(enabled=False)
    rep = UserTradeReport(enabled=True)
    start = pd.Timestamp(now) - pd.Timedelta(days=settings.agent.user_trade_lookback_days)
    trades = pair_deals(gw.deals(start.to_pydatetime(), now))
    rep.trades_found = len(trades)
    if trades.empty:
        return rep
    canonical = {
        r["broker_symbol"]: r["symbol"] for r in conn.execute("SELECT symbol, broker_symbol FROM instrument_specs")
    }
    tf = Timeframe(settings.instruments.timeframes[0])
    for t in trades.itertuples(index=False):
        symbol = canonical.get(t.broker_symbol)
        if symbol is None or symbol not in settings.instruments.symbols:
            continue
        cur = conn.execute(
            "INSERT INTO user_trades(ticket, symbol, direction, entry_time, exit_time, entry_price, exit_price, "
            "volume, profit_usd, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticket) DO NOTHING",
            (
                t.ticket,
                symbol,
                t.direction,
                t.entry_time.isoformat(),
                t.exit_time.isoformat(),
                t.entry_price,
                t.exit_price,
                t.volume,
                t.profit_usd,
                now_iso(),
            ),
        )
        rep.trades_stored += cur.rowcount
        feats = pipeline.feature_frame(
            symbol, tf, (t.entry_time - tf.delta * 5).to_pydatetime(), t.entry_time.to_pydatetime()
        )
        if feats.empty or pd.isna(feats["atr_14"].iloc[-1]):
            rep.skipped_no_bars += 1
            continue
        row = feats.iloc[[-1]]
        regime = str(classify_rules(row)["regime"].iloc[0])
        values = {k: (None if pd.isna(v) else v) for k, v in row.iloc[0].drop(["time_utc", "available_at"]).items()}
        sign = 1 if t.direction == "LONG" else -1
        move_atr = sign * (t.exit_price - t.entry_price) / float(row["atr_14"].iloc[0])
        added = agent.add_user_episode(
            ticket=int(t.ticket),
            symbol=symbol,
            timeframe=tf.value,
            direction=t.direction,
            regime=regime,
            feats=values,
            decided_at=pd.Timestamp(row["time_utc"].iloc[0]),
            available=t.exit_time,
            move_atr=move_atr,
            reward=reward_fn(move_atr, 0.0, settings.selection),
        )
        rep.episodes_added += int(added)
    return rep
