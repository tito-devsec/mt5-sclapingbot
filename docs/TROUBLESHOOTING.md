# Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `TERMINAL_UNAVAILABLE: the MetaTrader5 package is not installed` | Not on Windows, or the package is missing | Run on Windows with `pip install -e ".[all]"`. The package only ships Windows wheels. |
| `TERMINAL_UNAVAILABLE: MT5 initialize failed (error code ...)` | The terminal is not running, is not logged in, or the path is wrong | Start the MT5 terminal and log in to the JustMarkets **demo** account, or set `MT5BOT_MT5__TERMINAL_PATH`. |
| `ACCOUNT_NOT_DEMO` | The terminal is logged in to a live or contest account | Log in to a demo account. The guard cannot be disabled. |
| `ACCOUNT_CURRENCY_MISMATCH` | The account currency is not `mt5.expected_currency` | Use a USD demo account, or change the expected currency only if that is really your setup. |
| `SYMBOL_AMBIGUOUS` | The broker offers several variants, such as `EURUSD` and `EURUSD.m` | Set the one you want under `[mt5] symbol_map = { EURUSD = "EURUSD.m" }`. |
| `SYMBOL_UNRESOLVED` | The symbol is not offered, or is named very differently | Check Market Watch → Symbols in the terminal, then map it explicitly. |
| `SERVER_TIME_UNKNOWN` | Server time zone not configured | Run `mt5bot check` during market hours to see the current offset. Set `mt5.server_timezone` (`"NY+7"` if the offset is +2 in winter and +3 in summer, or an IANA zone), or `mt5.server_utc_offset_hours`. |
| `UNMAPPABLE_SERVER_TIME` data-quality issues | Bars fall in a DST gap or overlap of the configured zone | Expected for an hour around DST changes. Bars are rejected, never guessed. If you see many, re-check the time zone setting. |
| `GAP_UNEXPECTED` warnings | Missing history from the broker or a terminal disconnect | Re-run `collect` (it resumes from the checkpoint). Gaps are reported and never filled. |
| Very little history collected | The terminal has not downloaded history | Increase "Max bars in chart" in MT5 Options → Charts, open the symbol's chart once, then collect again. |
| Every decision is `REJECTED` with `RISK_NEWS_STATE_UNKNOWN` | No news provider is enabled, or providers are failing | See `docs/RISK.md` → "Stale news". |
| `train` reports `SKIPPED` | Too few labelled candidates | Collect more history, or use a lower timeframe. `ml.min_train_samples` sets the minimum. |
| Every challenger is `REJECTED` | Gates failed (see the printed gate table) | This is the expected protection when there is no out-of-sample edge after costs. Do not loosen gates just to promote a model. |
| `pip install` fails on a library | Unsupported Python version | Use Python 3.12 (`py -3.12 -m venv .venv`). |
| `sqlite3.OperationalError: database is locked` | Two long-running writers at once | Run one `monitor` per database. Short commands can run alongside it, because WAL mode is enabled. |
| `PermissionError` on `data/` | The working folder is not writable | Run from a folder you own, or set `storage.db_path`. |
| MQL5 compile errors | Not applicable | This project has no MQL5 code yet. |
