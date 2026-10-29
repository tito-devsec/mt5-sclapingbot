# Implementation Plan

**Status:** Proposed. Nothing in this plan is implemented yet. There are no delivery dates.

This plan builds the project in phases. Each phase ends with a usable, tested deliverable and an **exit gate**, a condition that must hold before the next phase starts. The order is deliberate. Data quality and evaluation come before machine learning, and risk controls come before any order execution.

## Guiding principles

1. **Demo-only by default.** The code refuses to trade on a live account. Lifting that restriction is a separate, deliberate future decision, outside this plan.
2. **Costs are first-class.** Spread, commission, and slippage are part of every evaluation. Scalping lives or dies on costs.
3. **No look-ahead.** Every feature, label, and fill is computed only from data available at that moment, and tests enforce this.
4. **Baselines before ML.** Rule-based strategies and a rule-based regime classifier come first. A model is only adopted if it beats them out of sample, after costs.
5. **Abstention is a valid output.** "Do not trade" is an expected decision, not a failure.
6. **Everything is reproducible.** Every experiment records its data range, configuration, code version, and random seed.

## Proposed technology choices

These choices are open to revision before Phase 0 starts.

| Area | Choice | Reason |
|---|---|---|
| Language | Python, latest version supported by the `MetaTrader5` package | Required by the official MT5 Python API (check PyPI before pinning). |
| MT5 access | `MetaTrader5` package, installed on Windows only | It is the official API. It is optional on other platforms so that CI and tests can run without it. |
| Data | `pandas`, `numpy` | Standard tools. Indicators are implemented in-house, with no TA-Lib binary dependency. |
| Storage | SQLite through the standard-library `sqlite3` | No server needed; parameterized queries only. |
| Config | `pydantic-settings` with `.env` | Typed, validated configuration; secrets come from the environment. |
| ML | `scikit-learn`, later `lightgbm` | Strong tabular baselines that are easy to inspect. |
| CLI | `typer` or `argparse` | Single entry point: `mt5bot <command>`. |
| Quality | `ruff` (lint and format), `mypy`, `pytest` | |
| CI | GitHub Actions on Ubuntu with MT5 mocked; least-privilege `permissions: contents: read` | |

## Target layout

```
src/mt5bot/
  config.py          # settings + validation
  cli.py             # mt5bot check | collect | backtest | ...
  mt5/               # thin gateway over the MetaTrader5 package (mockable)
  data/              # collection, validation, SQLite repository
  features/          # indicators, feature builders (no look-ahead)
  strategies/        # trend-following, breakout specialists
  regime/            # rule-based, then ML regime classifier
  backtest/          # engine, cost model, metrics
  evaluation/        # chronological splits, walk-forward, reports
  ml/                # training, model registry, drift detection
  risk/              # risk engine (pure functions)
  execution/         # paper + demo order routing
  memory/            # decision and outcome journal
mql5/                # optional monitoring Expert Advisor
tests/
```

---

## Phase 0: Project scaffolding

**Deliverables**
- `pyproject.toml` with pinned dependencies. `MetaTrader5` uses an environment marker so it installs only on Windows.
- `src/` layout, `ruff`, `mypy`, and `pytest` configuration, plus a single smoke test.
- GitHub Actions workflow that runs lint, type checks, and tests on pull requests and pushes, with read-only token permissions.
- `.github/dependabot.yml` for pip packages and GitHub Actions.
- `.github/CODEOWNERS` assigning `risk/`, `execution/`, and `.github/` to `@tito-devsec`.

**Exit gate:** CI is green on `main`, and `pip install -e .[dev]` followed by `pytest` works on Windows and Linux.

## Phase 1: Configuration, connection check, and account safety

**Deliverables**
- Typed settings: terminal path, login and server (secret, from `.env`), symbols, timeframes, database path, expected account currency, and expected starting balance (a reference value only).
- `.env.example` containing placeholders only.
- A gateway that wraps `initialize`, `account_info`, `terminal_info`, `symbol_info`, and `shutdown` behind an interface, so tests can use a fake.
- An **account guard** that fails closed. It refuses to continue if the terminal is not connected, if the account is not a demo account (`trade_mode` is not `ACCOUNT_TRADE_MODE_DEMO`), or if the account currency does not match the configured currency.
- Symbol resolution that handles broker suffixes, for example `EURUSD` → `EURUSD.m`.
- `mt5bot check` prints terminal status, account type, and currency. It never prints the login or the password.

**Exit gate:** unit tests cover each guard failure using the fake gateway, and a manual check against your demo account passes.

## Phase 2: Historical data collection and storage

**Deliverables**
- A SQLite schema `candles(symbol, timeframe, time, open, high, low, close, tick_volume, spread, real_volume)` with primary key `(symbol, timeframe, time)`, plus a `collection_runs` audit table.
- Incremental collection with `copy_rates_range`, resuming from the last stored candle. Inserts use `INSERT OR IGNORE`, so re-running the collector is safe.
- Validation for:
  - high ≥ max(open, close) and low ≤ min(open, close)
  - positive prices
  - monotonic timestamps
  - duplicates
  - gap detection with a weekend and session-aware report

  Invalid rows are quarantined, not silently dropped.
- Documented timestamp convention, because broker server time differs from UTC.
- Optional tick and spread sampling to calibrate the cost model.
- `mt5bot collect --symbols EURUSD --timeframes M1,M5 --from 2024-01-01` and `mt5bot inspect`.

**Exit gate:** a full collection for the chosen symbols completes, re-running it adds zero duplicates, and the validation report is reviewed.

## Phase 3: Features and rule-based regimes

**Deliverables**
- Indicators: EMA, ATR, ADX, RSI, Bollinger width, Donchian channels, and session flags.
- A **look-ahead test harness**. Each feature computed on data truncated at time *t* must equal the same feature computed on the full series at time *t*.
- A rule-based regime classifier with three labels: trending, ranging, and high-volatility, based on ADX and ATR percentiles. It is the baseline the ML classifier must later beat.

**Exit gate:** every feature passes the look-ahead harness.

## Phase 4: Cost-aware backtester and baseline strategies

**Deliverables**
- A bar-based backtest engine:
  - Signals are generated on bar close and filled at the **next bar's open**.
  - Fills pay the stored spread, a configurable commission, and a slippage allowance.
  - Stop-loss and take-profit are resolved conservatively: if both are hit within one bar, the stop counts first.
- Two baseline specialists: trend-following (EMA crossover with ATR stops) and breakout (Donchian or session-range breakout).
- Metrics: net return after costs, number of trades, win rate, profit factor, maximum drawdown, average trade compared with average cost, and exposure.
- `mt5bot backtest --strategy breakout --symbol EURUSD --timeframe M5`.

**Exit gate:** a hand-calculated test case matches the engine's output exactly, and a no-edge strategy (random entries) loses roughly its costs.

## Phase 5: Evaluation framework

**Deliverables**
- Chronological train, validation, and test splitting with a gap (embargo) between them, and **walk-forward** evaluation over rolling windows.
- An experiment record for every run, stored in SQLite: git commit, configuration hash, data range, seeds, and metrics.
- Per-regime performance reports for each strategy, using the Phase 3 baseline regimes.

**Exit gate:** the baseline strategies have walk-forward results. This is the benchmark ML must beat.

## Phase 6: Machine learning

**Deliverables**
- **Labels:** for each bar and strategy, whether taking that strategy's signal would have been profitable **after costs** within its holding horizon. This is meta-labeling.
- **Strategy selector:** a model estimates, for each specialist, the probability that its signal is profitable. The selector picks the best specialist above a confidence threshold, or abstains.
- **ML regime classifier:** compared against the rule-based baseline.
- **Model registry:** versioned artifacts with content hashes, the training data range, and metrics. Each model has a status (`challenger`, `champion`, or `retired`), and any model can be rolled back.
- **Champion vs. challenger:** a challenger replaces the champion only if it wins on walk-forward results after costs.
- **Drift detection:** a population stability index on features, plus rolling live-vs-expected performance. When drift is detected, the selector falls back to the baseline or abstains.

Online learning is deferred. Retraining happens periodically through the registry, which keeps results reproducible and auditable.

**Exit gate:** the selector beats the best single baseline strategy on out-of-sample walk-forward results after costs, or the result is documented as negative and the baseline is kept.

## Phase 7: Risk engine

**Deliverables.** Pure, heavily tested functions. Every proposed order must pass all of these checks:

- the demo-account guard from Phase 1, re-checked on every order
- position sizing by fixed fractional risk, using the **live** account equity from the terminal
- a maximum number of open positions, and a limit per symbol
- maximum daily loss and maximum drawdown limits, which trigger a **kill switch** that stays on until manually reset
- a maximum spread filter, a session or news-window filter, and a minimum stop distance from `symbol_info`

**Exit gate:** property-based tests show that no generated input produces an order that violates a limit.

## Phase 8: Paper trading, demo execution, and monitoring

**Deliverables**
- **Paper mode first:** live data, decisions and risk checks run in real time, and orders are only logged.
- **Demo execution:** `order_check` runs before every `order_send`, using a unique magic number, and positions are reconciled with the terminal.
- **Decision memory:** a journal of every decision (signal, regime, model version, risk verdict, abstentions) and its outcome.
- Structured logs with secrets redacted, plus a daily summary report.
- Optional: an MQL5 Expert Advisor for **read-only** on-chart monitoring. Python remains the only component that places orders.

**Exit gate:** paper mode and the backtest agree on decisions for the same period, and demo orders reconcile with no discrepancies.

## Phase 9: Demo forward test

Run on a demo account for a pre-agreed period and trade count. Both values are for you to set; at least several months is typical. Compare the results with the walk-forward expectations. Any decision about a live account is outside this plan, and requires its own review.

---

## Open decisions (needed before Phase 0)

1. Symbols and broker. This affects suffixes, commission model, and history depth.
2. Primary timeframes, for example M1 and M5 for entries, with M15 and H1 for context.
3. Holding horizon for "scalping", for example minutes up to one hour.
4. Whether the MQL5 monitoring EA is wanted.
5. Python version, after checking the current `MetaTrader5` wheel support.
