"""Shared enums and small value types used across the system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class Timeframe(StrEnum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"

    @property
    def minutes(self) -> int:
        return {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[self.value]

    @property
    def delta(self) -> timedelta:
        return timedelta(minutes=self.minutes)

    @property
    def mt5_constant(self) -> str:
        """Name of the matching constant in the MetaTrader5 package."""
        return f"TIMEFRAME_{self.value}"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    BREAKOUT_EXPANSION = "BREAKOUT_EXPANSION"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    UNCERTAIN = "UNCERTAIN"


class DecisionStatus(StrEnum):
    """Outcome of the research pipeline. There is deliberately no 'EXECUTE' value."""

    RECOMMEND = "RECOMMEND"  # research recommendation only; never routed to a broker
    NO_TRADE = "NO_TRADE"
    UNCERTAIN = "UNCERTAIN"
    REJECTED = "REJECTED"  # rejected by the risk engine


class ResultProvenance(StrEnum):
    SIMULATED = "SIMULATED"
    HISTORICAL = "HISTORICAL"
    OBSERVED_DEMO = "OBSERVED_DEMO"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime; naive values are rejected."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime is not allowed; pass a timezone-aware value")
    return ts.astimezone(UTC)


@dataclass(frozen=True)
class InstrumentSpec:
    """Contract metadata for cost and P&L arithmetic; live values must come from broker symbol_info."""

    symbol: str
    base: str
    quote: str
    point: float
    digits: int
    contract_size: float
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 100.0


def default_spec(symbol: str) -> InstrumentSpec:
    """Typical retail specs, for offline fixtures only. Not broker-verified."""
    base = symbol[:3].upper()
    quote = symbol[3:6].upper()
    if base == "XAU":
        return InstrumentSpec(symbol, "XAU", quote, 0.01, 2, 100.0)
    if quote == "JPY":
        return InstrumentSpec(symbol, base, quote, 0.001, 3, 100_000.0)
    return InstrumentSpec(symbol, base, quote, 0.00001, 5, 100_000.0)
