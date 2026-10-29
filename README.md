# mt5-sclapingbot

> Research system for adaptive, risk-aware short-timeframe trading on MetaTrader 5.

**Maintainer:** [@tito-devsec](https://github.com/tito-devsec)
**Development status:** 🧪 **Research prototype (pre-alpha).** Data collection, features, local ML, cost-aware simulation, adaptive selection, risk evaluation, news intake and observe-only demo monitoring are implemented and tested offline. **Order execution does not exist and is out of scope.**

**Suggested GitHub topics:** `mt5` · `metatrader5` · `algorithmic-trading` · `scalping-bot` · `machine-learning` · `adaptive-trading` · `quantitative-finance` · `python` · `forex` · `risk-management`

> [!WARNING]
> This software places no trades and has no demonstrated profitability. Every result it produces is a simulation or an observation for research. See [Risk disclosures](#risk-disclosures).

---

## Contents

- [Overview](#overview)
- [Features and status](#features-and-status)
- [Architecture](#architecture)
- [Repository structure](#repository-structure)
- [Requirements](#requirements)
- [Installation (Windows PowerShell)](#installation-windows-powershell)
- [Configuration](#configuration)
- [Usage](#usage)
- [Machine learning](#machine-learning)
- [Testing and validation](#testing-and-validation)
- [Risk disclosures](#risk-disclosures)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing, license and support](#contributing-license-and-support)

## Overview

Fixed-rule scalping bots apply the same logic in every market and are very sensitive to spread, commission and slippage. This project researches a different approach:

1. **Classify the market regime** (trend, range, breakout/expansion, high or low volatility, or uncertain) using information available at each bar's close.
2. **Generate candidates from specialist strategies** (trend-following and breakout; mean-reversion is optional) that are eligible only in suitable regimes.
3. **Score candidates** with a calibrated local ML model and with performance memory measured **after costs**, and **abstain** when the evidence is weak.
4. **Pass every proposal through an independent risk engine** that cannot be overridden, and record the full decision for later review.
5. **Learn only from outcomes that were knowable.** Each outcome is evaluated after its horizon closes, and drift is monitored.

Configured for the JustMarkets **demo** account, the USD reference currency, EURUSD, GBPUSD, USDJPY and XAUUSD, and the M1, M5, M15 and H1 timeframes. The $1,000 balance used for sizing is a **configured reference value**, not a verified account balance.

## Features and status

**Implemented** means the code exists and has automated tests. **Partial** means part of the feature exists. **Planned** means not started. **Not verified live** means it has been tested only against fakes or fixtures in this environment.

| Feature | Status | Files | Notes |
|---|---|---|---|
| MT5 connectivity (read-only) | Implemented · not verified live | `gateway/mt5_gateway.py` | Allow-list proxy; trading functions raise. Needs Windows and a running terminal. |
| Demo and currency validation | Implemented · not verified live | `gateway/account.py` | Fails closed; `require_demo` can only be `true`. |
| Broker symbol resolution | Implemented | `gateway/symbols.py` | Suffixes and prefixes handled; ambiguity is an error. |
| Server-time → UTC (incl. DST) | Implemented | `gateway/clock.py` | `NY+7`, IANA zone or fixed offset. |
| Historical collection (M1–H1) | Implemented · not verified live | `ingest/collector.py` | Incremental, checkpointed, idempotent, rate-limited, retried. |
| Candle validation and deduplication | Implemented | `ingest/quality.py` | Invalid rows rejected, gaps reported, forming candle excluded. Nothing is filled. |
| Tick / bid-ask collection | Partial | `gateway/*`, `storage/repositories.py` | Gateway and storage exist; the collector does not schedule tick collection yet. |
| SQLite storage and migrations | Implemented | `storage/` | 3 migrations, WAL, constraints, automatic backup before upgrades. |
| Feature engineering | Implemented | `features/pipeline.py` | 44 documented features ([docs/FEATURES.md](docs/FEATURES.md)), tested for look-ahead. |
| GARCH volatility forecast | Implemented (research) | `features/garch.py` | Causal; not in the default feature set. |
| Rule-based regime detection | Implemented | `regime/rules.py` | Returns `UNCERTAIN` for missing or out-of-distribution inputs. |
| HMM regimes and change-point audit | Implemented (research) | `regime/hmm.py`, `regime/changepoints.py` | Not wired into decisions. |
| Trend and breakout strategies | Implemented | `strategies/specialists.py` | Research candidates with **no validated edge**. |
| Mean-reversion strategy | Implemented, off by default | `strategies/specialists.py` | Must be validated independently first. |
| Cost-aware backtesting | Implemented | `backtest/` | Next-bar fills, spread, slippage, commission, swap, stop-first ambiguity rule, cost sensitivity. |
| Walk-forward / purged validation | Implemented | `evaluation/`, `ml/training.py` | Untouched test window, purge and embargo, bootstrap, PSR and DSR. |
| Local ML models | Implemented | `ml/` | logreg, random forest, LightGBM, XGBoost, CatBoost; calibration; conformal abstention. |
| Model versioning and rollback | Implemented | `ml/registry.py` | skops artifacts, SHA-256, type allow-list, champion vs. challenger gates. |
| Hyper-parameter tuning and SHAP | Implemented | `ml/tuning.py`, `ml/explain.py` | Validation folds only; every trial is counted. |
| Adaptive strategy selection | Implemented | `selection/selector.py` | Lower-confidence-bound evidence with minimum samples, plus champion probability; `NO_TRADE` / `UNCERTAIN`. |
| Contextual bandit | Implemented (offline only) | `selection/bandit.py` | LinUCB replay with delayed rewards; not in the decision path. |
| Online learning | Not implemented by design | — | Retraining goes through the gated registry. `river` is used for drift detection only. |
| Concept-drift detection | Implemented | `drift/` | PSI, chi-square, Brier, Page-Hinkley + ADWIN, feed staleness, spread shift. |
| Persistent decision memory | Implemented | `memory/outcomes.py`, `storage/` | Abstentions and rejections recorded; counterfactual candidate outcomes; rule-based attribution. |
| Independent risk engine | Implemented | `risk/engine.py` | 15 check groups, reason codes, fail-closed. |
| News: official RSS feeds | Implemented · **verified live** | `news/providers/rss.py` | Fed, ECB and BoE feeds fetched and parsed on 2026-10-29. Disabled by default. |
| News: economic calendar | Implemented | `news/providers/calendar_csv.py` | Local CSV import only. |
| News: licensed APIs (Finnhub, NewsAPI) | Planned | — | Configuration keys reserved. |
| Event-risk filter | Implemented | `news/event_filter.py` | Stale feeds mean "unknown", never "no news". |
| News sentiment | Implemented (baseline) | `news/sentiment.py` | Stored only; not yet used as a model feature. |
| Optional LLM research adapter | Implemented · not verified live | `llm/` | Off by default; schema-validated; cost cap; no trading authority. |
| Demo monitoring (observe-only) | Implemented · not verified live | `monitor/service.py` | Account re-validated every cycle; graceful shutdown. |
| Reporting | Implemented | `reporting/report.py` | CLI status, HTML and CSV, labelled SIMULATED / HISTORICAL / OBSERVED_DEMO. |
| Order execution | **Not implemented, out of scope** | — | Enforced by `tests/safety/`. |
| MQL5 components | Not implemented | — | None in this repository. |
| Deep learning (PyTorch / ONNX), MLflow | Planned | — | Optional extras declared; no code uses them yet. |

## Architecture

The full component description is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). There is a single decision path:

```
closed bars + news (known at t) → features → regime → eligible candidates → scoring (model + memory)
→ independent risk engine → RECOMMEND / NO_TRADE / UNCERTAIN / REJECTED → decision record
→ outcome evaluated after the horizon → performance memory and drift checks
```

## Repository structure

```
mt5-sclapingbot/
├── src/mt5bot/
│   ├── cli.py              # `mt5bot` entry point
│   ├── config/             # settings, default.toml, config reference generator
│   ├── core/               # types, errors, redacting logging
│   ├── gateway/            # read-only MT5 gateway, account guard, symbols, clock, fakes
│   ├── storage/            # SQLite, migrations/, repositories
│   ├── ingest/             # collector, data-quality checks
│   ├── features/           # indicators, feature pipeline, GARCH
│   ├── regime/             # rules, HMM, change points
│   ├── strategies/         # strategy specs and specialists
│   ├── backtest/           # simulator, engine, metrics
│   ├── labeling/           # meta-labels
│   ├── evaluation/         # splits, statistics
│   ├── ml/                 # dataset, models, training, registry, promotion, tuning, SHAP
│   ├── selection/          # reward, selector, bandit
│   ├── risk/               # risk engine
│   ├── news/               # providers, relevance, sentiment, event filter
│   ├── llm/                # optional research adapter
│   ├── memory/             # outcomes and attribution
│   ├── drift/              # detectors and monitor
│   ├── pipeline/           # decision pipeline and jobs
│   ├── monitor/            # observe-only demo monitor
│   ├── reporting/          # status, HTML, CSV
│   └── testing/            # deterministic synthetic bars (tests only)
├── tests/                  # unit/, integration/, safety/
├── docs/                   # architecture, configuration, features, guides
├── .github/                # CI, Dependabot, CODEOWNERS, templates
├── pyproject.toml          # package, extras, tool configuration
├── requirements-lock.txt   # pinned snapshot of the test environment
├── .env.example            # secret placeholders only
└── LICENSE, CHANGELOG.md, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md
```

## Requirements

| Requirement | Value |
|---|---|
| OS | **Windows** for live MT5 access, because the MetaTrader5 package ships Windows wheels only. Offline research and tests also run on Linux. |
| Python | **3.12** (`>=3.12,<3.14`) |
| MetaTrader 5 | Desktop terminal installed, running and logged in to the **JustMarkets demo** account during collection and monitoring |
| Account | Demo account in USD |
| Network | The terminal's connection to the broker, plus outbound HTTPS for any news feeds you enable |
| Storage | Depends on history depth. One year of M1 bars is about 370,000 rows per symbol in SQLite; size has not been measured on real data. |

## Installation (Windows PowerShell)

These commands follow the repository's actual layout. The automated tests have run on Linux only; the Windows steps have not been run in this environment.

```powershell
# 1. Python 3.12 from python.org (tick "Add to PATH"), then check:
py -3.12 --version

# 2. Install the MetaTrader 5 terminal from your broker, then log in to your JustMarkets DEMO account.

# 3. Clone and create a virtual environment
git clone https://github.com/tito-devsec/mt5-sclapingbot.git
cd mt5-sclapingbot
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 4. Install (core + all ML extras + dev tools)
python -m pip install --upgrade pip
pip install -e ".[all,dev]"

# 5. Local configuration (both files are git-ignored)
Copy-Item .env.example .env                       # fill in MT5 login/password/server if needed
New-Item -ItemType Directory -Force config | Out-Null
Copy-Item src\mt5bot\config\default.toml config\local.toml

# 6. Check the terminal, demo account, currency, symbols and server time
mt5bot check
#    -> set [mt5] server_timezone in config/local.toml (e.g. "NY+7") from the offset it reports

# 7. Collect history, then run the tests
mt5bot collect --from 2024-01-01
python -m pytest -q
```

No MQL5 component exists, so nothing needs compiling.

## Configuration

Every key, with its type, default, validation rule and secrecy, is listed in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). That file is generated from the validation code with `mt5bot config docs`.

- **Sources**, highest priority first: `MT5BOT_<SECTION>__<KEY>` environment variables, then `.env`, then `config/local.toml`, then packaged defaults. Unknown keys are rejected.
- **Secrets** (`mt5.login`, `mt5.password`, the news API keys and `llm.api_key`) are `SecretStr`. They are redacted from logs, errors and reports.
- **Restarts.** Settings are read at start-up, so any change needs a restart.
- **`risk.reference_balance_usd`** sizes simulated risk. It is never presented as your account balance; `mt5bot check` shows the terminal's figures separately.

## Usage

```powershell
mt5bot check                                                     # terminal, demo account, currency, symbols, server offset
mt5bot collect --from 2024-01-01                                  # all configured symbols/timeframes (incremental)
mt5bot collect --symbols XAUUSD --timeframes M1,M5
mt5bot inspect --symbol EURUSD --timeframe M1 --tail 5            # counts, quality issues, last rows
mt5bot news refresh                                               # enabled RSS providers / calendar CSV
mt5bot features build --symbol EURUSD --timeframe M5              # parquet in reports/
mt5bot backtest --symbol EURUSD --timeframe M1 --from 2024-01-01  # SIMULATED, with cost sensitivity
mt5bot train --timeframe M1                                       # challenger + promotion gates
mt5bot replay --symbol EURUSD --timeframe M1 --from 2024-06-01    # HISTORICAL decisions + performance memory
mt5bot recommend --symbol EURUSD --timeframe M5 --observe         # one OBSERVED_DEMO research decision
mt5bot monitor start                                              # observe-only loop (Ctrl+C to stop)
mt5bot outcomes                                                   # evaluate matured decisions
mt5bot drift --symbol EURUSD --timeframe M1
mt5bot models list | history | show --id <id> | rollback
mt5bot strategies list | restore --strategy trend --symbol EURUSD --timeframe M1
mt5bot status
mt5bot report                                                     # reports/report.html + reports/csv/
```

Exit codes: `0` ok, `1` runtime error, `2` validation failure (configuration, account, symbol, time zone).

## Machine learning

See [docs/ML_GUIDE.md](docs/ML_GUIDE.md) for the full protocol: labels, splits, purging, calibration, conformal abstention, promotion gates, the reward formula, attribution rules and drift thresholds.

In brief:

- **Target.** A meta-label model estimates whether each candidate is **net-profitable after costs**. Predicting price direction is not the target.
- **Training.** Models train locally on CPU and are evaluated once on an untouched chronological test window.
- **Promotion.** A challenger is promoted only if it beats both the rule baseline and the champion after costs, under cost stress and across walk-forward folds, and passes a deflated-Sharpe gate that accounts for every recorded trial.

No model has been trained on real data in this repository, so no performance numbers exist or are claimed.

## Testing and validation

| Check | Result in this environment (Linux, Python 3.12.3, 2026-10-29) |
|---|---|
| `python -m pytest -q` | **248 passed**. MT5 is faked; synthetic and fixture data only. |
| `ruff check` / `ruff format --check` | clean |
| `mypy src` | clean (86 files) |
| Live RSS fetch (Fed, ECB, BoE) | fetched and parsed successfully |
| Windows CI | configured in `.github/workflows/ci.yml`; **not run yet** |
| Live MT5 terminal, demo account, real history | **not run.** It needs your Windows PC. |
| LLM adapter against the real API | **not run.** Tested with a fake client only. |

**What the tests cover:**
- MT5 gateway failures, account guard and symbol ambiguity, DST time conversion
- duplicate, missing, out-of-order and invalid bars; transaction safety; migrations
- indicator correctness against independent implementations, warm-up handling, look-ahead truncation tests, timeframe alignment
- news parsing, unsafe XML, rate limits, staleness, availability timing and prompt-injection text
- training reproducibility, chronological purged splits, conformal sets matching MAPIE, registry integrity, rejection and rollback
- selector rules, risk rejections and fail-closed behaviour, simulator arithmetic against a hand-calculated trade
- drift detection on simulated deterioration, end-to-end pipeline and monitor runs, CLI workflows

**Safety tests** scan every source file for `order_send`, `OrderSend`, `CTrade` and similar APIs. A fake MT5 module raises if a trading function is reached. A further test proves that inference opens no network connection.

**What the tests do not show:** anything about real-market performance. Backtests can be distorted by overfitting, look-ahead bias, unrealistic fills, data errors and missing costs. An extended **demo forward observation** is required before any result means anything.

## Risk disclosures

- Trading forex, CFDs and gold involves **substantial risk** and leverage. Losses can be rapid and can exceed your expectations and, depending on your broker and jurisdiction, your deposit.
- **Machine learning cannot guarantee profitability.** Models fail when markets change.
- **Past or simulated results do not predict future results.**
- Costs, spreads, execution and symbol specifications vary by broker and account type.
- This is a **research project**. It is not financial advice and not a validated trading system.
- **You are solely responsible** for your accounts, your decisions and compliance with your local laws.

## Security

- Keep credentials in `.env` or environment variables only. `.env`, `config/local.toml`, databases, logs, reports, market data and model artifacts are git-ignored.
- Never publish logins, passwords, server names tied to your account, balances or API keys.
- Secrets are redacted from logs and exceptions (this is tested), and the LLM adapter strips account and credential fields before sending anything.
- Model artifacts are loaded only after a SHA-256 match and a type allow-list check.
- SQL is parameterized and lives in repository classes.
- CI uses a read-only token, actions pinned to commit SHAs, and `persist-credentials: false`. Dependabot is configured.
- Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). It covers a terminal that is not running, a non-demo account, currency mismatch, symbol suffixes, server time zone, missing history, stale news, rejected challengers, installation problems and SQLite locking.

## Roadmap

There are no delivery dates. The detailed phase plan is in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

1. **Done:** repository foundation and Phases 0–19 of the plan, offline and tested.
2. **Next, on your Windows PC:** run `mt5bot check`, set the server time zone, collect real history, backtest, train, then run the monitor in observe-only mode.
3. **Engineering:** schedule tick collection, add a licensed news provider, and verify the Windows CI run.
4. **ML research:**
   - test news and sentiment features out of sample
   - compare the HMM and supervised regime models
   - run gated deep-learning experiments
5. **Demo validation (Phase 20):** a pre-agreed observe-only period, with results compared against walk-forward expectations.
6. **Live trading:** any consideration needs a separate review and your explicit authorization. It is not part of this project's scope.

## Contributing, license and support

- [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)
- Licensed under the [MIT License](LICENSE). Copyright (c) 2026 Tito Oscar Mwaisengela. Third-party dependencies keep their own licenses.
- Support: GitHub issues.

Maintained by [@tito-devsec](https://github.com/tito-devsec).
