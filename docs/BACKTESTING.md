# Backtesting guide

All backtest output is **SIMULATED**. A good backtest is necessary but never sufficient evidence of an edge.

## Run a backtest

```powershell
mt5bot backtest --symbol EURUSD --timeframe M1 --from 2024-01-01 --to 2025-01-01
mt5bot backtest --symbol XAUUSD --timeframe M5 --strategies breakout --json
```

Each run is repeated for every cost multiplier in `evaluation.cost_multipliers` (default ×0.5, ×1, ×1.5, ×2, ×3). Every run and every simulated trade is stored in the `backtest_runs` and `simulated_trades` tables.

## Fill and cost rules (`src/mt5bot/backtest/simulator.py`)

| Rule | Behaviour |
|---|---|
| Timing | A candidate generated at the close of bar *i* fills at the **open of bar i+1**. |
| Gaps | If bar *i+1* is not the immediately following bar (data gap or weekend), the candidate is skipped as `NO_NEXT_BAR` and is never filled after the gap. |
| Prices | MT5 bars are bid prices, and ask = bid + bar spread. Longs buy at the ask and sell at the bid. Shorts sell at the bid and buy back at the ask. |
| Slippage | `costs.slippage_points` is applied adversely on both entry and exit. |
| Levels | Stop and target are anchored to the actual entry fill, not to the signal price. |
| Intrabar ambiguity | If one bar touches both stop and target, the **stop** is assumed first. Tick-level resolution is not implemented. |
| Gap through stop | A bar that opens beyond the stop exits at that open (`STOP_GAP`), which is worse than the planned 1R. |
| Time exit | At the close of the last allowed bar (`strategy.max_hold_minutes`). |
| Commission | `costs.commission_per_lot_per_side_usd` × 2 × lots. |
| Swap | Points per lot per rollover crossed. Rollover is approximated as 21:00 UTC. |
| Sizing | Risk budget = min(reference balance × `risk_per_trade_pct`, `max_risk_per_trade_usd`). Lots are floored to the volume step. A position below the minimum lot is skipped (`SIZE_BELOW_MINIMUM`). |
| Overlap | One open position per strategy. `risk.max_open_positions` caps concurrent positions. |
| Conversion | USD-quoted pairs and gold: P&L in USD directly. USD-base pairs (USDJPY): P&L divided by the exit price. Other crosses are refused. |

The $1,000 reference balance is a configured simulation value. It is never a verified account balance.

## Metrics (`src/mt5bot/backtest/metrics.py`)

- **Totals:** trade count, net P&L (USD), net R, expectancy (R per trade).
- **Trade quality:** win rate, average win and loss, profit factor.
- **Drawdown:** maximum drawdown in R and in USD.
- **Variability:** standard deviation of R, and a **per-trade Sharpe** (mean ÷ std of net R per trade, not annualised, because trades are not equally spaced).
- **Exposure:** average holding time.
- **Costs:** total cost (USD) and cost as a share of absolute gross P&L.
- **Exits and breakdowns:** exit-reason counts, plus breakdowns by strategy and by regime.

Metrics are not reported when a run has no trades.

## Known limitations

- Bar data cannot resolve the order of events inside a bar, so the stop-first rule is a conservative assumption.
- A bar's spread is the value MT5 reports for that bar, which may differ from the spread at the exact fill moment.
- Swap timing is approximate, and swap values default to 0 until you configure them from your broker's specification.
- Non-USD crosses are not supported.
- Backtests do not model requotes, partial fills, liquidity limits or broker execution policies.
