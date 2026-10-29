# Feature reference

Generated from `mt5bot.features.pipeline` (feature-set version 1.0.0, hash `58e2404ac187b778`). Regenerate with `mt5bot features docs`.

Timing: a bar opening at *t* closes at *t + timeframe*; its features become available at that close. Higher-timeframe (`htf_*`) features come from the last higher-timeframe bar that had **closed** by then (M1→M5, M5→M15, M15→H1; H1 has none). Warm-up rows are NaN and are dropped before training.

| Feature | Family | Calculation | Warm-up (bars) | Inputs |
|---|---|---|---|---|
| `ret_1` | price | log return over 1 bar | 1 | close |
| `ret_5` | price | log return over 5 bars | 5 | close |
| `ret_15` | price | log return over 15 bars | 15 | close |
| `body_ratio` | price | (close-open)/(high-low); 0 for zero-range bars | 0 | open, high, low, close |
| `upper_wick_ratio` | price | (high-max(open,close))/(high-low); 0 for zero-range bars | 0 | open, high, low, close |
| `lower_wick_ratio` | price | (min(open,close)-low)/(high-low); 0 for zero-range bars | 0 | open, high, low, close |
| `range_atr` | price | (high-low)/ATR14 | 14 | high, low, close |
| `consec_up` | price | consecutive bars with close>open | 0 | open, close |
| `consec_down` | price | consecutive bars with close<open | 0 | open, close |
| `dist_high_20` | price | (max high of last 20 bars incl. current - close)/ATR14 | 19 | high, close |
| `dist_low_20` | price | (close - min low of last 20 bars incl. current)/ATR14 | 19 | low, close |
| `breakout_up_20` | price | (close - highest high of previous 20 bars)/ATR14 | 20 | high, close |
| `breakout_dn_20` | price | (lowest low of previous 20 bars - close)/ATR14 | 20 | low, close |
| `mom_10` | momentum | (close - close 10 bars ago)/ATR14 | 14 | close |
| `accel_10_5` | momentum | mom_10 - mom_10 five bars earlier | 19 | close |
| `rsi_14` | momentum | RSI(14), Wilder smoothing | 14 | close |
| `macd_hist_atr` | momentum | MACD(12,26,9) histogram / ATR14 | 33 | close |
| `macd_above_signal` | momentum | 1 if MACD line > signal else 0 | 33 | close |
| `ema_20_50_atr` | trend | (EMA20 - EMA50)/ATR14 | 49 | close |
| `ema20_slope_atr` | trend | (EMA20 - EMA20 five bars earlier)/ATR14 | 24 | close |
| `close_ema20_atr` | trend | (close - EMA20)/ATR14 | 19 | close |
| `adx_14` | trend | ADX(14) | 27 | high, low, close |
| `plus_di_14` | trend | +DI(14) | 14 | high, low, close |
| `minus_di_14` | trend | -DI(14) | 14 | high, low, close |
| `atr_14` | volatility | ATR(14) in price units | 14 | high, low, close |
| `atr_pct` | volatility | ATR14 / close | 14 | high, low, close |
| `rv_30` | volatility | std of 1-bar log returns over 30 bars | 30 | close |
| `vol_pctile` | volatility | percentile of atr_pct within the trailing 500 bars | 513 | high, low, close |
| `range_expansion` | volatility | mean range of last 5 bars / mean range of last 50 | 49 | high, low |
| `bb_width` | volatility | Bollinger(20,2) width / middle band | 19 | close |
| `spread_points` | cost | broker-reported spread in points (bar value) | 0 | spread_points |
| `spread_atr` | cost | spread in price / ATR14 | 14 | spread_points, high, low, close |
| `tickvol_z` | cost | z-score of tick count vs. previous 50 bars (tick count, not traded volume) | 50 | tick_volume |
| `hour_utc` | session | UTC hour of the bar open | 0 | time_utc |
| `dow` | session | day of week of the bar open (0=Mon) | 0 | time_utc |
| `in_asia` | session | 1 if bar open hour in [0,8) UTC | 0 | time_utc |
| `in_london` | session | 1 if bar open hour in [7,16) UTC | 0 | time_utc |
| `in_new_york` | session | 1 if bar open hour in [12,21) UTC | 0 | time_utc |
| `session_open_30m` | session | 1 if any configured session opened within the last 30 minutes | 0 | time_utc |
| `htf_ema_20_50_atr` | cross_tf | higher-TF (EMA20-EMA50)/ATR14 of the last CLOSED higher bar | 49 | close |
| `htf_rsi_14` | cross_tf | higher-TF RSI14 of the last closed higher bar | 14 | close |
| `htf_adx_14` | cross_tf | higher-TF ADX14 of the last closed higher bar | 27 | close |
| `htf_close_ema20_atr` | cross_tf | (close - higher-TF EMA20)/higher-TF ATR14 | 19 | close |
| `htf_alignment` | cross_tf | sign(ema_20_50_atr) * sign(htf_ema_20_50_atr): +1 aligned, -1 opposed | 49 | close |
