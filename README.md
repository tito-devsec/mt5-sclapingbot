# mt5-sclapingbot

> Research system for adaptive, risk-aware short-timeframe trading on MetaTrader 5.

**Maintainer:** [@tito-devsec](https://github.com/tito-devsec)
**Development status:** 🧪 **Research prototype (pre-alpha).** Plan Phases 0–19 are implemented and tested offline, and the code includes a MetaTrader 5 chart and tick integration. The next step is live verification on a Windows PC with the JustMarkets demo terminal. **Order execution does not exist and is out of scope.**

**Latest release:** [v0.1.0](https://github.com/tito-devsec/mt5-sclapingbot/releases/tag/v0.1.0) (research pre-release; see [CHANGELOG.md](CHANGELOG.md))

**Suggested GitHub topics:** `mt5` · `metatrader5` · `mql5` · `algorithmic-trading` · `scalping-bot` · `machine-learning` · `adaptive-trading` · `quantitative-finance` · `python` · `forex` · `risk-management`

> [!WARNING]
> This software places no trades and has no demonstrated profitability. Every result it produces is a simulation or an observation for research. See [Risk disclosures](#risk-disclosures).

---

## Contents

- [Project progress](#project-progress)
- [Overview](#overview)
- [Features and status](#features-and-status)
- [MetaTrader 5 integration](#metatrader-5-integration)
- [Libraries used and why](#libraries-used-and-why)
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

## Project progress

| Stage | State |
|---|---|
| Repository foundation (docs, policies, MIT license, CI) | ✅ Done |
| Phases 0–4: scaffolding, configuration, read-only MT5 gateway, storage, ingestion | ✅ Done, tested offline |
| Phases 5–8: features, regimes, strategies, cost-aware simulator, labels | ✅ Done, tested offline |
| Phases 9–12: walk-forward evaluation, local ML, model registry, selector, risk engine | ✅ Done, tested offline |
| Phases 13–16: news, optional LLM adapter, decision memory, drift detection | ✅ Done; news feeds verified live |
| Phases 17–19: pipeline, observe-only monitor, CLI, reports, safety, docs | ✅ Done, tested offline |
| MT5 chart and tick integration (MQL5 indicator, observer EA, tick export, Python bridge) | ✅ Built. Python side tested; MQL5 statically checked; **not yet compiled in MetaEditor** |
| Live verification on your PC (terminal, demo account, real history, MQL5 compile) | ⏳ Next |
| Phase 20: demo forward observation | ⏳ After live verification |
| Live trading | ⛔ Out of scope; it would need a separate review and your authorization |

## Overview

Fixed-rule scalping bots apply the same logic in every market and are very sensitive to spread, commission and slippage. This project researches a different approach:

1. **Classify the market regime** (trend, range, breakout/expansion, high or low volatility, or uncertain) using information available at each bar's close.
2. **Generate candidates from specialist strategies** (trend-following and breakout; mean-reversion is optional) that are eligible only in suitable regimes.
3. **Select candidates by the strategy rules** (current mode). Later, a calibrated local ML model and performance memory measured **after costs** can do the scoring and **abstain** when the evidence is weak.
4. **Pass every proposal through an independent risk engine** that cannot be overridden, and record the full decision for later review.
5. **Show everything on the MT5 charts** (decisions, stop-loss, take-profit, and which level real ticks hit first), and learn only from outcomes that were knowable at the time.

Configured for the JustMarkets **demo** account, the USD reference currency, EURUSD, GBPUSD, USDJPY and XAUUSD, and the M1, M5, M15 and H1 timeframes. The $1,000 balance used for sizing is a **configured reference value**, not a verified account balance.

## Features and status

**Implemented** means the code exists and has automated tests. **Partial** means part of the feature exists. **Planned** means not started. **Not verified live** means it has been tested only against fakes or fixtures here.

| Feature | Status | Files | Notes |
|---|---|---|---|
| MT5 connectivity (read-only) | Implemented · not verified live | `gateway/mt5_gateway.py` | Allow-list proxy; trading functions raise. Needs Windows and a running terminal. |
| Demo and currency validation | Implemented · not verified live | `gateway/account.py`, EA `OnInit` | Python and the EA both refuse non-demo accounts. |
| Broker symbol resolution | Implemented | `gateway/symbols.py` | Suffixes and prefixes handled; ambiguity is an error. |
| Server-time → UTC (incl. DST) | Implemented | `gateway/clock.py` | `NY+7`, IANA zone or fixed offset. |
| Historical bar collection (M1–H1) | Implemented · not verified live | `ingest/collector.py` | Incremental, checkpointed, idempotent, rate-limited, retried. |
| Candle validation and deduplication | Implemented | `ingest/quality.py` | Invalid rows rejected, gaps reported, forming candle excluded. Nothing is filled. |
| Tick collection | Implemented · not verified live | `pipeline/ticks_job.py`, observer EA, export script | Python API (full range or ambiguous bars only), live EA recording, or terminal script. |
| **MT5 chart display** | Implemented · **not compiled yet** | `mql5/Indicators/MT5Bot/MT5Bot_Decisions.mq5` | Decision arrows, SL/TP/entry lines, tick-verdict markers, status panel. |
| **Live SL/TP first-touch resolution** | Implemented · **not compiled yet** | `mql5/Experts/MT5Bot/MT5Bot_Observer.mq5` | Every tick checked; longs exit on bid, shorts on ask. |
| Tick-resolved backtests and outcomes | Implemented | `backtest/ticks.py`, `memory/outcomes.py` | Bars touching both stop and target are settled by ticks; otherwise stop-first and flagged. |
| SQLite storage and migrations | Implemented | `storage/` | 4 migrations, WAL, constraints, automatic backup before upgrades. |
| Feature engineering | Implemented | `features/pipeline.py` | 44 documented features ([docs/FEATURES.md](docs/FEATURES.md)), tested for look-ahead. |
| GARCH volatility forecast | Implemented (research) | `features/garch.py` | Causal; not in the default feature set. |
| Rule-based regime detection | Implemented | `regime/rules.py` | Returns `UNCERTAIN` for missing or out-of-distribution inputs. |
| HMM regimes and change-point audit | Implemented (research) | `regime/hmm.py`, `regime/changepoints.py` | Not wired into decisions. |
| Trend and breakout strategies | Implemented | `strategies/specialists.py` | Research candidates with **no validated edge**. |
| Mean-reversion strategy | Implemented, off by default | `strategies/specialists.py` | Must be validated independently first. |
| Cost-aware backtesting | Implemented | `backtest/` | Next-bar fills, spread, slippage, commission, swap, cost sensitivity, Ljung-Box check. |
| Walk-forward / purged validation | Implemented | `evaluation/`, `ml/training.py` | Untouched test window, purge and embargo, bootstrap, PSR and DSR. |
| Local ML models | Implemented | `ml/` | logreg, random forest, LightGBM, XGBoost, CatBoost; calibration; conformal abstention. |
| Model versioning and rollback | Implemented | `ml/registry.py` | skops artifacts, SHA-256, type allow-list, champion vs. challenger gates. |
| Hyper-parameter tuning and SHAP | Implemented | `ml/tuning.py`, `ml/explain.py` | Validation folds only; every trial is counted. |
| Rules-based selection (**current default**) | Implemented | `selection/selector.py` | `selection.mode = "rules"`: a rule-eligible candidate goes to the risk engine; memory vetoes proven losers. No ML model is used. |
| Adaptive / ML selection (for later) | Implemented, not default | `selection/selector.py` | `mode = "evidence"` (performance-memory LCB) or `mode = "ml"` (champion probability + conformal abstention). |
| Contextual bandit | Implemented (offline only) | `selection/bandit.py` | LinUCB replay with delayed rewards; not in the decision path. |
| Concept-drift detection | Implemented | `drift/` | PSI, chi-square, Brier, Page-Hinkley + ADWIN, feed staleness, spread shift. |
| Persistent decision memory | Implemented | `memory/outcomes.py`, `storage/` | Abstentions and rejections recorded; counterfactual candidate outcomes; rule-based attribution. |
| Independent risk engine | Implemented | `risk/engine.py` | 15 check groups, reason codes, fail-closed, property-tested. |
| News: official RSS feeds | Implemented · **verified live** | `news/providers/rss.py` | Fed, ECB and BoE feeds fetched and parsed on 2026-10-29. Disabled by default. |
| News: economic calendar | Implemented | `news/providers/calendar_csv.py` | Local CSV import only. |
| News: licensed APIs (Finnhub, NewsAPI) | Planned | — | Configuration keys reserved. |
| Event-risk filter | Implemented | `news/event_filter.py` | Stale feeds mean "unknown", never "no news". |
| Optional LLM research adapter | Implemented · not verified live | `llm/` | Off by default; schema-validated; cost cap; no trading authority. |
| Demo monitoring (observe-only) | Implemented · not verified live | `monitor/service.py` | Account re-validated every cycle; chart bridge synced every cycle. |
| Reporting | Implemented | `reporting/report.py` | CLI status, HTML and CSV, labelled SIMULATED / HISTORICAL / OBSERVED_DEMO. |
| Online learning | Not implemented by design | — | Retraining goes through the gated registry. |
| Deep learning (PyTorch / ONNX), MLflow | Planned | — | Optional extras declared; no code uses them yet. |
| Order execution | **Not implemented, out of scope** | — | Enforced by `tests/safety/` for Python **and** MQL5. |

## MetaTrader 5 integration

The full guide is [docs/MT5_INTEGRATION.md](docs/MT5_INTEGRATION.md). MT5 is used in two layers:

- **Python `MetaTrader5` API.** Connection, account and currency checks, symbols, bar history and tick history.
- **MQL5 inside the terminal (`mql5/`):**
  - **`MT5Bot_Decisions` (indicator):**
    - buy and sell arrows for recommendations, and × marks for risk rejections (the tooltip lists the reason codes)
    - dashed stop-loss, take-profit and entry lines across each holding window
    - ✓/✗ markers where ticks hit the take-profit or stop-loss first
    - a status panel: data age, mode, champion, news state, alerts, counts
  - **`MT5Bot_Observer` (Expert Advisor):**
    - **demo-only and observe-only**
    - records every tick, using `CopyTicksRange` so no tick is skipped
    - anchors stop and target to the live fill and decides tick by tick which is hit first
    - draws live levels and writes results plus a heartbeat
  - **`MT5Bot_ExportTicks` (script):** exports historical ticks from the terminal for tick-resolved backtests.

**Why it matters.** A bar whose range covers both the stop and the target cannot say which was hit first. Python on bars alone must assume the worse case (stop first), which understates results, especially on M1 scalping. With MT5 ticks, mt5bot settles those bars with evidence. Every backtest also prints how many exits were ambiguous and how many were settled by ticks.

Python and MT5 exchange small ASCII CSV files in MT5's `Common\Files\mt5bot` folder:
- **Python writes:** decisions, watched levels and status.
- **The EA writes:** tick verdicts, tick files and a heartbeat.

The MQL5 code contains no trade library and no order call. It is statically checked by `tests/safety/`. It **has not been compiled in this environment**, because MetaEditor runs on Windows only.

## Libraries used and why

Versions are those installed in the test environment (Python 3.12.3). `requirements-lock.txt` pins the full set.

| Library | Version | Install extra | Used for | Where |
|---|---|---|---|---|
| **MetaTrader5** | 5.0.6231 (PyPI) | core (Windows only) | Official MT5 Python API: terminal connection, account info, symbols, `copy_rates_range`, `copy_ticks_range` | `gateway/mt5_gateway.py` |
| **MQL5** | MT5 terminal | — | Chart indicator, observer EA and tick export inside the terminal | `mql5/` |
| numpy | 2.5.4 | core | Arrays for the simulator, statistics, detectors and the bandit | throughout |
| pandas | 3.1.0 | core | Bars and features as time-zone-aware frames, as-of joins, SQL reads | throughout |
| scipy | 1.18.1 | core | Normal distribution (PSR/DSR), KS and chi-square tests, skew and kurtosis, multivariate normal for HMM filtering | `evaluation/stats.py`, `drift/`, `regime/hmm.py` |
| scikit-learn | 1.9.1 | core | Pipelines, scaling, logistic regression, random forest, sigmoid calibration (`CalibratedClassifierCV` + `FrozenEstimator`), Brier / log-loss / AUC | `ml/` |
| statsmodels | 0.15.0 | core | Ljung-Box autocorrelation test on trade results | `evaluation/stats.py` |
| pyarrow | 26.0.0 | core | Parquet engine for `mt5bot features build` | via pandas |
| skops | 0.16.0 | core | Safe model serialization with a type allow-list instead of pickle | `ml/registry.py` |
| pydantic / pydantic-settings | 2.14.1 / 2.16.2 | core | Typed, validated configuration from TOML, `.env` and environment variables; `SecretStr`; LLM output schemas | `config/`, `llm/schemas.py` |
| httpx | 0.28.1 | core | News HTTP with timeouts and retries | `news/providers/rss.py` |
| feedparser | 6.0.14 | core | RSS/Atom parsing | `news/providers/rss.py` |
| defusedxml | 0.7.1 | core | Rejects unsafe XML (DTDs, entity expansion) before parsing | `news/providers/rss.py` |
| lightgbm | 4.7.0 | `boost` | Gradient-boosted meta-label models | `ml/models.py` |
| xgboost | 3.4.1 | `boost` | Gradient-boosted meta-label models | `ml/models.py` |
| catboost | 1.2.10 | `boost` | Gradient-boosted meta-label models | `ml/models.py` |
| hmmlearn | 0.3.3 | `regime` | Gaussian HMM regime discovery (research) | `regime/hmm.py` |
| ruptures | 1.1.10 | `regime` | Offline change-point audit of regime labels | `regime/changepoints.py` |
| arch | 8.0.0 | `regime` | Causal GARCH(1,1) volatility forecasts | `features/garch.py` |
| river | 0.26.1 | `online` | ADWIN drift detector, confirming Page-Hinkley alerts | `drift/detectors.py` |
| optuna | 5.0.0 | `tuning` | Hyper-parameter search on validation folds only | `ml/tuning.py` |
| shap | 0.54.1 | `explain` | Global and per-decision feature attributions for tree models | `ml/explain.py` |
| MAPIE | 1.6.0 | `uncertainty` | Reference implementation that verifies our conformal prediction sets (test) | `tests/unit/test_ml_selection_risk.py` |
| imbalanced-learn | 0.14.2 | `uncertainty` | Optional undersampling of fitting rows only | `ml/training.py` |
| anthropic | 2.x | `llm` | Optional research adapter (summaries and explanations only) | `llm/adapter.py` |
| pytest / hypothesis | 9.2.0 / 6.170.13 | `dev` | Test suite and property-based risk-engine test | `tests/` |
| ruff / mypy | 0.17.3 / 2.4.1 | `dev` | Lint, format and type checks (CI) | `pyproject.toml` |
| numba, torch, onnxruntime, mlflow | — | `perf`, `deep`, `tracking` | **Declared but not used yet** (planned research); not installed by `[all]` | — |

The standard library supplies `sqlite3` (storage), `argparse` (CLI), `zoneinfo` (DST-aware time zones) and `logging` (with secret redaction).

## Architecture

The full component description is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). There is a single decision path:

```
closed bars + news (known at t) → features → regime → eligible candidates → scoring (model + memory)
→ independent risk engine → RECOMMEND / NO_TRADE / UNCERTAIN / REJECTED → decision record
→ MT5 charts (indicator) + live SL/TP tracking (observer EA)
→ outcome evaluated after the horizon (ticks settle SL/TP ties) → performance memory and drift checks
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
│   ├── backtest/           # simulator, engine, metrics, tick resolution
│   ├── labeling/           # meta-labels
│   ├── evaluation/         # splits, statistics
│   ├── ml/                 # dataset, models, training, registry, promotion, tuning, SHAP
│   ├── selection/          # reward, selector, bandit
│   ├── risk/               # risk engine
│   ├── news/               # providers, relevance, sentiment, event filter
│   ├── llm/                # optional research adapter
│   ├── memory/             # outcomes and attribution
│   ├── drift/              # detectors and monitor
│   ├── bridge/             # file exchange with the MQL5 components
│   ├── pipeline/           # decision pipeline, jobs, tick collection
│   ├── monitor/            # observe-only demo monitor
│   ├── reporting/          # status, HTML, CSV
│   └── testing/            # deterministic synthetic bars (tests only)
├── mql5/
│   ├── Include/MT5Bot/Bridge.mqh
│   ├── Indicators/MT5Bot/MT5Bot_Decisions.mq5
│   ├── Experts/MT5Bot/MT5Bot_Observer.mq5
│   └── Scripts/MT5Bot/MT5Bot_ExportTicks.mq5
├── tests/                  # unit/, integration/, safety/
├── docs/                   # architecture, MT5 integration, configuration, features, guides
├── .github/                # CI, Dependabot, CODEOWNERS, templates
├── pyproject.toml          # package, extras, tool configuration
├── requirements-lock.txt   # pinned snapshot of the test environment
├── .env.example            # secret placeholders only
└── LICENSE, CHANGELOG.md, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md
```

## Requirements

| Requirement | Value |
|---|---|
| OS | **Windows** for MT5 (the Python package and MetaEditor are Windows-only). Offline research and tests also run on Linux. |
| Python | **3.12** (`>=3.12,<3.14`) |
| MetaTrader 5 | Desktop terminal installed, running and logged in to the **JustMarkets demo** account; MetaEditor for compiling the MQL5 files |
| Account | Demo account in USD |
| Network | The terminal's connection to the broker, plus outbound HTTPS for any news feeds you enable |
| Storage | Depends on history depth. One year of M1 bars is about 370,000 rows per symbol; ticks are far larger (record only what you need). |

## Installation (Windows PowerShell)

The test suite passes on Windows in CI. These interactive steps with a real MT5 terminal have not been run yet.

```powershell
# 1. Python 3.12 from python.org (tick "Add to PATH"), then check:
py -3.12 --version

# 2. Install the MetaTrader 5 terminal from your broker, then log in to your JustMarkets DEMO account.

# 3. Clone and create a virtual environment
git clone https://github.com/tito-devsec/mt5-sclapingbot.git
cd mt5-sclapingbot
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 4. Install (core + ML extras + dev tools)
python -m pip install --upgrade pip
pip install -e ".[all,dev]"

# 5. Local configuration (both files are git-ignored)
Copy-Item .env.example .env
New-Item -ItemType Directory -Force config | Out-Null
Copy-Item src\mt5bot\config\default.toml config\local.toml

# 6. Check the terminal, demo account, currency, symbols and server time
mt5bot check
#    -> set [mt5] server_timezone in config/local.toml (e.g. "NY+7") from the offset it reports

# 7. Collect history and run the tests
mt5bot collect --from 2024-01-01
python -m pytest -q
```

**MQL5 components:**
1. In MT5, choose File → Open Data Folder, then copy `mql5\Include\MT5Bot`, `mql5\Indicators\MT5Bot`, `mql5\Experts\MT5Bot` and `mql5\Scripts\MT5Bot` into the matching folders under `MQL5\`.
2. Open each `.mq5` file in MetaEditor (F4) and press F7 to compile.
3. Attach `MT5Bot_Decisions` to your charts, and `MT5Bot_Observer` to one chart per symbol.
4. Run `mt5bot monitor start`.

Details are in [docs/MT5_INTEGRATION.md](docs/MT5_INTEGRATION.md).

## Configuration

Every key, with its type, default, validation rule and secrecy, is listed in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). That file is generated from the validation code with `mt5bot config docs`.

- **Sources**, highest priority first: `MT5BOT_<SECTION>__<KEY>` environment variables, then `.env`, then `config/local.toml`, then packaged defaults. Unknown keys are rejected.
- **Secrets** (`mt5.login`, `mt5.password`, the news API keys and `llm.api_key`) are `SecretStr`. They are redacted from logs, errors and reports.
- **`bridge.*`** controls the MT5 file exchange: on by default, using MT5's Common\Files folder.
- **`risk.reference_balance_usd`** sizes simulated risk. It is never presented as your account balance.

## Usage

```powershell
mt5bot check                                                     # terminal, demo account, currency, symbols, server offset
mt5bot collect --from 2024-01-01                                  # bars, all configured symbols/timeframes (incremental)
mt5bot ticks ambiguous --symbol EURUSD --timeframe M1             # ticks only for bars that hit both SL and TP
mt5bot ticks collect --symbol EURUSD --from 2024-06-03 --to 2024-06-04
mt5bot inspect --symbol EURUSD --timeframe M1 --tail 5
mt5bot news refresh
mt5bot backtest --symbol EURUSD --timeframe M1 --tick-resolve     # SIMULATED, cost sensitivity, tick-settled ties
mt5bot train --timeframe M1                                       # challenger + promotion gates
mt5bot replay --symbol EURUSD --timeframe M1 --from 2024-06-01    # HISTORICAL decisions + performance memory
mt5bot recommend --symbol EURUSD --timeframe M5 --observe         # one OBSERVED_DEMO research decision
mt5bot monitor start                                              # observe-only loop; syncs the MT5 charts each cycle
mt5bot bridge export | import | path                              # manual chart/EA exchange
mt5bot verify                                                     # rules-based trades the EA resolved on live ticks
mt5bot outcomes
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

**Current mode is rules-based** (`selection.mode = "rules"`). The ML stack is built and tested, but no model is used for decisions until you switch to `mode = "ml"` after a model passes the promotion gates on your real data. No model has been trained on real data yet, so no performance numbers exist or are claimed.

## Testing and validation

| Check | Result |
|---|---|
| `python -m pytest -q` (Linux, Python 3.12.3) | **278 passed**. MT5 is faked; synthetic and fixture data only. |
| GitHub Actions CI (Windows + Ubuntu, Python 3.12) | **Passed** on commit `6d0b0c8`. It re-runs on every push. |
| `ruff check` / `ruff format --check` / `mypy src` | clean |
| Live RSS fetch (Fed, ECB, BoE) | fetched and parsed successfully |
| MQL5 static checks (no trading API, demo guard, syntax balance, includes, formats agree with Python) | passed |
| MQL5 compilation in MetaEditor | **not run** (needs Windows MetaEditor) |
| Live MT5 terminal, demo account, real history, chart rendering | **not run** (needs your PC) |
| LLM adapter against the real API | **not run** (tested with a fake client only) |

**What the tests cover:**
- MT5 gateway failures, account guard and symbol ambiguity, DST time conversion
- duplicate, missing, out-of-order and invalid bars; transaction safety; migrations
- indicator correctness, look-ahead truncation tests, timeframe alignment
- news parsing, unsafe XML, rate limits, staleness, availability timing and prompt-injection text
- training reproducibility, purged splits, conformal sets matching MAPIE, registry integrity, rejection and rollback
- selector rules, risk rejections, fail-closed behaviour and a hypothesis property test
- simulator arithmetic against a hand-calculated trade, plus tick first-touch logic and tick-settled ambiguous bars
- bridge file formats and imports, drift detection, end-to-end pipeline, monitor and CLI runs

**Safety tests** scan every Python and MQL5 file for order and position APIs. They also check that the EA's demo-only guard is present, and that inference opens no network connection.

**What the tests do not show:** anything about real-market performance. A demo forward observation is required before any result means anything.

## Risk disclosures

- Trading forex, CFDs and gold involves **substantial risk** and leverage. Losses can be rapid and can exceed your expectations and, depending on your broker and jurisdiction, your deposit.
- **Machine learning cannot guarantee profitability.** Models fail when markets change.
- **Past or simulated results do not predict future results.**
- Costs, spreads, execution and symbol specifications vary by broker and account type.
- This is a **research project**. It is not financial advice and not a validated trading system.
- **You are solely responsible** for your accounts, your decisions and compliance with your local laws.

## Security

- Keep credentials in `.env` or environment variables only. `.env`, `config/local.toml`, databases, logs, reports, market data and model artifacts are git-ignored.
- Never publish logins, passwords, server names tied to your account, balances or API keys. The bridge files contain no credentials or balances.
- Secrets are redacted from logs and exceptions (this is tested), and the LLM adapter strips account and credential fields.
- Model artifacts are loaded only after a SHA-256 match and a type allow-list check. SQL is parameterized.
- CI uses a read-only token, actions pinned to commit SHAs, and `persist-credentials: false`. Dependabot is configured.
- Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). It covers the terminal, the demo account, currency, symbol suffixes, the server time zone, missing history, stale news, rejected challengers, MQL5 compile and attach problems, and the stale chart panel.

## Roadmap

There are no delivery dates. The detailed phase plan is in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

1. **Next, on your Windows PC** (step-by-step: [docs/VERIFY_ON_MT5.md](docs/VERIFY_ON_MT5.md)):
   - Compile the MQL5 files and report any MetaEditor message.
   - Run `mt5bot check` and set the server time zone.
   - Collect real history, run a tick-resolved backtest, and train.
   - Run the observe-only monitor with the indicator and EA attached.
2. **Engineering:** add a licensed news provider, and add chart templates for one-click setup.
3. **ML research:**
   - test news and sentiment features out of sample
   - compare the HMM and supervised regime models
   - run gated deep-learning experiments
4. **Demo validation (Phase 20):** a pre-agreed observe-only period, with results compared against walk-forward expectations.
5. **Live trading:** any consideration needs a separate review and your explicit authorization. It is outside this project's scope.

## Contributing, license and support

- [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)
- Licensed under the [MIT License](LICENSE). Copyright (c) 2026 Tito Oscar Mwaisengela. Third-party dependencies keep their own licenses.
- Support: GitHub issues.

Maintained by [@tito-devsec](https://github.com/tito-devsec).
