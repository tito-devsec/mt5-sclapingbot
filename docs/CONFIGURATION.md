# Configuration reference

Generated from `mt5bot.config.settings` by `mt5bot config docs`; the validation code is the source of truth.

Sources, highest priority first: environment variables `MT5BOT_<SECTION>__<KEY>`, then `.env`, then `config/local.toml` (or the file in `MT5BOT_CONFIG_FILE`), then the packaged defaults. Unknown keys are rejected. All settings are read at start-up, so **every change requires restarting the command**.

| Key | Purpose | Type | Default | Validation | Secret |
|---|---|---|---|---|---|
| `mt5.terminal_path` | Path to terminal64.exe; empty attaches to the running terminal | `str \| None` | `None` | - | no |
| `mt5.login` | Demo account login (numeric). Optional if the terminal is already logged in | `SecretStr \| None` | (none) | - | **yes** |
| `mt5.password` | Demo account password | `SecretStr \| None` | (none) | - | **yes** |
| `mt5.server` | Broker server name for the demo account | `str \| None` | `None` | - | no |
| `mt5.require_demo` | Demo-only guard. Only `true` is accepted | `True` | `True` | - | no |
| `mt5.expected_currency` | Account currency the account guard requires | `str` | `USD` | - | no |
| `mt5.symbol_map` | Explicit canonical -> broker symbol names, used when resolution is ambiguous | `dict[str, str]` | `{}` | - | no |
| `mt5.server_timezone` | How broker server time maps to UTC: 'NY+7', an IANA zone, or empty | `str \| None` | `None` | - | no |
| `mt5.server_utc_offset_hours` | Fixed server UTC offset (no DST) if server_timezone is empty | `float \| None` | `None` | >= -14, <= 14 | no |
| `mt5.timeout_ms` | Terminal connection timeout | `int` | `60000` | >= 1000, <= 600000 | no |
| `mt5.connect_retries` | Extra connection attempts with backoff | `int` | `3` | >= 0, <= 10 | no |
| `instruments.symbols` | Canonical instruments to collect and research | `list[str]` | `['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD']` | - | no |
| `instruments.timeframes` | Timeframes to collect and research | `list[Timeframe]` | `['M1', 'M5', 'M15', 'H1']` | - | no |
| `collection.max_bars_per_request` | Bars requested per copy_rates_range call | `int` | `20000` | >= 100, <= 100000 | no |
| `collection.request_pause_seconds` | Minimum pause between terminal history requests | `float` | `0.2` | >= 0, <= 10 | no |
| `collection.default_history_days` | History depth for a first collection without --from | `int` | `730` | >= 1, <= 3650 | no |
| `collection.tick_sample_minutes` | Reserved for tick sampling (0 = off) | `int` | `0` | >= 0, <= 1440 | no |
| `storage.db_path` | SQLite database file | `Path` | `data/mt5bot.db` | - | no |
| `storage.model_dir` | Model artifact directory | `Path` | `models` | - | no |
| `storage.report_dir` | Report output directory | `Path` | `reports` | - | no |
| `storage.log_dir` | Log directory | `Path` | `logs` | - | no |
| `features.sessions_utc` | Trading sessions as [start_hour, end_hour) in UTC | `dict[str, tuple[int, int]]` | `{'asia': (0, 8), 'london': (7, 16), 'new_york': (12, 21)}` | - | no |
| `features.vol_percentile_window` | Trailing window for the volatility percentile feature | `int` | `500` | >= 50, <= 20000 | no |
| `costs.commission_per_lot_per_side_usd` | Commission per lot per side (depends on account type) | `float` | `0.0` | >= 0, <= 100 | no |
| `costs.slippage_points` | Assumed adverse slippage per fill, by symbol ('*' = default) | `dict[str, float]` | `{'*': 0.0}` | - | no |
| `costs.swap_long_points_per_night` | Swap for longs in points per rollover (positive = credit) | `dict[str, float]` | `{'*': 0.0}` | - | no |
| `costs.swap_short_points_per_night` | Swap for shorts in points per rollover (positive = credit) | `dict[str, float]` | `{'*': 0.0}` | - | no |
| `strategy.enabled` | Strategy specialists that may produce candidates | `list['trend' \| 'breakout' \| 'mean_reversion']` | `['trend', 'breakout']` | - | no |
| `strategy.max_hold_minutes` | Maximum holding time for every candidate | `int` | `60` | >= 1, <= 1440 | no |
| `ml.random_seed` | Seed for every model and search | `int` | `42` | - | no |
| `ml.model_type` | Default challenger model family | `'logreg' \| 'random_forest' \| 'lightgbm' \| 'xgboost' \| 'catboost'` | `lightgbm` | - | no |
| `ml.min_train_samples` | Minimum labelled candidates required to train | `int` | `200` | >= 20 | no |
| `ml.probability_threshold` | Minimum calibrated success probability to select a candidate | `float` | `0.55` | > 0.5, < 1.0 | no |
| `ml.conformal_confidence` | Confidence level of the conformal abstention sets | `float` | `0.8` | > 0.5, < 1.0 | no |
| `evaluation.n_folds` | Walk-forward folds | `int` | `5` | >= 2, <= 50 | no |
| `evaluation.embargo_bars` | Extra embargo bars between training and evaluation windows | `int` | `60` | >= 0 | no |
| `evaluation.test_fraction` | Share of data held out as the untouched test window | `float` | `0.2` | > 0, < 0.5 | no |
| `evaluation.validation_fraction` | Share of data used for validation | `float` | `0.2` | > 0, < 0.5 | no |
| `evaluation.min_trades_for_promotion` | Promotion gate: minimum selected trades on test | `int` | `100` | >= 1 | no |
| `evaluation.max_drawdown_r_for_promotion` | Promotion gate: maximum drawdown in R on test | `float` | `20.0` | > 0 | no |
| `evaluation.max_brier_for_promotion` | Promotion gate: maximum Brier score on test | `float` | `0.25` | > 0, <= 1 | no |
| `evaluation.cost_multipliers` | Cost multipliers for backtest sensitivity runs | `list[float]` | `[0.5, 1.0, 1.5, 2.0, 3.0]` | - | no |
| `evaluation.promotion_cost_multiplier` | Promotion gate: expectancy must stay positive at this cost multiple | `float` | `1.5` | >= 1.0 | no |
| `evaluation.min_positive_fold_share` | Promotion gate: share of walk-forward folds with positive expectancy | `float` | `0.6` | >= 0, <= 1 | no |
| `evaluation.min_deflated_sharpe_prob` | Promotion gate: minimum deflated-Sharpe probability | `float` | `0.5` | >= 0, < 1 | no |
| `selection.mode` | rules (default): rule-eligible candidates go to the risk engine; evidence; or ml (champion) | `'rules' \| 'evidence' \| 'ml'` | `rules` | - | no |
| `selection.rules_memory_veto` | In rules mode, let performance memory veto a strategy proven negative after costs | `bool` | `True` | - | no |
| `selection.min_samples` | Evidence needed before performance memory can qualify or veto a candidate | `int` | `30` | >= 1 | no |
| `selection.lcb_z` | z-score of the lower confidence bound on mean reward | `float` | `1.0` | >= 0, <= 5 | no |
| `selection.reward_clip_low` | Lower clip of net R in the reward | `float` | `-1.5` | - | no |
| `selection.reward_clip_high` | Upper clip of net R in the reward | `float` | `3.0` | - | no |
| `selection.mae_penalty` | Penalty per R of adverse excursion beyond 1R | `float` | `0.25` | >= 0 | no |
| `risk.reference_balance_usd` | Configured reference balance for sizing - NOT a verified account balance | `float` | `1000.0` | > 0 | no |
| `risk.risk_per_trade_pct` | Risk budget per candidate as % of the reference balance | `float` | `0.5` | > 0, <= 5 | no |
| `risk.max_risk_per_trade_usd` | Absolute cap on risk per candidate | `float` | `10.0` | > 0 | no |
| `risk.max_daily_loss_pct` | Simulated daily loss limit (% of reference balance) | `float` | `2.0` | > 0, <= 50 | no |
| `risk.max_drawdown_pct` | Simulated drawdown limit (% of reference balance) | `float` | `10.0` | > 0, <= 90 | no |
| `risk.max_consecutive_losses` | Consecutive simulated losses before rejection | `int` | `5` | >= 1 | no |
| `risk.max_open_positions` | Maximum concurrent simulated positions | `int` | `3` | >= 1 | no |
| `risk.max_usd_leg_exposure` | Maximum open positions with the same USD direction | `int` | `2` | >= 1 | no |
| `risk.max_spread_atr_ratio` | Maximum spread / ATR | `float` | `0.35` | > 0 | no |
| `risk.max_spread_points` | Maximum spread in points by symbol ('*' = default) | `dict[str, float]` | `{'*': 50.0, 'XAUUSD': 60.0}` | - | no |
| `risk.min_reward_risk` | Minimum reward/risk after estimated costs | `float` | `1.0` | > 0 | no |
| `risk.max_data_age_seconds` | Maximum age of the last closed bar in observe mode | `int` | `180` | >= 1 | no |
| `risk.max_model_uncertainty` | Maximum model uncertainty (1 - probability) | `float` | `0.5` | > 0, <= 1 | no |
| `risk.vol_percentile_min` | Lower volatility-percentile bound | `float` | `0.05` | >= 0, < 1 | no |
| `risk.vol_percentile_max` | Upper volatility-percentile bound | `float` | `0.98` | > 0, <= 1 | no |
| `risk.event_window_before_minutes` | Block window before a high-impact event | `int` | `30` | >= 0 | no |
| `risk.event_window_after_minutes` | Block window after a high-impact event / breaking news | `int` | `15` | >= 0 | no |
| `risk.block_on_stale_news` | Reject when news state is unknown (stale/failed/disabled feeds) | `bool` | `True` | - | no |
| `risk.emergency_disable` | Global emergency disable: rejects every candidate | `bool` | `False` | - | no |
| `news.enabled` | Master switch for news collection | `bool` | `True` | - | no |
| `news.providers` | RSS providers: id, url, currencies, category, enabled, min_interval_seconds, store_summary | `list[ProviderConfig]` | `[]` | - | no |
| `news.stale_after_minutes` | A provider without success for this long makes news state unknown | `int` | `180` | >= 1 | no |
| `news.request_timeout_seconds` | HTTP timeout per request | `float` | `10.0` | > 0, <= 120 | no |
| `news.max_retries` | Retries for network errors and HTTP 5xx | `int` | `2` | >= 0, <= 5 | no |
| `news.calendar_csv` | Optional local economic-calendar CSV you are permitted to use | `Path \| None` | `None` | - | no |
| `news.finnhub_api_key` | Reserved for an optional licensed provider (not implemented yet) | `SecretStr \| None` | (none) | - | **yes** |
| `news.newsapi_api_key` | Reserved for an optional licensed provider (not implemented yet) | `SecretStr \| None` | (none) | - | **yes** |
| `llm.enabled` | Enable the optional research adapter | `bool` | `False` | - | no |
| `llm.provider` | 'none' or 'anthropic' | `'none' \| 'anthropic'` | `none` | - | no |
| `llm.model` | Model identifier used by the adapter | `str` | `claude-opus-5-5` | - | no |
| `llm.api_key` | API key (or use ANTHROPIC_API_KEY) | `SecretStr \| None` | (none) | - | **yes** |
| `llm.timeout_seconds` | Request timeout | `float` | `60.0` | > 0, <= 600 | no |
| `llm.max_retries` | SDK retries | `int` | `2` | >= 0, <= 5 | no |
| `llm.daily_cost_limit_usd` | Daily spending cap enforced before each request | `float` | `1.0` | >= 0 | no |
| `llm.cache_ttl_hours` | Reuse identical responses for this long | `int` | `24` | >= 0 | no |
| `llm.input_usd_per_mtok` | Input price used for the cost cap | `float` | `4.0` | >= 0 | no |
| `llm.output_usd_per_mtok` | Output price used for the cost cap | `float` | `20.0` | >= 0 | no |
| `llm.max_output_tokens` | max_tokens per request | `int` | `4000` | >= 256, <= 16000 | no |
| `logging.level` | Log level | `'DEBUG' \| 'INFO' \| 'WARNING' \| 'ERROR'` | `INFO` | - | no |
| `logging.json_format` | JSON log lines (true) or plain text | `bool` | `True` | - | no |
| `monitor.interval_seconds` | Seconds between observe-only monitoring cycles | `int` | `60` | >= 5, <= 3600 | no |
| `bridge.enabled` | Exchange decisions, levels and tick results with the MQL5 indicator and observer EA | `bool` | `True` | - | no |
| `bridge.directory` | Exchange folder; empty uses MT5's Common\Files\mt5bot folder | `Path \| None` | `None` | - | no |
| `bridge.max_decisions` | Most recent decisions exported per symbol and timeframe for the chart | `int` | `300` | >= 10, <= 5000 | no |
| `bridge.import_ticks` | Import tick files recorded by the observer EA into the quotes table | `bool` | `True` | - | no |
