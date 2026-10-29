# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html); 0.x releases are pre-alpha research releases.

## [Unreleased]

## [0.1.0] - 2026-10-29

First public research release (pre-alpha). Research, data collection, simulation and observe-only monitoring only: **no order execution exists**. Python components are tested offline and in CI (Windows and Ubuntu); the MQL5 components are statically checked but not yet compiled in MetaEditor, and nothing has yet been verified against a live MT5 terminal.

### Added

- Project documentation, policies and README.
- `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, reporting through GitHub private reporting).
- `.gitignore` for Python environments, secrets, local databases, logs, market data, and model artifacts.
- GitHub issue templates and a pull request template.
- `LICENSE` (MIT, copyright Tito Oscar Mwaisengela).
- `docs/IMPLEMENTATION_PLAN.md` with the proposed phased build plan.
- `mt5bot` Python package (Python 3.12) implementing plan Phases 0-19, tested offline:
  - read-only MT5 gateway (allow-list proxy), demo/USD account guard, broker symbol resolution, DST-aware server clock;
  - SQLite storage with four versioned migrations, automatic pre-upgrade backup and repository classes;
  - checkpointed, idempotent bar collection with data-quality checks (closed candles only, no filling);
  - 44-feature causal feature pipeline with generated reference (`docs/FEATURES.md`);
  - rule-based regimes with out-of-distribution handling, Gaussian HMM and change-point audit (research);
  - trend, breakout and (disabled) mean-reversion strategy specialists;
  - cost-aware trade simulator, backtest engine, metrics and cost-sensitivity runs;
  - meta-labels, purged/embargoed chronological splits, walk-forward evaluation, bootstrap, PSR and DSR;
  - logreg, random forest, LightGBM, XGBoost and CatBoost models with calibration and conformal abstention;
  - skops model registry with SHA-256 integrity, champion/challenger promotion gates and rollback; Optuna and SHAP;
  - reliability-weighted adaptive selector, offline LinUCB replay, documented reward formula;
  - independent fail-closed risk engine with reason codes;
  - news provider interface, official Fed/ECB/BoE RSS integration, calendar CSV, relevance, sentiment, event filter;
  - optional, disabled-by-default LLM research adapter with schema validation and cost cap;
  - decision memory with counterfactual candidate outcomes and rule-based attribution; drift detection;
  - observe-only demo monitor, `mt5bot` CLI, HTML/CSV reports.
- 283 automated tests, including safety tests that forbid order-execution APIs in Python and MQL5.
- CI workflow (least privilege, pinned actions), Dependabot, CODEOWNERS, `.env.example`, `requirements-lock.txt`.
- Documentation: architecture, configuration reference (generated), features (generated), ML, backtesting, risk,
  news providers, troubleshooting and optional VPS deployment.
- MetaTrader 5 integration:
  - MQL5 chart indicator showing decisions, stop-loss and take-profit lines and tick verdicts;
  - demo-only, observe-only observer EA recording every tick and resolving stop vs. target first touch live;
  - tick-export script;
  - Python bridge (`mt5bot bridge`), Python tick collection (`mt5bot ticks`) and tick-resolved backtests (`--tick-resolve`);
  - migration 0004.
- Rules-based selection mode (`selection.mode = "rules"`, now the default); `evidence` and `ml` modes kept for later.
- `mt5bot verify` and `docs/VERIFY_ON_MT5.md` for an end-to-end check on the demo terminal with tick-resolved trades.
- Ljung-Box autocorrelation p-value (statsmodels) in trade metrics; hypothesis property test for the risk engine.

### Security

- Secrets are `SecretStr` values redacted from logs and errors; `config/local.toml`, `reports/` and backups are git-ignored.

[Unreleased]: https://github.com/tito-devsec/mt5-sclapingbot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tito-devsec/mt5-sclapingbot/releases/tag/v0.1.0
