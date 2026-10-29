"""Meta-labels: was a candidate net-profitable after costs, available only once its exit bar closed."""

from __future__ import annotations

import pandas as pd

from mt5bot.backtest.simulator import BarArrays, CostModel, Sizing, simulate_trade
from mt5bot.core.types import Direction, InstrumentSpec, Timeframe

LABEL_VERSION = "meta-1.0.0"


def label_candidates(
    bars: pd.DataFrame, candidates: pd.DataFrame, tf: Timeframe, spec: InstrumentSpec, costs: CostModel, sizing: Sizing
) -> pd.DataFrame:
    """Label candidates with net_r and label_available_at; unsimulatable candidates get no label."""
    arrays = BarArrays(bars, tf)
    rows = []
    for c in candidates.itertuples(index=False):
        i = arrays.index_of(c.time_utc)
        if i is None:
            continue
        r = simulate_trade(
            arrays,
            i,
            Direction(c.direction),
            float(c.stop_distance),
            float(c.target_distance),
            int(c.max_hold_bars),
            spec,
            costs,
            sizing,
            strategy=c.strategy,
        )
        if r.status != "FILLED":
            continue
        rows.append(
            {
                **c._asdict(),
                "net_r": r.net_r,
                "gross_r": r.gross_r,
                "cost_r": r.cost_r,
                "mae_r": r.mae_r,
                "mfe_r": r.mfe_r,
                "exit_reason": r.exit_reason,
                "exit_time": r.exit_time,
                "label_available_at": r.label_available_at,
                "label": int(r.net_r > 0),
                "label_version": LABEL_VERSION,
            }
        )
    return pd.DataFrame(rows)
