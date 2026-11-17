# models/

This folder is where trained models live on your machine. Only this guide is tracked by Git; every model file is ignored, so a model is never pushed by accident.

You do not copy files here by hand. The training command writes them, checks them and records them in the database.

## What gets stored

| Item | Where | Notes |
|---|---|---|
| Model file | `models/<model-id>.skops` | Saved with skops (no pickle). Its SHA-256 is recorded and checked on every load. |
| Model record | `model_versions` table in `data/mt5bot.db` | Type, training window, features, metrics, gate results, status |

Change the folder with `storage.model_dir` in `config/local.toml`.

## Train a model

```powershell
pip install -e ".[boost]"                                  # LightGBM, XGBoost, CatBoost
mt5bot train --symbols EURUSD --timeframe M5 --model lightgbm
mt5bot models list
mt5bot models show --id <model-id>
mt5bot models rollback                                     # return to the previous champion
```

Available model types: `lightgbm` (default), `xgboost`, `catboost`, `random_forest`, `logreg`.

A trained model becomes the **champion** only if it passes every promotion gate (enough trades, calibration, walk-forward results after costs, drawdown, and better than the current champion). Otherwise it is stored as `REJECTED` and never used.

## Where the model plugs in

| `selection.mode` | Candidates the model is trained on | How the champion is used |
|---|---|---|
| `rules` (default) | — | Not used |
| `ml` | Trend / breakout / mean-reversion specialists | Probability threshold plus conformal confidence decide |
| `agent` with `agent.use_champion_model = true` | The agent's 120 technique variants | Extra filter on top of proven skills; it can reject a setup, never approve one alone |

Train in the same mode you will run in. A model trained for one mode is refused in the other.

## Adding a new model type

1. Add a builder in `src/mt5bot/ml/models.py` and its name to `ml.model_type` in `src/mt5bot/config/settings.py`.
2. Add a test in `tests/unit/`.
3. Train it and compare with `mt5bot models list`. The gates decide; there is no manual override.

## When to train

Train after enough real outcomes exist. As a rough guide: several thousand labelled candidates per symbol and timeframe, covering different sessions and volatility conditions. Retrain on a schedule (for example monthly) and let the gates decide whether the new model replaces the old one.

No model in this project has been trained on real market data yet, so no model performance is claimed.
