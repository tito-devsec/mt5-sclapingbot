"""Chronological backtest over strategy candidates; no look-ahead and one open position per strategy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from mt5bot.backtest.simulator import (
    AmbiguityResolver,
    BarArrays,
    CostModel,
    Sizing,
    TradeResult,
    results_frame,
    simulate_trade,
)
from mt5bot.core.types import Direction, InstrumentSpec, Timeframe

# chooser(candidates_at_one_bar) -> the subset to take (rows of the same frame)
Chooser = Callable[[pd.DataFrame], pd.DataFrame]


def take_all_eligible(c: pd.DataFrame) -> pd.DataFrame:
    return c[c["eligible"].astype(bool)]


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    skipped: dict[str, int] = field(default_factory=dict)
    candidates: int = 0


def run_backtest(
    bars: pd.DataFrame,
    candidates: pd.DataFrame,
    tf: Timeframe,
    spec: InstrumentSpec,
    costs: CostModel,
    sizing: Sizing,
    chooser: Chooser = take_all_eligible,
    max_concurrent: int | None = None,
    resolver_for: Callable[[Direction], AmbiguityResolver] | None = None,
) -> BacktestResult:
    arrays = BarArrays(bars, tf)
    results: list[TradeResult] = []
    skipped: dict[str, int] = {}
    busy_until: dict[str, pd.Timestamp] = {}  # strategy -> exit time of its open position
    open_exits: list[pd.Timestamp] = []
    if candidates.empty:
        return BacktestResult(results_frame([]), skipped, 0)
    for t, group in candidates.sort_values("available_at").groupby("time_utc", sort=True):
        chosen = chooser(group)
        for row in chosen.itertuples(index=False):
            if row.strategy in busy_until and busy_until[row.strategy] >= t:
                skipped["POSITION_OPEN"] = skipped.get("POSITION_OPEN", 0) + 1
                continue
            open_exits = [x for x in open_exits if x >= t]
            if max_concurrent is not None and len(open_exits) >= max_concurrent:
                skipped["MAX_CONCURRENT"] = skipped.get("MAX_CONCURRENT", 0) + 1
                continue
            i = arrays.index_of(t)
            if i is None:
                skipped["BAR_NOT_FOUND"] = skipped.get("BAR_NOT_FOUND", 0) + 1
                continue
            r = simulate_trade(
                arrays,
                i,
                Direction(row.direction),
                float(row.stop_distance),
                float(row.target_distance),
                int(row.max_hold_bars),
                spec,
                costs,
                sizing,
                strategy=row.strategy,
                regime=getattr(row, "regime", None),
                resolver=resolver_for(Direction(row.direction)) if resolver_for else None,
            )
            if r.status != "FILLED":
                skipped[r.status] = skipped.get(r.status, 0) + 1
                continue
            results.append(r)
            assert r.exit_time is not None
            busy_until[row.strategy] = r.exit_time
            open_exits.append(r.exit_time)
    return BacktestResult(results_frame(results), skipped, len(candidates))
