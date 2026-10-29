# Risk controls

The risk engine (`src/mt5bot/risk/engine.py`) evaluates **research proposals and simulated trades only**. It has no ability to place, modify, close or cancel orders.

## Principles

- **Independent.** The only model output it receives is an uncertainty value. The selector and the optional LLM adapter cannot change its limits.
- **Fail closed.** If any check cannot be computed, the candidate is rejected with `RISK_CHECK_INCOMPLETE`.
- **Audited.** Every check, passed or failed, is stored in `risk_evaluations` with a reason code.
- **No universal limits.** The defaults below are conservative starting assumptions, not limits proven safe.

## Checks and reason codes

| Check | Rejection code | Setting |
|---|---|---|
| Global emergency disable | `RISK_EMERGENCY_DISABLED` | `risk.emergency_disable` |
| Demo account, account currency (observe mode) | `RISK_ACCOUNT_UNAVAILABLE`, `RISK_ACCOUNT_NOT_DEMO`, `RISK_ACCOUNT_CURRENCY` | `mt5.expected_currency` |
| Missing or invalid inputs | `RISK_DATA_INCONSISTENT` | - |
| Data freshness (observe mode) | `RISK_DATA_STALE` | `risk.max_data_age_seconds` |
| Spread in points | `RISK_SPREAD_TOO_WIDE` | `risk.max_spread_points` |
| Spread / ATR | `RISK_SPREAD_ATR_TOO_HIGH` | `risk.max_spread_atr_ratio` |
| Volatility percentile bounds | `RISK_VOLATILITY_OUT_OF_BOUNDS` | `risk.vol_percentile_min/max` |
| High-impact event window | `RISK_EVENT_WINDOW` | `risk.event_window_before/after_minutes` |
| Recent relevant breaking news | `RISK_BREAKING_NEWS` | `risk.event_window_after_minutes` |
| Unknown news state | `RISK_NEWS_STATE_UNKNOWN` | `risk.block_on_stale_news` (default **true**) |
| Net reward/risk after costs | `RISK_REWARD_RISK_TOO_LOW` | `risk.min_reward_risk` |
| Position size at broker minimum | `RISK_SIZE_BELOW_MINIMUM` | - |
| Risk per candidate (% and USD) | `RISK_PER_TRADE_TOO_HIGH` | `risk.risk_per_trade_pct`, `risk.max_risk_per_trade_usd` |
| Simulated daily loss | `RISK_DAILY_LOSS_LIMIT` | `risk.max_daily_loss_pct` |
| Simulated drawdown | `RISK_MAX_DRAWDOWN` | `risk.max_drawdown_pct` |
| Consecutive losses | `RISK_CONSECUTIVE_LOSSES` | `risk.max_consecutive_losses` |
| Concurrent simulated positions | `RISK_MAX_OPEN_POSITIONS` | `risk.max_open_positions` |
| Same-direction USD exposure | `RISK_CORRELATED_EXPOSURE` | `risk.max_usd_leg_exposure` |
| Model uncertainty | `RISK_MODEL_UNCERTAINTY` | `risk.max_model_uncertainty` |
| Suspended strategy | `RISK_STRATEGY_SUSPENDED` | drift alerts |

Some inputs need explaining:

- **Net reward/risk** = (target_R − cost_R) ÷ (1 + cost_R), where cost_R is the estimated spread, 2× slippage and commission, expressed in R.
- **Model uncertainty** is 1 − p when a champion model is used, and a fixed, documented 0.5 under the evidence-only baseline.
- **Daily loss, drawdown and consecutive losses** are computed from recorded simulated outcomes, using R × risk budget against the **configured reference balance**. They are not broker balances.

## Account guard

`collect`, `recommend --observe` and `monitor start` refuse to run unless the terminal reports a **DEMO** account in the expected currency. The monitor re-checks this every cycle and stops on failure.

`mt5.require_demo` accepts only `true`. Do not try to work around these checks.

## Stale news and the default configuration

`block_on_stale_news = true` treats unknown news state as a reason to reject. With every news provider disabled (the packaged default), **every candidate is rejected with `RISK_NEWS_STATE_UNKNOWN`**.

Either enable providers after reviewing their terms, or set `risk.block_on_stale_news = false` knowingly, for example for pure historical research.
