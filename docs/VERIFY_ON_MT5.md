# Verify on your PC with MetaTrader 5 (rules-based, at least two trades)

This walkthrough checks the whole chain on your JustMarkets **demo** terminal:

- **Python:** connection, account check, data collection, and the rule-based decisions.
- **MT5:** chart drawing, plus the observer EA resolving each trade's stop-loss and take-profit on live ticks.

A "trade" here is a **paper trade**: the bot records it, draws it, and the EA tracks it tick by tick. **No order is sent to the broker.**

Run it while the market is open (Monday 00:00 to Friday ~21:00 UTC). M1 trades on EURUSD usually appear within an hour, but rules only fire when their conditions are met.

## 1. Install Python 3.12 and the project (PowerShell)

```powershell
py -3.12 --version
git clone https://github.com/tito-devsec/mt5-sclapingbot.git
cd mt5-sclapingbot
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[all,dev]"
python -m pytest -q          # expect all tests to pass
```

## 2. Prepare MetaTrader 5

1. Open MT5 and log in to your **JustMarkets demo** account. File → Login to Trade Account; the account type must say Demo.
2. Open a **EURUSD M1** chart.
3. Copy the MQL5 files. Choose File → Open Data Folder, open `MQL5`, and copy:
   - `mql5\Include\MT5Bot` → `MQL5\Include\MT5Bot`
   - `mql5\Indicators\MT5Bot` → `MQL5\Indicators\MT5Bot`
   - `mql5\Experts\MT5Bot` → `MQL5\Experts\MT5Bot`
   - `mql5\Scripts\MT5Bot` → `MQL5\Scripts\MT5Bot`
4. Compile. In MetaEditor (F4), open `MT5Bot_Decisions.mq5`, `MT5Bot_Observer.mq5` and `MT5Bot_ExportTicks.mq5` and press **F7** on each. Every file should end with `0 errors`. **If you see errors, copy the full message (file, line, text) and send it to me.**
5. In the Navigator, refresh with right-click → Refresh, then drag onto the EURUSD M1 chart:
   - **Indicators → MT5Bot → MT5Bot_Decisions**
   - **Expert Advisors → MT5Bot → MT5Bot_Observer**. It says "observe-only" and needs no trading permission, so you can leave Algo Trading off.

   The chart's top-left should show "MT5Bot Observer (observe-only, demo)".

## 3. Configure (rules-based mode, news on)

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force config | Out-Null
mt5bot check
```

`mt5bot check` must show:
- `[OK ] ACCOUNT_DEMO`
- `[OK ] ACCOUNT_CURRENCY_OK`
- the broker name for EURUSD, which may have a suffix
- `server offset now=UTC+2h` or `+3h`

Create `config\local.toml` with this content. Use `"NY+7"` if the offset is +2 in winter or +3 in summer; otherwise use the offset `check` printed in `server_utc_offset_hours`.

```toml
[mt5]
expected_currency = "USD"
server_timezone = "NY+7"

[instruments]
symbols = ["EURUSD"]
timeframes = ["M1"]

[selection]
mode = "rules"

[news]
enabled = true

[[news.providers]]
id = "fed_press"
url = "https://www.federalreserve.gov/feeds/press_all.xml"
currencies = ["USD"]
category = "central_bank"
enabled = true

[[news.providers]]
id = "ecb_press"
url = "https://www.ecb.europa.eu/rss/press.html"
currencies = ["EUR"]
category = "central_bank"
enabled = true

[[news.providers]]
id = "boe_news"
url = "https://www.bankofengland.co.uk/rss/news"
currencies = ["GBP"]
category = "central_bank"
enabled = true

[logging]
level = "INFO"
json_format = false
```

If `check` showed a suffixed name, such as `EURUSD -> EURUSD.m`, nothing else is needed. If it reported `SYMBOL_AMBIGUOUS`, add `symbol_map = { EURUSD = "EURUSD.m" }` under `[mt5]`.

## 4. Collect history and check it

```powershell
mt5bot check                         # must now end without FAIL lines
mt5bot collect --from 2026-09-01     # about two months of M1 bars
mt5bot inspect --symbol EURUSD
mt5bot news refresh                  # all three feeds should say ok=True
mt5bot backtest --symbol EURUSD --timeframe M1     # rule baseline after costs (SIMULATED)
```

## 5. Run the monitor and watch the chart

```powershell
mt5bot monitor start
```

Every minute the monitor:
1. collects the newest closed bar
2. refreshes the news feeds
3. runs the rules and the risk engine
4. writes the decision files for the chart
5. imports the EA's tick results

When a rule fires and the risk engine approves it:

- A **buy or sell arrow** appears with dashed **SL (red)**, **TP (green)** and entry lines across the holding window. The indicator draws these.
- Solid live SL/TP lines anchored to the actual tick fill appear. The EA draws these.
- When ticks reach one level first, a **✓ (take-profit)** or **✗ (stop-loss)** marker appears. That level is recorded in `resolved_EURUSD.csv`.
- A gray **×** means the risk engine rejected the candidate. Hover over it to see the reason codes.

Leave both the monitor and the EA running. Stop the monitor with Ctrl+C.

## 6. Confirm at least two trades

In a second PowerShell window, in the same folder, with the virtual environment activated:

```powershell
mt5bot verify
```

It prints the EA heartbeat (`trade_mode=DEMO`) and each rules-based trade with its tick verdict, for example:

```
#12 EURUSD M1 RECOMMEND trend LONG bar ... -> TP at ... fill=1.08412 exit=1.08562 ticks=311
#15 EURUSD M1 RECOMMEND breakout SHORT bar ... -> SL at ... fill=1.08390 exit=1.08490 ticks=97
2 trade(s) resolved by live ticks (paper tracking only; no order is ever sent).
```

The command exits with code 0 once at least two trades are resolved. Then run:

```powershell
mt5bot outcomes      # evaluates matured decisions (uses the EA's tick verdicts for SL/TP ties)
mt5bot status
mt5bot report        # reports\report.html
```

## If no trade appears

| What you see | Why | What to do |
|---|---|---|
| Many `REJECTED` with `RISK_NEWS_STATE_UNKNOWN` | News feeds are failing or not enabled | Run `mt5bot news refresh`. All three providers must show `ok=True`. |
| Mostly `UNCERTAIN` | The regime rules see mixed or out-of-range conditions | Normal in quiet markets. Wait, or add `M5` to `timeframes` and attach the EA and indicator to an M5 chart too. |
| Mostly `NO_TRADE` | No strategy rule fired on that bar | Normal. Rules only fire on their specific setups. |
| `RISK_SPREAD_TOO_WIDE` / `RISK_SPREAD_ATR_TOO_HIGH` | The spread is too large relative to the M1 range | Expected around rollover (about 21:00–23:00 UTC). Try again during the London or New York session. |
| `first_hit=LATE` | The EA was attached after the trade's entry time | Keep the EA attached continuously. |
| Indicator panel says STALE | The monitor is not running, or the time zone is wrong | Restart `mt5bot monitor start` and re-check `server_timezone`. |

Send me the output of `mt5bot check`, `mt5bot verify` and any MetaEditor messages, and I'll fix anything that does not work.
