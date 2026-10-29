# Machine-learning training and evaluation guide

Everything here runs locally on CPU. No hosted model is needed for training or inference.

## What the models predict

The main model is a **meta-label model**. For each strategy candidate, it predicts the probability that the candidate is **net-profitable after costs** (spread, slippage, commission and swap), based on features known at the decision bar's close.

Predicting price direction is deliberately not the target. A correct direction can still lose money once costs are paid.

## Data and labels

- **Labels.** A candidate is labelled by simulating it with the backtest fill rules (`labeling/meta.py`). Each label records `label_available_at`, the close of the exit bar.
- **Unfinished labels.** A label is never used for training before its `label_available_at`, and candidates without a complete future horizon get no label.
- **Model inputs.** These are the registered features (`docs/FEATURES.md`) plus strategy, direction and regime indicators. Rows with any missing feature are dropped; values are never imputed.

## Training protocol (`ml/training.py`)

1. Order samples by decision time (`available_at`).
2. Split them chronologically into train, validation and an **untouched test** window (`evaluation.validation_fraction`, `evaluation.test_fraction`).
3. **Purge and embargo:**
   - Training rows whose label was not yet known before the evaluation window starts are removed.
   - An embargo of `max_hold + 1 + evaluation.embargo_bars` bars also applies.
4. Fit the model on the first 75% of the training window, then calibrate it (sigmoid) on the last 25%. Scaling is inside the scikit-learn pipeline, so it is fit on training rows only.
5. Fit split-conformal prediction sets (LAC, `ml.conformal_confidence`) on the calibration rows. Only `{success}` sets count as confident. `{both}` or `{}` means **abstain**. The sets are verified in tests to match MAPIE's `SplitConformalClassifier`.
6. Evaluate once on the test window.
7. Run walk-forward folds (`evaluation.n_folds`) that repeat the whole procedure inside the pre-test data.

**Model families:** `logreg`, `random_forest`, `lightgbm`, `xgboost` and `catboost`. All are seeded with `ml.random_seed` and use a single thread, so results are deterministic.

**Optional training extras:**
- Optuna tuning (`--tune N`) searches **on validation folds only**, and every trial is counted.
- Undersampling (imbalanced-learn) is available in `fit_meta_model(rebalance="undersample")` and touches fitting rows only.

## Commands

```powershell
mt5bot train --timeframe M1 --from 2024-01-01             # default model type from ml.model_type
mt5bot train --timeframe M5 --model catboost --tune 30    # Optuna on validation folds
mt5bot models list
mt5bot models history
mt5bot models show --id <model-id>
mt5bot models rollback
```

## Champion vs. challenger (`ml/promotion.py`, `ml/registry.py`)

Every trained model is registered as a `CHALLENGER`. It is promoted only if **all** of these gates pass on the untouched test window:

| Gate | Requirement |
|---|---|
| `min_trades` | selected trades ≥ `evaluation.min_trades_for_promotion` |
| `positive_expectancy_after_costs` | expectancy R > 0 |
| `beats_rule_baseline` | beats "take every rule-eligible candidate" |
| `beats_champion` | beats the current champion re-evaluated on the same test window (if a champion exists) |
| `survives_cost_stress` | expectancy > 0 at `evaluation.promotion_cost_multiplier` × costs |
| `max_drawdown_r` | ≤ `evaluation.max_drawdown_r_for_promotion` |
| `calibration_brier` | Brier ≤ `evaluation.max_brier_for_promotion` |
| `walk_forward_consistency` | share of positive folds ≥ `evaluation.min_positive_fold_share` |
| `deflated_sharpe` | deflated-Sharpe probability ≥ `evaluation.min_deflated_sharpe_prob`, using the total number of trials recorded in `experiments` |

How promotion, rejection and rollback work:

- **Rejection.** A failed gate marks the candidate `REJECTED`, and the champion stays unchanged.
- **Audit trail.** Every decision is written to the `promotions` table.
- **Rollback.** `mt5bot models rollback` restores the previous champion after re-verifying its artifact.
- **Failed runs.** A failed training run raises before registration, so it cannot affect the champion.

Artifacts are saved with **skops**, a SHA-256 hash and an allow-list of types. A tampered, missing or untrusted artifact is refused. Inference then falls back to the documented evidence-only baseline, which may result in `NO_TRADE`.

## Adaptive selection (`selection/`)

**Reward**, defined before use:

```
reward = clip(net_R, -1.5, 3.0) - 0.25 * max(0, MAE_R - 1)
```

**Performance memory** keeps n, the mean reward and the variance per bucket: strategy × symbol × timeframe × regime × session × volatility bucket × news context. It is updated from **every rule-eligible candidate** once its horizon closes, simulated counterfactually when it was not selected, so the evidence is not biased by what was chosen.

**Selector rules:**
- A bucket with n ≥ `selection.min_samples` and a lower confidence bound (LCB) ≤ 0 vetoes the candidate.
- **Without a champion,** a candidate needs n ≥ `min_samples` and LCB > 0.
- **With a champion,** a candidate also needs p ≥ `ml.probability_threshold` and a confident conformal set.

**Outputs:** `RECOMMEND`, `NO_TRADE`, or `UNCERTAIN`. `UNCERTAIN` covers an uncertain regime, bad or stale data, an open critical drift alert, or model uncertainty. The risk engine can still turn any `RECOMMEND` into `REJECTED`.

**A single loss never retrains or switches anything.** Strategies are suspended only by critical drift alerts. `mt5bot strategies restore` reactivates a strategy only after a fresh backtest on recent data shows positive expectancy with enough trades.

**Contextual bandit.** A LinUCB bandit (`selection/bandit.py`) is implemented for **offline replay only**, with delayed rewards. It is not used in the decision path.

## Regime models

- **Rules (`regime/rules.py`).** The live baseline, versioned `rules-1.0.0`. Out-of-distribution inputs (Mahalanobis distance against recent history) become `UNCERTAIN`.
- **Gaussian HMM (`regime/hmm.py`).** Fit on training rows; predictions use forward filtering only. It is a research model and not wired into the decision path.
- **Change-point audit (`regime/changepoints.py`, ruptures).** Measures how well rule transitions match structural breaks. It sees the whole series, so it is never used for features or decisions.
- **GARCH(1,1) forecast (`features/garch.py`, arch).** Causal and tested for look-ahead. It is not in the default feature set because of its cost.

## Outcome attribution (`memory/outcomes.py`)

Rules are applied in this order; the first match wins:

| Code | Evidence |
|---|---|
| `RISK_REJECTED` | decision was rejected; the counterfactual trade lost |
| `INVALID_WIN` | decision was rejected, but the counterfactual trade won (a win by chance) |
| `STALE_DATA` | bars missing inside the holding window |
| `UNREALISTIC_FILL_ASSUMPTION` | entry-bar spread > 2× the spread at decision time |
| `NEWS_VOLATILITY` | relevant news became available during the hold, and the hold range was > 3× decision ATR |
| `COST_DOMINATED` | gross result > 0 but net ≤ 0 |
| `VALID_WIN` | recommended decision with a net win |
| `FAILED_BREAKOUT` | breakout stopped out within the first third of the horizon |
| `TREND_REVERSAL` | trend trade reached ≥ +1R, then stopped out |
| `POOR_TIMING` | reached ≥ +1R but finished at a loss |
| `REGIME_MISCLASSIFIED` | the regime re-classified on the exit bar is not one the strategy requires |
| `VALID_LOSS` | loss no worse than −1.5R |
| `UNATTRIBUTED` | none of the above |
| `NOT_SELECTED` | nothing was selected; rule-eligible candidates were still evaluated for memory |

## Drift monitoring (`drift/`)

| Type | Method | Threshold | Response |
|---|---|---|---|
| Input drift | PSI per feature | 0.10 warning, 0.25 critical | raise uncertainty (critical blocks decisions) |
| Regime change | chi-square on regime frequencies | p < 0.01 | raise uncertainty |
| Prediction quality | Brier vs. validation baseline | +25% | keep champion, request re-evaluation |
| Strategy performance | Page-Hinkley, confirmed by ADWIN (river) | detector defaults | ADWIN-confirmed: suspend strategy; otherwise raise uncertainty |
| Feed failure | missed bars vs. expected cadence | 3 bars | NO_TRADE until fresh; resolves automatically |
| Execution cost | p95 spread ratio + KS test | 1.5× | local alert (risk spread limits still apply) |

The system never retrains automatically and never promotes a model without passing the gates.

## Collect real data, then train

```powershell
mt5bot check                                          # demo account, USD, symbols, server time
mt5bot collect --from 2024-01-01                       # all configured symbols and timeframes
mt5bot inspect                                         # counts and data-quality issues
mt5bot backtest --symbol EURUSD --timeframe M1         # rule baseline after costs
mt5bot train --timeframe M1                            # challenger + gates
mt5bot replay --symbol EURUSD --timeframe M1 --from 2024-06-01   # builds performance memory
mt5bot report
```

## Not implemented yet

- A PyTorch sequence model, ONNX export and an MLflow UI. The extras are declared, but no code uses them yet.
- An ML regime classifier wired into decisions.
- Live tick-level intrabar resolution.
