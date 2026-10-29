import math

import pandas as pd
import pytest

from mt5bot.backtest.engine import run_backtest
from mt5bot.backtest.metrics import max_drawdown, trade_metrics
from mt5bot.backtest.simulator import BarArrays, CostModel, Sizing, estimated_cost_r, simulate_trade
from mt5bot.core.types import Direction, InstrumentSpec, Timeframe, default_spec
from mt5bot.labeling.meta import label_candidates

EURUSD = InstrumentSpec("EURUSD", "EUR", "USD", 0.00001, 5, 100_000.0)
USDJPY = InstrumentSpec("USDJPY", "USD", "JPY", 0.001, 3, 100_000.0)
T0 = pd.Timestamp("2024-03-05 10:00", tz="UTC")
COSTS = CostModel(commission_per_lot_per_side_usd=3.5, slippage_points=2)
SIZING = Sizing(1000, 0.5, 10)  # risk budget 5 USD


def make_bars(rows, start=T0, tf=Timeframe.M1, spread=10):
    """rows: list of (open, high, low, close)."""
    return pd.DataFrame(
        {
            "time_utc": [start + i * tf.delta for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "tick_volume": 100,
            "spread_points": spread,
            "real_volume": 0,
        }
    )


def sim(bars, direction, stop=0.0010, target=0.0020, hold=5, spec=EURUSD, costs=COSTS, sizing=SIZING):
    return simulate_trade(BarArrays(bars, Timeframe.M1), 0, direction, stop, target, hold, spec, costs, sizing)


def test_hand_calculated_long_target():
    bars = make_bars(
        [
            (1.1, 1.1, 1.1, 1.1),
            (1.10000, 1.10100, 1.09950, 1.10050),
            (1.10050, 1.10250, 1.10000, 1.10200),
            (1.102, 1.102, 1.102, 1.102),
            (1.102, 1.102, 1.102, 1.102),
            (1.102, 1.102, 1.102, 1.102),
        ]
    )
    r = sim(bars, Direction.LONG)
    assert r.status == "FILLED" and r.exit_reason == "TARGET"
    assert r.lots == pytest.approx(0.05)
    assert r.entry_price == pytest.approx(1.10012)  # open + spread + slippage
    assert r.exit_price == pytest.approx(1.10210)  # target - slippage
    assert r.gross_pnl_usd == pytest.approx(10.60)  # bid-to-bid 21.2 pips * 5000 units
    assert r.net_pnl_usd == pytest.approx(9.55)  # 9.90 - 0.35 commission
    assert r.cost_usd == pytest.approx(1.05)
    assert r.net_r == pytest.approx(1.91)
    assert r.label_available_at == bars["time_utc"].iloc[2] + pd.Timedelta(minutes=1)


def test_stop_wins_intrabar_ambiguity():
    bars = make_bars(
        [(1.1, 1.1, 1.1, 1.1), (1.10000, 1.10010, 1.09990, 1.1), (1.10000, 1.10300, 1.09800, 1.10000)]
        + [(1.1, 1.1, 1.1, 1.1)] * 4
    )
    r = sim(bars, Direction.LONG)
    assert r.exit_reason == "STOP" and r.net_r < -1.0  # stop + costs


def test_gap_through_stop_fills_at_open():
    bars = make_bars(
        [(1.1, 1.1, 1.1, 1.1), (1.10000, 1.10010, 1.09990, 1.1), (1.09800, 1.09850, 1.09700, 1.09800)]
        + [(1.098, 1.098, 1.098, 1.098)] * 4
    )
    r = sim(bars, Direction.LONG)
    assert r.exit_reason == "STOP_GAP"
    assert r.exit_price == pytest.approx(1.09800 - 0.00002)
    assert r.net_r < -2.0  # worse than the planned 1R stop


def test_short_pays_ask_on_exit():
    bars = make_bars(
        [(1.1, 1.1, 1.1, 1.1), (1.10000, 1.10010, 1.09990, 1.1), (1.10000, 1.10000, 1.09700, 1.09750)]
        + [(1.0975, 1.0975, 1.0975, 1.0975)] * 4
    )
    r = sim(bars, Direction.SHORT)
    # entry 1.09998 (bid - slip); target 1.09798 hit when ASK low (1.09700+0.0001=1.09710) <= target
    assert r.exit_reason == "TARGET"
    assert r.entry_price == pytest.approx(1.09998)
    assert r.exit_price == pytest.approx(1.09800)


def test_time_exit():
    bars = make_bars([(1.1, 1.1, 1.1, 1.1)] + [(1.1, 1.1001, 1.0999, 1.1)] * 6)
    r = sim(bars, Direction.LONG, hold=3)
    assert r.exit_reason == "TIME" and r.bars_held == 3


def test_no_fill_after_gap():
    bars = make_bars([(1.1, 1.1, 1.1, 1.1)] * 6)
    bars.loc[1:, "time_utc"] += pd.Timedelta(hours=48)
    assert sim(bars, Direction.LONG).status == "NO_NEXT_BAR"


def test_insufficient_future_data_not_labelled():
    bars = make_bars([(1.1, 1.1, 1.1, 1.1)] * 3)
    assert sim(bars, Direction.LONG, hold=5).status == "INSUFFICIENT_DATA"


def test_usdjpy_pnl_converted_to_usd():
    bars = make_bars(
        [(150, 150, 150, 150), (150.000, 150.050, 149.990, 150.0), (150.000, 150.300, 149.995, 150.2)]
        + [(150.2, 150.2, 150.2, 150.2)] * 4
    )
    r = sim(bars, Direction.LONG, stop=0.100, target=0.200, spec=USDJPY, costs=CostModel())
    assert r.exit_reason == "TARGET"
    # risk 5 USD at 150 -> 0.1 JPY * units / 150 = 5  => units = 7500 -> lots 0.07 (floor)
    assert r.lots == pytest.approx(0.07)
    assert r.net_pnl_usd == pytest.approx((r.exit_price - r.entry_price) * 7000 / r.exit_price)


def test_size_below_minimum_skipped():
    xau = default_spec("XAUUSD")
    bars = make_bars([(2000, 2000, 2000, 2000)] * 8, spread=30)
    r = sim(bars, Direction.LONG, stop=50.0, target=100.0, spec=xau)
    assert r.status == "SIZE_BELOW_MINIMUM"


def test_costs_reduce_results_monotonically():
    bars = make_bars(
        [(1.1, 1.1, 1.1, 1.1), (1.10000, 1.10100, 1.09950, 1.10050), (1.10050, 1.10250, 1.10000, 1.10200)]
        + [(1.102, 1.102, 1.102, 1.102)] * 4
    )
    rs = [sim(bars, Direction.LONG, costs=COSTS.scaled(m)).net_r for m in (0.0, 1.0, 2.0)]
    assert rs[0] > rs[1] > rs[2]


def test_estimated_cost_r():
    c = estimated_cost_r(EURUSD, 10, 0.0010, CostModel(3.5, 2), price=1.1)
    assert c == pytest.approx((14 * 1e-5 + 7 / 100_000) / 0.0010)


def test_engine_no_overlap_and_reproducible():
    rows = [(1.1, 1.1001, 1.0999, 1.1)] * 30
    bars = make_bars(rows)
    cands = pd.DataFrame(
        {
            "time_utc": bars["time_utc"].iloc[[0, 1, 2, 10]].to_numpy(),
            "available_at": (bars["time_utc"].iloc[[0, 1, 2, 10]] + pd.Timedelta(minutes=1)).to_numpy(),
            "strategy": "trend",
            "strategy_version": "t",
            "direction": "LONG",
            "stop_distance": 0.001,
            "target_distance": 0.002,
            "max_hold_bars": 5,
            "regime": "TREND_UP",
            "eligible": True,
            "eligibility_reason": "OK",
        }
    )
    a = run_backtest(bars, cands, Timeframe.M1, EURUSD, COSTS, SIZING)
    b = run_backtest(bars, cands, Timeframe.M1, EURUSD, COSTS, SIZING)
    assert len(a.trades) == 2 and a.skipped["POSITION_OPEN"] == 2
    pd.testing.assert_frame_equal(a.trades, b.trades)


def test_labels_carry_availability_time():
    bars = make_bars([(1.1, 1.1001, 1.0999, 1.1)] * 20)
    cands = pd.DataFrame(
        {
            "time_utc": [bars["time_utc"].iloc[0], bars["time_utc"].iloc[18]],
            "available_at": [bars["time_utc"].iloc[0] + pd.Timedelta(minutes=1)] * 2,
            "strategy": "trend",
            "strategy_version": "t",
            "direction": "LONG",
            "stop_distance": 0.001,
            "target_distance": 0.002,
            "max_hold_bars": 5,
            "regime": "TREND_UP",
            "eligible": True,
            "eligibility_reason": "OK",
        }
    )
    lab = label_candidates(bars, cands, Timeframe.M1, EURUSD, COSTS, SIZING)
    assert len(lab) == 1  # the second candidate has no complete horizon -> no label
    assert (lab["label_available_at"] > lab["available_at"]).all()


def test_metrics():
    assert max_drawdown([1, 3, 0, 2]) == 3
    t = pd.DataFrame(
        {
            "net_r": [1.0, -1.0, 2.0],
            "net_pnl_usd": [5.0, -5.0, 10.0],
            "gross_pnl_usd": [6, -4, 11],
            "cost_usd": [1, 1, 1],
            "exit_reason": ["TARGET", "STOP", "TARGET"],
            "entry_time": pd.to_datetime(["2024-01-01 00:00"] * 3, utc=True),
            "exit_time": pd.to_datetime(["2024-01-01 00:10"] * 3, utc=True),
        }
    )
    m = trade_metrics(t, 1000)
    assert m["trades"] == 3 and m["profit_factor"] == 3.0 and m["win_rate"] == pytest.approx(2 / 3)
    assert m["return_on_reference_pct"] == pytest.approx(1.0)
    assert trade_metrics(t.iloc[:0])["trades"] == 0
    assert not math.isnan(m["sharpe_per_trade"])
