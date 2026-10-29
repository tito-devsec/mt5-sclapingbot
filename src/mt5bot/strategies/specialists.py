"""Versioned strategy specialists; research candidates with no validated edge."""

from __future__ import annotations

import pandas as pd

from mt5bot.core.types import Regime
from mt5bot.strategies.base import Strategy, StrategySpec


class TrendFollowing(Strategy):
    def __init__(self, max_hold_minutes: int = 60) -> None:
        self.spec = StrategySpec(
            name="trend",
            version="trend-1.0.0",
            required_regimes=frozenset({Regime.TREND_UP, Regime.TREND_DOWN}),
            entry="in an EMA20/EMA50-aligned trend, enter when close crosses back over EMA20 after a pullback "
            "and the higher timeframe is not opposed",
            exit="stop-loss, take-profit, or maximum holding time",
            invalidation="price moves 1.5 ATR against the entry (stop)",
            stop_atr=1.5,
            target_atr=2.25,
            max_hold_minutes=max_hold_minutes,
            required_features=("ema_20_50_atr", "close_ema20_atr", "atr_14", "spread_atr", "htf_alignment"),
            cost_assumption="spread at entry and exit plus configured slippage and commission",
        )

    def long_signal(self, f: pd.DataFrame) -> pd.Series:
        cross_up = (f["close_ema20_atr"] > 0) & (f["close_ema20_atr"].shift(1) <= 0)
        return (f["ema_20_50_atr"] >= 0.5) & cross_up & (f["htf_alignment"] >= 0)

    def short_signal(self, f: pd.DataFrame) -> pd.Series:
        cross_dn = (f["close_ema20_atr"] < 0) & (f["close_ema20_atr"].shift(1) >= 0)
        return (f["ema_20_50_atr"] <= -0.5) & cross_dn & (f["htf_alignment"] >= 0)


class Breakout(Strategy):
    def __init__(self, max_hold_minutes: int = 60) -> None:
        self.spec = StrategySpec(
            name="breakout",
            version="breakout-1.0.0",
            required_regimes=frozenset({Regime.BREAKOUT_EXPANSION, Regime.RANGE, Regime.LOW_VOL}),
            entry="close beyond the previous 20-bar high/low with range expansion >= 1.3",
            exit="stop-loss, take-profit, or maximum holding time",
            invalidation="price returns 1 ATR back inside the broken range (stop)",
            stop_atr=1.0,
            target_atr=2.0,
            max_hold_minutes=max_hold_minutes,
            required_features=("breakout_up_20", "breakout_dn_20", "range_expansion", "atr_14", "spread_atr"),
            cost_assumption="spread at entry and exit plus configured slippage and commission",
        )

    def long_signal(self, f: pd.DataFrame) -> pd.Series:
        first = (f["breakout_up_20"] > 0) & (f["breakout_up_20"].shift(1) <= 0)
        return first & (f["range_expansion"] >= 1.3)

    def short_signal(self, f: pd.DataFrame) -> pd.Series:
        first = (f["breakout_dn_20"] > 0) & (f["breakout_dn_20"].shift(1) <= 0)
        return first & (f["range_expansion"] >= 1.3)


class MeanReversion(Strategy):
    """Optional and disabled by default until independently validated."""

    def __init__(self, max_hold_minutes: int = 60) -> None:
        self.spec = StrategySpec(
            name="mean_reversion",
            version="mean_reversion-1.0.0",
            required_regimes=frozenset({Regime.RANGE, Regime.LOW_VOL}),
            entry="RSI14 extreme (<25 / >75) with close > 1.5 ATR away from EMA20",
            exit="stop-loss, take-profit, or maximum holding time",
            invalidation="price extends a further 1.25 ATR (stop)",
            stop_atr=1.25,
            target_atr=1.5,
            max_hold_minutes=max_hold_minutes,
            required_features=("rsi_14", "close_ema20_atr", "atr_14", "spread_atr"),
            cost_assumption="spread at entry and exit plus configured slippage and commission",
        )

    def long_signal(self, f: pd.DataFrame) -> pd.Series:
        return (f["rsi_14"] < 25) & (f["close_ema20_atr"] < -1.5)

    def short_signal(self, f: pd.DataFrame) -> pd.Series:
        return (f["rsi_14"] > 75) & (f["close_ema20_atr"] > 1.5)


REGISTRY = {"trend": TrendFollowing, "breakout": Breakout, "mean_reversion": MeanReversion}


def build_strategies(names: list[str], max_hold_minutes: int) -> list[Strategy]:
    return [REGISTRY[n](max_hold_minutes) for n in names]
