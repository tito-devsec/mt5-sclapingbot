"""Agent technique library: entry rules crossed with exit profiles; research candidates with no validated edge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from mt5bot.core.types import Regime, Timeframe
from mt5bot.strategies.base import CANDIDATE_COLUMNS

TECHNIQUE_VERSION = "tech-1.0.0"
Signal = Callable[[pd.DataFrame], tuple[pd.Series, pd.Series]]

TRENDING = frozenset({Regime.TREND_UP, Regime.TREND_DOWN})
QUIET = frozenset({Regime.RANGE, Regime.LOW_VOL})
ANY_KNOWN = frozenset(set(Regime) - {Regime.UNCERTAIN})


@dataclass(frozen=True)
class EntryRule:
    name: str
    description: str
    regimes: frozenset[Regime]
    requires: tuple[str, ...]
    signal: Signal
    max_spread_atr: float = 0.3


@dataclass(frozen=True)
class ExitProfile:
    stop_atr: float
    target_atr: float
    hold_minutes: int

    @property
    def tag(self) -> str:
        return f"s{self.stop_atr:g}_t{self.target_atr:g}_h{self.hold_minutes}"


def _cross_up(x: pd.Series, level: float = 0.0) -> pd.Series:
    return (x > level) & (x.shift(1) <= level)


def _cross_down(x: pd.Series, level: float = 0.0) -> pd.Series:
    return (x < level) & (x.shift(1) >= level)


def _trend_pullback(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    ok = f["htf_alignment"] >= 0
    return (
        (f["ema_20_50_atr"] >= 0.5) & _cross_up(f["close_ema20_atr"]) & ok,
        (f["ema_20_50_atr"] <= -0.5) & _cross_down(f["close_ema20_atr"]) & ok,
    )


def _donchian_breakout(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    exp = f["range_expansion"] >= 1.3
    return _cross_up(f["breakout_up_20"]) & exp, _cross_up(f["breakout_dn_20"]) & exp


def _rsi_reversion(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return (f["rsi_14"] < 25) & (f["close_ema20_atr"] < -1.5), (f["rsi_14"] > 75) & (f["close_ema20_atr"] > 1.5)


def _sr_bounce(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    bull = (f["cs_pin_bull"] == 1) | (f["cs_bull_engulf"] == 1)
    bear = (f["cs_pin_bear"] == 1) | (f["cs_bear_engulf"] == 1)
    return f["sr_sup_dist_atr"].between(-0.2, 0.6) & bull, f["sr_res_dist_atr"].between(-0.2, 0.6) & bear


def _engulfing_trend(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return (f["cs_bull_engulf"] == 1) & (f["struct_trend"] > 0), (f["cs_bear_engulf"] == 1) & (f["struct_trend"] < 0)


def _pinbar_extreme(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return (f["cs_pin_bull"] == 1) & (f["dist_low_20"] < 0.5), (f["cs_pin_bear"] == 1) & (f["dist_high_20"] < 0.5)


def _session_momentum(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    live = (f["session_open_30m"] == 1) & (f["range_expansion"] >= 1.2)
    return live & (f["mom_10"] > 1.0), live & (f["mom_10"] < -1.0)


def _trendline_break(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return (
        _cross_up(f["tl_dev_atr"], 1.0) & (f["tl_slope_atr"] <= 0),
        _cross_down(f["tl_dev_atr"], -1.0) & (f["tl_slope_atr"] >= 0),
    )


def _inside_bar_break(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    was_inside = f["cs_inside"].shift(1) == 1
    return was_inside & (f["body_ratio"] > 0.5) & (f["ret_1"] > 0), was_inside & (f["body_ratio"] < -0.5) & (
        f["ret_1"] < 0
    )


def _macd_momentum(f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    strong = f["adx_14"] >= 20
    return _cross_up(f["macd_hist_atr"]) & strong, _cross_down(f["macd_hist_atr"]) & strong


ENTRY_RULES: tuple[EntryRule, ...] = (
    EntryRule(
        "trend_pullback",
        "close crosses back over EMA20 in an EMA-aligned trend",
        TRENDING,
        ("ema_20_50_atr", "close_ema20_atr", "htf_alignment"),
        _trend_pullback,
    ),
    EntryRule(
        "donchian_breakout",
        "first close beyond the previous 20-bar range with range expansion",
        frozenset({Regime.BREAKOUT_EXPANSION, Regime.RANGE, Regime.LOW_VOL}),
        ("breakout_up_20", "breakout_dn_20", "range_expansion"),
        _donchian_breakout,
    ),
    EntryRule(
        "rsi_reversion",
        "RSI extreme far from EMA20 in a quiet market",
        QUIET,
        ("rsi_14", "close_ema20_atr"),
        _rsi_reversion,
    ),
    EntryRule(
        "sr_bounce",
        "pin bar or engulfing at the last confirmed swing support or resistance",
        ANY_KNOWN,
        ("sr_sup_dist_atr", "sr_res_dist_atr", "cs_pin_bull", "cs_bull_engulf"),
        _sr_bounce,
    ),
    EntryRule(
        "engulfing_trend",
        "engulfing bar in the direction of the swing structure",
        ANY_KNOWN,
        ("cs_bull_engulf", "cs_bear_engulf", "struct_trend"),
        _engulfing_trend,
    ),
    EntryRule(
        "pinbar_extreme",
        "pin bar at a 20-bar extreme",
        ANY_KNOWN,
        ("cs_pin_bull", "cs_pin_bear", "dist_low_20", "dist_high_20"),
        _pinbar_extreme,
    ),
    EntryRule(
        "session_momentum",
        "momentum with range expansion in the first 30 minutes of a session",
        ANY_KNOWN,
        ("session_open_30m", "range_expansion", "mom_10"),
        _session_momentum,
    ),
    EntryRule(
        "trendline_break",
        "close breaks one ATR beyond the 30-bar trendline against its slope",
        ANY_KNOWN,
        ("tl_dev_atr", "tl_slope_atr"),
        _trendline_break,
    ),
    EntryRule(
        "inside_bar_break",
        "strong bar out of an inside bar",
        ANY_KNOWN,
        ("cs_inside", "body_ratio", "ret_1"),
        _inside_bar_break,
    ),
    EntryRule(
        "macd_momentum",
        "MACD histogram crosses zero with ADX >= 20",
        TRENDING | {Regime.BREAKOUT_EXPANSION},
        ("macd_hist_atr", "adx_14"),
        _macd_momentum,
    ),
)
EXIT_SHAPES: tuple[tuple[float, float], ...] = ((1.0, 1.5), (1.0, 2.0), (1.0, 3.0), (1.5, 2.25), (1.5, 3.0), (2.0, 3.0))


def technique_name(rule: str, profile: ExitProfile) -> str:
    return f"{rule}|{profile.tag}"


def entry_of(technique: str) -> str:
    return technique.split("|", 1)[0]


class TechniqueSet:
    """Generates candidates for every entry rule and exit profile; each entry signal is computed once."""

    def __init__(self, hold_minutes: list[int]) -> None:
        self.profiles = [ExitProfile(s, t, h) for s, t in EXIT_SHAPES for h in hold_minutes]
        self.rules = ENTRY_RULES

    @property
    def names(self) -> list[str]:
        return [technique_name(r.name, p) for r in self.rules for p in self.profiles]

    def candidates(self, features: pd.DataFrame, regimes: pd.Series, tf: Timeframe) -> pd.DataFrame:
        frames = []
        base_missing = features[["atr_14", "spread_atr"]].isna().any(axis=1)
        for rule in self.rules:
            missing = base_missing | features[list(rule.requires)].isna().any(axis=1)
            long_sig, short_sig = rule.signal(features)
            allowed = [r.value for r in rule.regimes]
            for direction, sig in (("LONG", long_sig), ("SHORT", short_sig)):
                mask = sig.fillna(False).astype(bool) & ~missing
                if not mask.any():
                    continue
                f, reg = features[mask], regimes[mask]
                regime_ok = reg.isin(allowed).to_numpy()
                spread_ok = (f["spread_atr"] <= rule.max_spread_atr).to_numpy()
                reason = np.where(
                    ~regime_ok, "REGIME_NOT_ELIGIBLE", np.where(~spread_ok, "SPREAD_TOO_WIDE_FOR_STRATEGY", "OK")
                )
                atr = f["atr_14"].to_numpy()
                for p in self.profiles:
                    frames.append(
                        pd.DataFrame(
                            {
                                "time_utc": f["time_utc"].to_numpy(),
                                "available_at": f["available_at"].to_numpy(),
                                "strategy": technique_name(rule.name, p),
                                "strategy_version": TECHNIQUE_VERSION,
                                "direction": direction,
                                "stop_distance": atr * p.stop_atr,
                                "target_distance": atr * p.target_atr,
                                "max_hold_bars": max(1, p.hold_minutes // tf.minutes),
                                "regime": reg.to_numpy(),
                                "eligible": regime_ok & spread_ok,
                                "eligibility_reason": reason,
                            }
                        )
                    )
        if not frames:
            return pd.DataFrame(columns=CANDIDATE_COLUMNS)
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["time_utc", "strategy", "direction"], kind="stable").reset_index(drop=True)
