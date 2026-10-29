"""Performance metrics computed only from simulated trades (accounting in USD and R)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))
    return float(np.max(peak[1:] - equity)) if len(equity) else 0.0


def trade_metrics(trades: pd.DataFrame, reference_balance_usd: float | None = None) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "no trades; metrics not computable"}
    r = trades["net_r"].to_numpy(float)
    pnl = trades["net_pnl_usd"].to_numpy(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq_r = np.cumsum(r)
    eq_usd = np.cumsum(pnl)
    gross_profit, gross_loss = wins.sum(), -losses.sum()
    hold = (pd.to_datetime(trades["exit_time"]) - pd.to_datetime(trades["entry_time"])).dt.total_seconds() / 60
    out: dict[str, Any] = {
        "trades": n,
        "net_pnl_usd": float(pnl.sum()),
        "net_r": float(r.sum()),
        "expectancy_r": float(r.mean()),
        "win_rate": float((pnl > 0).mean()),
        "avg_win_usd": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_usd": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else math.inf,
        "max_drawdown_r": max_drawdown(eq_r),
        "max_drawdown_usd": max_drawdown(eq_usd),
        "r_std": float(r.std(ddof=1)) if n > 1 else 0.0,
        # Per-trade Sharpe: mean/std of net R per trade. Not annualised; trades are not equally spaced.
        "sharpe_per_trade": float(r.mean() / r.std(ddof=1)) if n > 1 and r.std(ddof=1) > 0 else 0.0,
        "avg_hold_minutes": float(hold.mean()),
        "total_cost_usd": float(trades["cost_usd"].sum()),
        "cost_share_of_gross_abs": float(trades["cost_usd"].sum() / max(trades["gross_pnl_usd"].abs().sum(), 1e-12)),
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
    }
    if reference_balance_usd:
        out["return_on_reference_pct"] = float(100 * pnl.sum() / reference_balance_usd)
    return out


def breakdown(trades: pd.DataFrame, by: str) -> dict[str, dict[str, Any]]:
    if trades.empty or by not in trades:
        return {}
    return {str(k): trade_metrics(g) for k, g in trades.groupby(by)}
