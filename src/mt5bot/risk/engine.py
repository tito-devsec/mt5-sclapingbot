"""Independent, fail-closed risk engine for research proposals; it cannot place or manage orders."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from mt5bot.config.settings import RiskSettings
from mt5bot.core.types import Direction, InstrumentSpec
from mt5bot.gateway.base import AccountSnapshot


@dataclass(frozen=True)
class RiskCandidate:
    symbol: str
    strategy: str
    direction: Direction
    stop_distance: float
    target_distance: float
    price: float
    spread_points: float
    atr: float
    vol_pctile: float
    est_cost_r: float
    model_uncertainty: float  # 0 = certain, 1 = no information
    spec: InstrumentSpec


@dataclass
class RiskContext:
    mode: str  # HISTORICAL | SIMULATED | OBSERVED_DEMO
    expected_currency: str = "USD"
    account: AccountSnapshot | None = None
    data_age_seconds: float | None = None
    data_quality_ok: bool = True
    upcoming_high_impact_events: list[str] = field(default_factory=list)
    breaking_news_flag: bool = False
    news_stale: bool = False
    daily_pnl_usd: float = 0.0
    equity_usd: float | None = None
    peak_equity_usd: float | None = None
    consecutive_losses: int = 0
    open_positions: list[tuple[str, str]] = field(default_factory=list)  # (symbol, direction)
    suspended_strategies: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class CheckResult:
    code: str
    passed: bool
    detail: str

    def as_record(self) -> dict[str, Any]:
        return {"code": self.code, "passed": self.passed, "detail": self.detail}


@dataclass
class RiskVerdict:
    approved: bool
    checks: list[CheckResult]

    @property
    def rejections(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def usd_leg_direction(symbol: str, direction: Direction) -> int:
    """+1 if the position is long USD, -1 if short USD, 0 if no USD leg."""
    base, quote = symbol[:3], symbol[3:6]
    long_ = direction is Direction.LONG
    if base == "USD":
        return 1 if long_ else -1
    if quote == "USD":
        return -1 if long_ else 1
    return 0


class RiskEngine:
    def __init__(self, settings: RiskSettings) -> None:
        self.s = settings

    def position_risk_usd(self, c: RiskCandidate) -> tuple[float, float]:
        """(lots, risk in USD) using the configured reference balance - not a verified balance."""
        from mt5bot.backtest.simulator import Sizing, _lots, usd_per_quote_unit

        sizing = Sizing(self.s.reference_balance_usd, self.s.risk_per_trade_pct, self.s.max_risk_per_trade_usd)
        lots = _lots(c.spec, c.stop_distance, c.price, sizing)
        risk = lots * c.spec.contract_size * c.stop_distance * usd_per_quote_unit(c.spec, c.price)
        return lots, risk

    def evaluate(self, c: RiskCandidate, ctx: RiskContext) -> RiskVerdict:
        checks: list[CheckResult] = []
        for fn in (
            self._emergency,
            self._account,
            self._data,
            self._spread,
            self._volatility,
            self._events,
            self._reward_risk,
            self._size,
            self._daily_loss,
            self._drawdown,
            self._streak,
            self._positions,
            self._correlation,
            self._uncertainty,
            self._suspension,
        ):
            try:
                checks.append(fn(c, ctx))
            except Exception as exc:  # fail closed on any computation problem
                checks.append(CheckResult("RISK_CHECK_INCOMPLETE", False, f"{fn.__name__}: {type(exc).__name__}"))
        return RiskVerdict(all(ch.passed for ch in checks), checks)

    # -- individual checks --------------------------------------------------
    def _emergency(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if self.s.emergency_disable:
            return CheckResult("RISK_EMERGENCY_DISABLED", False, "global emergency disable is on")
        return CheckResult("EMERGENCY_OK", True, "")

    def _account(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if ctx.mode != "OBSERVED_DEMO":
            return CheckResult("ACCOUNT_NOT_APPLICABLE", True, f"{ctx.mode} mode uses no account")
        a = ctx.account
        if a is None:
            return CheckResult("RISK_ACCOUNT_UNAVAILABLE", False, "no account snapshot")
        if not a.is_demo:
            return CheckResult("RISK_ACCOUNT_NOT_DEMO", False, f"trade mode {a.trade_mode_name}")
        if a.currency.upper() != ctx.expected_currency.upper():
            return CheckResult("RISK_ACCOUNT_CURRENCY", False, f"{a.currency} != {ctx.expected_currency}")
        return CheckResult("ACCOUNT_OK", True, "demo, currency matches")

    def _data(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        values = [c.stop_distance, c.target_distance, c.price, c.atr, c.vol_pctile, c.spread_points, c.est_cost_r]
        if not ctx.data_quality_ok or any(v is None or not math.isfinite(v) for v in values):
            return CheckResult("RISK_DATA_INCONSISTENT", False, "missing or invalid inputs")
        if c.stop_distance <= 0 or c.target_distance <= 0 or c.price <= 0 or c.atr <= 0:
            return CheckResult("RISK_DATA_INCONSISTENT", False, "non-positive distance, price or ATR")
        if ctx.data_age_seconds is not None and ctx.data_age_seconds > self.s.max_data_age_seconds:
            return CheckResult("RISK_DATA_STALE", False, f"data age {ctx.data_age_seconds:.0f}s")
        return CheckResult("DATA_OK", True, "")

    def _spread(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        limit = self.s.max_spread_points.get(c.symbol, self.s.max_spread_points.get("*", math.inf))
        if c.spread_points > limit:
            return CheckResult("RISK_SPREAD_TOO_WIDE", False, f"{c.spread_points:g} > {limit:g} points")
        ratio = c.spread_points * c.spec.point / c.atr
        if ratio > self.s.max_spread_atr_ratio:
            return CheckResult("RISK_SPREAD_ATR_TOO_HIGH", False, f"spread/ATR {ratio:.2f}")
        return CheckResult("SPREAD_OK", True, f"spread/ATR {ratio:.2f}")

    def _volatility(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if not (self.s.vol_percentile_min <= c.vol_pctile <= self.s.vol_percentile_max):
            return CheckResult("RISK_VOLATILITY_OUT_OF_BOUNDS", False, f"vol percentile {c.vol_pctile:.2f}")
        return CheckResult("VOLATILITY_OK", True, "")

    def _events(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if ctx.upcoming_high_impact_events:
            return CheckResult("RISK_EVENT_WINDOW", False, "; ".join(ctx.upcoming_high_impact_events)[:300])
        if ctx.breaking_news_flag:
            return CheckResult("RISK_BREAKING_NEWS", False, "recent relevant breaking news")
        if ctx.news_stale and self.s.block_on_stale_news:
            return CheckResult("RISK_NEWS_STATE_UNKNOWN", False, "news feeds stale or failing; news state unknown")
        return CheckResult("EVENTS_OK", True, "")

    def _reward_risk(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        target_r = c.target_distance / c.stop_distance
        net_rr = (target_r - c.est_cost_r) / (1 + c.est_cost_r)
        if net_rr < self.s.min_reward_risk:
            return CheckResult("RISK_REWARD_RISK_TOO_LOW", False, f"net reward/risk {net_rr:.2f}")
        return CheckResult("REWARD_RISK_OK", True, f"net reward/risk {net_rr:.2f}")

    def _size(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        lots, risk = self.position_risk_usd(c)
        if lots <= 0:
            return CheckResult("RISK_SIZE_BELOW_MINIMUM", False, "stop too wide for the risk budget at minimum lot")
        cap = min(self.s.max_risk_per_trade_usd, self.s.reference_balance_usd * self.s.risk_per_trade_pct / 100)
        if risk > cap + 1e-9:
            return CheckResult("RISK_PER_TRADE_TOO_HIGH", False, f"{risk:.2f} USD > {cap:.2f} USD")
        return CheckResult("SIZE_OK", True, f"{lots:g} lots, {risk:.2f} USD at risk (reference balance)")

    def _daily_loss(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        limit = self.s.reference_balance_usd * self.s.max_daily_loss_pct / 100
        if -ctx.daily_pnl_usd >= limit:
            return CheckResult("RISK_DAILY_LOSS_LIMIT", False, f"daily loss {-ctx.daily_pnl_usd:.2f} >= {limit:.2f}")
        return CheckResult("DAILY_LOSS_OK", True, "")

    def _drawdown(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if ctx.equity_usd is None or ctx.peak_equity_usd is None:
            return CheckResult("DRAWDOWN_NOT_TRACKED", True, "no equity curve in this mode")
        dd = ctx.peak_equity_usd - ctx.equity_usd
        limit = self.s.reference_balance_usd * self.s.max_drawdown_pct / 100
        if dd >= limit:
            return CheckResult("RISK_MAX_DRAWDOWN", False, f"drawdown {dd:.2f} >= {limit:.2f}")
        return CheckResult("DRAWDOWN_OK", True, "")

    def _streak(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if ctx.consecutive_losses >= self.s.max_consecutive_losses:
            return CheckResult("RISK_CONSECUTIVE_LOSSES", False, f"{ctx.consecutive_losses} losses in a row")
        return CheckResult("STREAK_OK", True, "")

    def _positions(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if len(ctx.open_positions) >= self.s.max_open_positions:
            return CheckResult("RISK_MAX_OPEN_POSITIONS", False, f"{len(ctx.open_positions)} open")
        return CheckResult("POSITIONS_OK", True, "")

    def _correlation(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        leg = usd_leg_direction(c.symbol, c.direction)
        if leg == 0:
            return CheckResult("CORRELATION_OK", True, "no USD leg")
        same = sum(1 for sym, d in ctx.open_positions if usd_leg_direction(sym, Direction(d)) == leg)
        if same >= self.s.max_usd_leg_exposure:
            return CheckResult("RISK_CORRELATED_EXPOSURE", False, f"{same} open positions with the same USD exposure")
        return CheckResult("CORRELATION_OK", True, "")

    def _uncertainty(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if c.model_uncertainty > self.s.max_model_uncertainty:
            return CheckResult("RISK_MODEL_UNCERTAINTY", False, f"uncertainty {c.model_uncertainty:.2f}")
        return CheckResult("UNCERTAINTY_OK", True, "")

    def _suspension(self, c: RiskCandidate, ctx: RiskContext) -> CheckResult:
        if c.strategy in ctx.suspended_strategies:
            return CheckResult("RISK_STRATEGY_SUSPENDED", False, c.strategy)
        return CheckResult("STRATEGY_ACTIVE", True, "")
