# Contributing to mt5-sclapingbot

Thank you for your interest. This document explains how to propose changes. Submitting a contribution does not guarantee that it will be accepted. Every change is reviewed by the maintainer, [@tito-devsec](https://github.com/tito-devsec).

> [!NOTE]
> Run the same checks as CI before opening a pull request: `ruff check src tests`, `ruff format --check src tests`, `mypy src` and `python -m pytest -q`.

## Project purpose

`mt5-sclapingbot` is a research project for adaptive, risk-aware short-timeframe trading on MetaTrader 5. Correctness, reproducibility, and safety come before features and claimed returns.

## Supported contribution types

- Bug reports and fixes
- Documentation improvements
- Tests
- Data-validation, backtesting, and evaluation improvements
- Research proposals with reproducible experiments
- Security reports, which go through [SECURITY.md](SECURITY.md) and **not** public issues

## Bug reports

Use the **Bug report** issue template. Include:

- the steps to reproduce
- the expected and actual behaviour
- your environment (OS, Python version, MT5 build)
- logs, with **all account identifiers, credentials, and balances removed**

## Feature requests

Use the **Feature request** issue template. Explain the problem it solves and how it would be validated.

## Pull request procedure

1. Open an issue first for anything non-trivial.
2. Fork the repository and create a descriptively named branch, for example `fix/candle-dedup`.
3. Keep each PR focused on one change.
4. Complete the pull request template. Every PR must:
   1. **Explain the purpose** of the change.
   2. **Add or update tests** for changed behaviour.
   3. **Document** behaviour and configuration changes in the README and CHANGELOG.
   4. **Contain no secrets**, credentials, account data, or private datasets.
   5. Provide **reproducible experiments** for ML changes.
   6. Make **no profitability claims** without sufficient, reproducible, cost-aware evidence.
   7. **Not bypass** risk checks or account safeguards.
   8. **Disclose third-party code** and its license obligations.
   9. Contain **no unlicensed proprietary code**.
   10. Respect the project's [MIT License](LICENSE). By submitting a contribution, you agree that it is licensed under the MIT License.
5. Expect review comments, and respond to them in the PR thread.

## Coding standards

- **Python style:** PEP 8, enforced by `ruff` (lint and format, 120-character lines) as configured in `pyproject.toml`.
- **Comments:** single-line comments and single-line docstrings only. Put longer explanations in `docs/`.
- **Type annotations:** required on public functions and module boundaries.
- **SQL:** parameterized queries only, never string-formatted SQL.
- **No hidden network calls or telemetry.**

## Tests

- New behaviour needs tests, and bug fixes need a regression test.
- Tests must never connect to a live account or place orders. Mock the MT5 API instead.
- Report only test results you have actually run.

## Documentation

Update the README's feature-status table when a feature's status changes. Add an entry to `CHANGELOG.md` under **Unreleased**.

## Dependency changes

- Justify every new dependency, and prefer well-maintained packages.
- Pin versions in the manifest.
- Check the dependency's license for compatibility.

## Security-sensitive changes

Changes to credential handling, account validation, the risk engine, order execution, or CI workflows need extra review. Describe the security impact in the PR.

## ML experiment reproducibility

For any model or strategy change, record:

- data source, symbols, timeframes, and date ranges
- the exact train, validation, and test boundaries
- random seeds
- library versions
- cost assumptions: spread, commission, slippage, and swap
- the commands used to reproduce the result

## Backtesting standards

- Include realistic transaction costs.
- Use chronological, out-of-sample evaluation, preferably walk-forward.
- Report drawdown, trade count, and the cost model alongside any return figure.
- Treat in-sample results as development aids, not evidence.

## Data-leakage prevention

- Compute features only from data available at decision time.
- Never shuffle across time when splitting data.
- Fit scalers, encoders, and feature selection on training data only.
- Do not tune repeatedly on the test set.

## Risk-engine protection

The risk engine and account safeguards, such as demo-account enforcement and position limits, are safety-critical. A PR must not weaken, disable, or bypass them. Any change to their logic requires explicit maintainer approval and tests.

## Commit messages

Use clear, imperative messages, for example `Add candle deduplication`. Conventional Commits prefixes (`feat:`, `fix:`, `docs:`) are welcome. Do not include credentials or account details in commit messages.

## Review requirements

All changes require maintainer review. CI checks must pass once CI exists.

## Release process (intended)

1. Move the **Unreleased** changelog entries under a new version heading.
2. Tag the release with a semantic version, for example `v0.1.0`.
3. Publish GitHub release notes based on the changelog.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
