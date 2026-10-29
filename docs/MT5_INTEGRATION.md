# MetaTrader 5 integration

mt5bot uses MetaTrader 5 in two ways. Neither can place, modify, close or cancel an order.

| Layer | Runs | Used for |
|---|---|---|
| **Python `MetaTrader5` API** (`src/mt5bot/gateway/`) | Python process | Connecting to the terminal, checking the demo account and currency, symbol metadata, history download (`copy_rates_range`), tick download (`copy_ticks_range`) |
| **MQL5 components** (`mql5/`) | Inside the MT5 terminal | Drawing decisions and stop/target levels on charts, recording every tick live, deciding tick by tick whether stop-loss or take-profit is hit first, and exporting historical ticks from the terminal UI |

## Why the order of stop and target matters

Bars show only open, high, low and close, so a bar whose range covers **both** the stop-loss and the take-profit cannot tell you which was hit first. Bar-only research must assume one; mt5bot conservatively assumes the **stop**. That understates results whenever the target was really hit first. The bias is largest on M1 scalping, where wide bars often contain both levels.

mt5bot settles these bars with real ticks from three sources, in this priority order:

1. **The live observer EA.** For each watched decision it anchors the stop and target to the first tick after the entry time (ask for longs, bid for shorts, plus configured slippage), then checks every tick. Longs exit on the bid; shorts exit on the ask. The first level touched is written to `resolved_<SYMBOL>.csv`.
2. **Stored ticks.** Ticks recorded by the EA, exported with the script, or collected by `mt5bot ticks collect|ambiguous` are kept in the `quotes` table. `first_touch()` replays the ambiguous bar's ticks.
3. **No ticks available.** The bar stays stop-first. It is flagged `ASSUMED_STOP` and counted in backtest output (`ambiguous=`, `tick_resolved=`, `to_target=`).

A tick that crosses both levels at once (a gap) still counts as the stop.

## Components

| File | Type | What it does |
|---|---|---|
| `mql5/Include/MT5Bot/Bridge.mqh` | include | Shared helpers: file read/append in `Common\Files\mt5bot`, key/value parsing, chart objects |
| `mql5/Indicators/MT5Bot/MT5Bot_Decisions.mq5` | indicator | Arrows for RECOMMEND (buy/sell), REJECTED (×, tooltip lists reason codes) and optionally NO_TRADE/UNCERTAIN; dashed stop-loss, take-profit and entry lines over each holding window; ✓/✗ markers where ticks hit TP/SL first; a status panel (data age, mode, champion, news state, alerts, counts) |
| `mql5/Experts/MT5Bot/MT5Bot_Observer.mq5` | Expert Advisor | **Demo-only** (refuses to start on other accounts) and **observe-only**: records every tick (`CopyTicksRange`, so no tick is skipped between events), tracks watched decisions, draws fill-anchored live levels, writes results and a heartbeat |
| `mql5/Scripts/MT5Bot/MT5Bot_ExportTicks.mq5` | script | Exports the chart symbol's historical ticks for a chosen period, for tick-resolved backtests |

The EA includes no trade library and no order call. `tests/safety/` scans every `.mq5` and `.mqh` file for `OrderSend`, `CTrade`, `Trade.mqh`, `TRADE_ACTION_*`, `Position*` and related calls, and checks that the EA keeps its demo-only guard.

## File exchange

All files live in MT5's shared folder `%APPDATA%\MetaQuotes\Terminal\Common\Files\mt5bot\`. Run `mt5bot bridge path` to print it, or override it with `bridge.directory`.

| File | Writer | Columns / content |
|---|---|---|
| `decisions_<BROKER_SYMBOL>_<TF>.csv` | Python | `id,bar_server,bar_utc,status,regime,strategy,direction,entry_ref,stop,target,hold_bars,tf_minutes,rejections,rationale` |
| `watch_<BROKER_SYMBOL>.csv` | Python | `id,direction,start_server,stop_dist,target_dist,expiry_server,slip_points` |
| `status.txt` | Python | `key=value` lines: updated time, mode, champion, news state, open alerts, `execution=DISABLED` |
| `resolved_<BROKER_SYMBOL>.csv` | EA | `id,entry_msc,entry_price,first_hit,hit_msc,hit_price,ticks`, where `first_hit` is `TP`, `SL`, `NONE` (expired untouched) or `LATE` (EA started after entry) |
| `ticks_<BROKER_SYMBOL>_<YYYYMMDD>.csv` | EA / script | `time_msc,bid,ask` in server time |
| `heartbeat_<BROKER_SYMBOL>.txt` | EA | server time, account mode, spread, watched and resolved counts, version |

Rules for the exchange files:
- **Times:** chart and EA times are broker **server** time. Python converts them with `mt5.server_timezone`, so set that first.
- **Encoding:** files are ASCII with comma-free text fields.
- **Writing:** Python writes atomically (temp file plus rename) and retries while MT5 holds a file open. A partially written last tick line is ignored on import.
- **Idempotent import:** importing again never duplicates rows. Each decision id is resolved once, and ticks are keyed by symbol and time.

## Install (Windows, terminal logged in to the JustMarkets demo)

1. **Copy the files.** In MT5, choose **File → Open Data Folder** and open `MQL5`. Copy this repository's folders into it:
   - `mql5\Include\MT5Bot` → `MQL5\Include\MT5Bot`
   - `mql5\Indicators\MT5Bot` → `MQL5\Indicators\MT5Bot`
   - `mql5\Experts\MT5Bot` → `MQL5\Experts\MT5Bot`
   - `mql5\Scripts\MT5Bot` → `MQL5\Scripts\MT5Bot`
2. **Compile.** Open MetaEditor (F4), open each `.mq5` file and press **F7**. Each should report `0 errors`. If not, see [Troubleshooting](TROUBLESHOOTING.md).
3. **Attach to charts.** For each symbol you research (EURUSD, GBPUSD, USDJPY, XAUUSD), open a chart on M1, M5, M15 or H1 and:
   - drag **MT5Bot_Decisions** onto the chart (indicator);
   - drag **MT5Bot_Observer** onto **one** chart per symbol. It needs no trading permission; leave "Algo Trading" off if you like, because the EA never trades.
4. **Start Python:** run `mt5bot monitor start`. Every cycle it imports EA results and ticks, makes decisions, and exports decisions, watched levels and status. The chart refreshes every few seconds.

## Commands

```powershell
mt5bot bridge path                                     # exchange folder
mt5bot bridge export --mode SIMULATED                  # push current decisions/levels/status to the charts now
mt5bot bridge import                                   # pull EA results, tick files and heartbeats now
mt5bot ticks ambiguous --symbol EURUSD --timeframe M1  # Python API: fetch ticks only for bars that hit both levels
mt5bot ticks collect --symbol EURUSD --from 2024-06-03 --to 2024-06-04
mt5bot backtest --symbol EURUSD --timeframe M1 --tick-resolve
```

## What has and has not been verified

| Item | Status |
|---|---|
| Python file formats, imports, server/UTC conversion, tick resolution logic, monitor bridge sync | **Tested** (`tests/unit/test_bridge_ticks.py`) |
| MQL5 files: no trading API, demo guard, balanced syntax, includes, Python/MQL5 column agreement | **Statically tested** (`tests/safety/test_no_execution_path.py`) |
| MQL5 compilation in MetaEditor, chart rendering, live EA tick resolution | **Not run.** These need MetaEditor and a terminal on your Windows PC. Please report any compile message. |
