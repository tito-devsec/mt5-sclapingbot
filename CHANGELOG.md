# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers will follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once releases begin.

## [Unreleased]

### Added

- `README.md` documenting the project goal, its pre-implementation status, the planned architecture, and risk disclosures.
- `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, reporting through GitHub private reporting).
- `.gitignore` for Python environments, secrets, local databases, logs, market data, and model artifacts.
- GitHub issue templates and a pull request template.
- `LICENSE` (MIT, copyright Tito Oscar Mwaisengela).
- `docs/IMPLEMENTATION_PLAN.md` with the proposed phased build plan.
- `mt5bot` Python package (Python 3.12) implementing plan Phases 0-19, tested offline:
  - read-only MT5 gateway (allow-list proxy), demo/USD account guard, broker symbol resolution, DST-aware server clock;
  - SQLite storage with three versioned migrations, automatic pre-upgrade backup and repository classes;
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
- 248 automated tests, including safety tests that forbid order-execution APIs.
- CI workflow (least privilege, pinned actions), Dependabot, CODEOWNERS, `.env.example`, `requirements-lock.txt`.
- Documentation: architecture, configuration reference (generated), features (generated), ML, backtesting, risk,
  news providers, troubleshooting and optional VPS deployment.

- MetaTrader 5 integration:
  - MQL5 chart indicator showing decisions, stop-loss and take-profit lines and tick verdicts;
  - demo-only, observe-only observer EA recording every tick and resolving stop vs. target first touch live;
  - tick-export script;
  - Python bridge (`mt5bot bridge`), Python tick collection (`mt5bot ticks`) and tick-resolved backtests (`--tick-resolve`);
  - migration 0004.
- Ljung-Box autocorrelation p-value (statsmodels) in trade metrics; hypothesis property test for the risk engine.

### Security

- Secrets are `SecretStr` values redacted from logs and errors; `config/local.toml`, `reports/` and backups are git-ignored.
