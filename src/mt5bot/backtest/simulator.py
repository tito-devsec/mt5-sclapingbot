"""Cost-aware single-trade simulator on bid-price bars; fill rules are documented in docs/BACKTESTING.md."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from mt5bot.core.types import Direction, InstrumentSpec, Timeframe


@dataclass(frozen=True)
class CostModel:
    commission_per_lot_per_side_usd: float = 0.0
    slippage_points: float = 0.0
    swap_long_points_per_night: float = 0.0
    swap_short_points_per_night: float = 0.0
    multiplier: float = 1.0  # sensitivity scaling of spread, slippage and commission

    def scaled(self, m: float) -> CostModel:
        return CostModel(
            self.commission_per_lot_per_side_usd,
            self.slippage_points,
            self.swap_long_points_per_night,
            self.swap_short_points_per_night,
            m,
        )


@dataclass(frozen=True)
class Sizing:
    reference_balance_usd: float = 1000.0
    risk_per_trade_pct: float = 0.5
    max_risk_per_trade_usd: float = 10.0

    @property
    def risk_budget_usd(self) -> float:
        return min(self.reference_balance_usd * self.risk_per_trade_pct / 100.0, self.max_risk_per_trade_usd)


@dataclass
class TradeResult:
    status: str  # FILLED | NO_NEXT_BAR | SIZE_BELOW_MINIMUM | INSUFFICIENT_DATA
    strategy: str = ""
    direction: str = ""
    signal_time: pd.Timestamp | None = None
    entry_time: pd.Timestamp | None = None
    entry_price: float = math.nan
    exit_time: pd.Timestamp | None = None
    exit_price: float = math.nan
    exit_reason: str = ""
    lots: float = 0.0
    gross_pnl_usd: float = 0.0
    cost_usd: float = 0.0
    net_pnl_usd: float = 0.0
    risk_usd: float = 0.0
    gross_r: float = math.nan
    net_r: float = math.nan
    cost_r: float = math.nan
    mae_r: float = math.nan
    mfe_r: float = math.nan
    bars_held: int = 0
    label_available_at: pd.Timestamp | None = None
    regime: str | None = None
    ambiguous: bool = False
    resolution: str = ""


# Called for a bar that touched both levels: (bar_start_utc, bar_end_utc, stop, target) -> "STOP" | "TARGET" | None.
AmbiguityResolver = Callable[[pd.Timestamp, pd.Timestamp, float, float], str | None]


def usd_per_quote_unit(spec: InstrumentSpec, price: float) -> float:
    """USD value of one unit of the quote currency."""
    if spec.quote == "USD":
        return 1.0
    if spec.base == "USD":
        return 1.0 / price
    raise NotImplementedError(f"{spec.symbol}: conversion for non-USD crosses needs an FX rate series")


def _lots(spec: InstrumentSpec, stop_distance: float, price: float, sizing: Sizing) -> float:
    risk_per_lot = stop_distance * spec.contract_size * usd_per_quote_unit(spec, price)
    if risk_per_lot <= 0:
        return 0.0
    raw = sizing.risk_budget_usd / risk_per_lot
    steps = math.floor(raw / spec.volume_step + 1e-9)
    lots = min(steps * spec.volume_step, spec.volume_max)
    return round(lots, 8) if lots >= spec.volume_min else 0.0


def _rollovers(start: pd.Timestamp, end: pd.Timestamp) -> int:
    if end <= start:
        return 0
    first = start.normalize() + pd.Timedelta(hours=21)
    if first <= start:
        first += pd.Timedelta(days=1)
    if first > end:
        return 0
    return int((end - first) // pd.Timedelta(days=1)) + 1


class BarArrays:
    """Numpy view of a bar frame for fast repeated simulation."""

    def __init__(self, bars: pd.DataFrame, tf: Timeframe) -> None:
        b = bars.sort_values("time_utc").reset_index(drop=True)
        self.time = pd.DatetimeIndex(b["time_utc"])
        self.o = b["open"].to_numpy(float)
        self.h = b["high"].to_numpy(float)
        self.l = b["low"].to_numpy(float)
        self.c = b["close"].to_numpy(float)
        self.spread = b["spread_points"].to_numpy(float)
        self.tf = tf
        self._pos = {t: i for i, t in enumerate(self.time)}

    def index_of(self, t: pd.Timestamp) -> int | None:
        return self._pos.get(pd.Timestamp(t))


def simulate_trade(
    bars: BarArrays,
    signal_idx: int,
    direction: Direction,
    stop_distance: float,
    target_distance: float,
    max_hold_bars: int,
    spec: InstrumentSpec,
    costs: CostModel,
    sizing: Sizing,
    strategy: str = "",
    regime: str | None = None,
    resolver: AmbiguityResolver | None = None,
) -> TradeResult:
    tf = bars.tf
    res = TradeResult(
        status="FILLED", strategy=strategy, direction=direction.value, signal_time=bars.time[signal_idx], regime=regime
    )
    e = signal_idx + 1
    if e >= len(bars.time) or bars.time[e] != bars.time[signal_idx] + tf.delta:
        res.status = "NO_NEXT_BAR"
        return res
    last = e + max_hold_bars - 1
    if last >= len(bars.time):
        res.status = "INSUFFICIENT_DATA"
        return res
    pt, m = spec.point, costs.multiplier
    slip = costs.slippage_points * pt * m
    sgn = direction.sign
    spread = bars.spread * pt * m

    entry = bars.o[e] + spread[e] + slip if direction is Direction.LONG else bars.o[e] - slip
    stop = entry - sgn * stop_distance
    target = entry + sgn * target_distance
    lots = _lots(spec, stop_distance, entry, sizing)
    if lots <= 0:
        res.status = "SIZE_BELOW_MINIMUM"
        return res

    exit_idx, exit_reason, exit_fill = last, "TIME", math.nan
    mae = mfe = 0.0
    for j in range(e, last + 1):
        if direction is Direction.LONG:
            lo, hi, op = bars.l[j], bars.h[j], bars.o[j]
        else:  # shorts close at the ask
            lo, hi, op = bars.l[j] + spread[j], bars.h[j] + spread[j], bars.o[j] + spread[j]
        adverse = (entry - lo) if sgn > 0 else (hi - entry)
        favourable = (hi - entry) if sgn > 0 else (entry - lo)
        mae, mfe = max(mae, adverse), max(mfe, favourable)
        gap_stop = (op <= stop) if sgn > 0 else (op >= stop)
        hit_stop = (lo <= stop) if sgn > 0 else (hi >= stop)
        hit_target = (hi >= target) if sgn > 0 else (lo <= target)
        if j > e and gap_stop:
            exit_idx, exit_reason, exit_fill = j, "STOP_GAP", op - sgn * slip
            break
        if hit_stop and hit_target:  # intrabar ambiguity: ticks decide if available, else the stop wins
            res.ambiguous = True
            verdict = resolver(bars.time[j], bars.time[j] + tf.delta, stop, target) if resolver else None
            if verdict == "TARGET":
                res.resolution = "TICKS_TARGET"
                exit_idx, exit_reason, exit_fill = j, "TARGET", target - sgn * slip
            else:
                res.resolution = "TICKS_STOP" if verdict == "STOP" else "ASSUMED_STOP"
                exit_idx, exit_reason, exit_fill = j, "STOP", stop - sgn * slip
            break
        if hit_stop:
            exit_idx, exit_reason, exit_fill = j, "STOP", stop - sgn * slip
            break
        if hit_target:
            exit_idx, exit_reason, exit_fill = j, "TARGET", target - sgn * slip
            break
    if exit_reason == "TIME":
        close_px = bars.c[last] if sgn > 0 else bars.c[last] + spread[last]
        exit_fill = close_px - sgn * slip

    conv_exit = usd_per_quote_unit(spec, exit_fill)
    units = spec.contract_size * lots
    net_move = (exit_fill - entry) * sgn
    # Gross = bid-to-bid price move without spread or slippage.
    entry_bid = bars.o[e]
    exit_bid = exit_fill + sgn * slip - (0 if sgn > 0 else spread[exit_idx])
    gross_move = (exit_bid - entry_bid) * sgn
    commission = 2 * costs.commission_per_lot_per_side_usd * m * lots
    nights = _rollovers(bars.time[e], bars.time[exit_idx] + tf.delta)
    swap_pts = costs.swap_long_points_per_night if sgn > 0 else costs.swap_short_points_per_night
    swap_usd = nights * swap_pts * pt * units * conv_exit  # positive swap points = credit

    gross_usd = gross_move * units * conv_exit
    net_usd = net_move * units * conv_exit - commission + swap_usd
    risk_usd = stop_distance * units * usd_per_quote_unit(spec, entry)

    res.entry_time, res.entry_price = bars.time[e], float(entry)
    res.exit_time, res.exit_price, res.exit_reason = bars.time[exit_idx], float(exit_fill), exit_reason
    res.lots = lots
    res.gross_pnl_usd, res.net_pnl_usd = float(gross_usd), float(net_usd)
    res.cost_usd = float(gross_usd - net_usd)
    res.risk_usd = float(risk_usd)
    res.gross_r, res.net_r = float(gross_usd / risk_usd), float(net_usd / risk_usd)
    res.cost_r = float(res.cost_usd / risk_usd)
    res.mae_r, res.mfe_r = float(mae / stop_distance), float(mfe / stop_distance)
    res.bars_held = exit_idx - e + 1
    res.label_available_at = bars.time[exit_idx] + tf.delta
    return res


def results_frame(results: list[TradeResult]) -> pd.DataFrame:
    rows = [r.__dict__ for r in results if r.status == "FILLED"]
    cols = list(TradeResult("FILLED").__dict__)
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def estimated_cost_r(
    spec: InstrumentSpec, spread_points: float, stop_distance: float, costs: CostModel, price: float
) -> float:
    """Pre-trade cost estimate in R: spread + 2x slippage + round-turn commission, per unit of stop distance."""
    if stop_distance <= 0:
        return float("inf")
    price_cost = (spread_points + 2 * costs.slippage_points) * spec.point * costs.multiplier
    commission_price = 0.0
    if costs.commission_per_lot_per_side_usd:
        usd_per_unit = 2 * costs.commission_per_lot_per_side_usd * costs.multiplier / spec.contract_size
        commission_price = usd_per_unit / usd_per_quote_unit(spec, price)
    return float((price_cost + commission_price) / stop_distance)
