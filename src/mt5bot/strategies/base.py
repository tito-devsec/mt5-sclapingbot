"""Strategy specification and candidate generation contract."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from mt5bot.core.types import Direction, Regime, Timeframe

CANDIDATE_COLUMNS = [
    "time_utc",
    "available_at",
    "strategy",
    "strategy_version",
    "direction",
    "stop_distance",
    "target_distance",
    "max_hold_bars",
    "regime",
    "eligible",
    "eligibility_reason",
]


@dataclass(frozen=True)
class StrategySpec:
    name: str
    version: str
    required_regimes: frozenset[Regime]
    entry: str
    exit: str
    invalidation: str
    stop_atr: float
    target_atr: float
    max_hold_minutes: int
    required_features: tuple[str, ...]
    cost_assumption: str
    max_spread_atr: float = 0.3

    def max_hold_bars(self, tf: Timeframe) -> int:
        return max(1, self.max_hold_minutes // tf.minutes)

    @property
    def reward_risk(self) -> float:
        return self.target_atr / self.stop_atr


class Strategy:
    spec: StrategySpec

    def long_signal(self, f: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def short_signal(self, f: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def candidates(self, features: pd.DataFrame, regimes: pd.Series, tf: Timeframe) -> pd.DataFrame:
        """Candidate proposals at bar closes as distances, anchored to the next-bar fill by the simulator."""
        spec = self.spec
        missing = features[list(spec.required_features)].isna().any(axis=1)
        out = []
        for direction, sig in (
            (Direction.LONG, self.long_signal(features)),
            (Direction.SHORT, self.short_signal(features)),
        ):
            mask = sig.fillna(False).astype(bool) & ~missing
            if not mask.any():
                continue
            f = features[mask]
            reg = regimes[mask]
            regime_ok = reg.isin([r.value for r in spec.required_regimes])
            spread_ok = f["spread_atr"] <= spec.max_spread_atr
            reason = np.where(
                ~regime_ok, "REGIME_NOT_ELIGIBLE", np.where(~spread_ok, "SPREAD_TOO_WIDE_FOR_STRATEGY", "OK")
            )
            out.append(
                pd.DataFrame(
                    {
                        "time_utc": f["time_utc"].to_numpy(),
                        "available_at": f["available_at"].to_numpy(),
                        "strategy": spec.name,
                        "strategy_version": spec.version,
                        "direction": direction.value,
                        "stop_distance": (f["atr_14"] * spec.stop_atr).to_numpy(),
                        "target_distance": (f["atr_14"] * spec.target_atr).to_numpy(),
                        "max_hold_bars": spec.max_hold_bars(tf),
                        "regime": reg.to_numpy(),
                        "eligible": (regime_ok & spread_ok).to_numpy(),
                        "eligibility_reason": reason,
                    }
                )
            )
        if not out:
            return pd.DataFrame(columns=CANDIDATE_COLUMNS)
        return pd.concat(out, ignore_index=True).sort_values(["time_utc", "direction"]).reset_index(drop=True)
