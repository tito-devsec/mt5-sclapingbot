# Skill catalogue

Generated from `mt5bot.agent.catalogue` with `mt5bot agent catalogue --out docs/SKILLS.md`.

**108 capabilities are implemented in code with tests; 119 are planned and do not exist yet.** A capability is something the agent can compute or do. Whether a trading technique actually *works* is a separate question, answered per market context by the skill book (`mt5bot agent skills`).

## A. Market structure

| Implemented | Where |
|---|---|
| Swing-high and swing-low detection (confirmed 3 bars later) | `features/structure.py` |
| Higher-high/higher-low and lower-high/lower-low structure trend | `features/structure.py: struct_trend` |
| Range and consolidation identification | `regime/rules.py: RANGE, LOW_VOL` |
| Compression, expansion and contraction | `features: range_expansion, bb_width` |
| Multi-timeframe alignment | `features: htf_alignment` |
| Trend continuation | `techniques: trend_pullback` |

**Planned:** Break of Structure; Change of Character; Market Structure Shift; internal vs external structure; major vs minor structure; trend exhaustion; trend reversal; impulsive vs corrective moves.

## B. Candlesticks

| Implemented | Where |
|---|---|
| Bullish and bearish engulfing | `features/structure.py` |
| Pin bars (hammer and shooting-star shapes) | `features/structure.py` |
| Doji | `features/structure.py` |
| Inside bars | `features/structure.py` |
| Body and wick analysis | `features: body_ratio, wick ratios` |
| Pattern context at support, resistance and extremes | `techniques: sr_bounce, pinbar_extreme` |
| Pattern significance by regime and session | `agent/skills.py (per-context evidence)` |

**Planned:** hanging man; dragonfly and gravestone doji; morning and evening star; three white soldiers; three black crows; outside bars; marubozu; tweezers; harami; piercing line; dark cloud cover; three-bar reversals; candle-volume relationships.

## C. Technical indicators

| Implemented | Where |
|---|---|
| SMA, EMA | `features/indicators.py` |
| ADX and DMI | `features/indicators.py` |
| RSI | `features/indicators.py` |
| MACD line, signal and histogram | `features/indicators.py` |
| ATR, Bollinger width, Donchian channels | `features/indicators.py` |
| Realized volatility and volatility percentiles | `features/indicators.py` |
| GARCH volatility forecast (research) | `features/garch.py` |
| Feature importance | `ml/explain.py (SHAP)` |
| Reliability by regime, instrument and session | `agent/skills.py (evidence per context)` |

**Planned:** WMA, VWMA, HMA; Aroon, Supertrend, Parabolic SAR; Stochastic, CCI, ROC, Williams %R; MACD divergence; Keltner channels; OBV, MFI, Chaikin Money Flow, volume profile; pivot points; Fibonacci levels; indicator lag and redundancy detection; leak-free indicator parameter optimisation.

## D. Support, resistance and liquidity

| Implemented | Where |
|---|---|
| Horizontal support and resistance from confirmed swings | `features/structure.py` |
| Distance-to-level measurements | `features: sr_res_dist_atr, sr_sup_dist_atr` |
| Rejection from levels | `techniques: sr_bounce` |
| 20-bar range extremes | `features: dist_high_20, dist_low_20` |

**Planned:** previous-day and previous-week high and low; daily and weekly open; session highs and lows; round-number levels; supply and demand zones; support/resistance flips; equal highs and lows; liquidity sweeps; false breakouts; level strength and retests; level invalidation.

## E. Trendlines and geometric patterns

| Implemented | Where |
|---|---|
| Least-squares trendline with slope | `features/structure.py: tl_slope_atr` |
| Trendline breaks | `techniques: trendline_break` |

**Planned:** trendline retests; channels; triangles; wedges; flags and pennants; head and shoulders; double and triple tops and bottoms; rectangles; pattern failure recognition.

## F. Smart Money Concepts (optional specialists)

Planned as testable price-behaviour hypotheses only; candles never show real institutional order flow.

**Planned:** order blocks; breaker and mitigation blocks; fair value gaps; liquidity voids; liquidity grabs; premium and discount zones; displacement candles; optimal trade entry; dealing ranges.

## G. Forex and gold

| Implemented | Where |
|---|---|
| Pip, point and contract specifications from the broker | `gateway, storage: instrument_specs` |
| Lot-size and exposure calculation | `backtest/simulator.py, risk/engine.py` |
| Currency conversion for USD-quoted and USD-base pairs | `backtest/simulator.py` |
| Swap and financing costs | `backtest/simulator.py (configured values)` |
| Broker symbol names | `gateway/symbols.py` |
| Correlated USD exposure | `risk/engine.py` |
| Per-instrument spread limits | `risk/engine.py` |

**Planned:** currency strength; pair correlation and correlation breakdown; cross-pair confirmation; gold-dollar relationship.

## H. Trading sessions

Session hours are configured in UTC today; a daylight-saving-aware session calendar is planned.

| Implemented | Where |
|---|---|
| Asia, London and New York session flags | `features: in_asia, in_london, in_new_york` |
| Session-open window | `features: session_open_30m; techniques: session_momentum` |
| Weekday and hour features | `features: dow, hour_utc` |
| Broker server time with daylight saving | `gateway/clock.py` |
| Session-specific evidence | `agent/skills.py (session is part of every skill context)` |

**Planned:** Sydney session; London-New York overlap as its own state; Asian-range breakout; London breakout; daylight-saving-aware session calendars; holiday and shortened sessions.

## I. News and macro

| Implemented | Where |
|---|---|
| Official central-bank feeds (Fed, ECB, BoE) | `news/providers/rss.py` |
| Economic calendar import | `news/providers/calendar_csv.py` |
| Scheduled events vs. breaking news | `news/event_filter.py` |
| Relevance by instrument | `news/relevance.py` |
| Publication vs. retrieval time (no look-ahead) | `news/models.py: available_at` |
| Feed freshness: an outage means unknown, never no news | `news/event_filter.py` |
| Baseline sentiment | `news/sentiment.py` |
| Event-window risk | `risk/engine.py` |

**Planned:** actual vs. forecast analysis; per-indicator interpretation (CPI, NFP, PMI, GDP); risk-on/risk-off state; news-driven continuation and reversal; licensed news APIs.

## J. Scalping techniques

Each technique is crossed with stop/target/hold profiles; none has an edge until its skill is VALIDATED.

| Implemented | Where |
|---|---|
| EMA pullback / trend continuation | `techniques: trend_pullback` |
| Donchian breakout | `techniques: donchian_breakout` |
| RSI reversion in quiet markets | `techniques: rsi_reversion` |
| Support/resistance rejection | `techniques: sr_bounce` |
| Engulfing with structure | `techniques: engulfing_trend` |
| Pin bar at extremes | `techniques: pinbar_extreme` |
| Session-open momentum | `techniques: session_momentum` |
| Trendline breakout | `techniques: trendline_break` |
| Inside-bar breakout | `techniques: inside_bar_break` |
| MACD momentum | `techniques: macd_momentum` |
| News-event avoidance | `risk/engine.py` |

**Planned:** moving-average crossover; Bollinger squeeze breakout; ATR expansion breakout; Asian-range breakout; retest continuation; liquidity-sweep reversal; fair-value-gap retracement; failed-breakout reversal; news-momentum continuation.

## K. Stop-loss and take-profit

| Implemented | Where |
|---|---|
| ATR-based stops and targets in several shapes | `strategies/techniques.py: EXIT_SHAPES` |
| Fixed-risk position sizing | `backtest/simulator.py` |
| Reward/risk and minimum reward/risk after costs | `risk/engine.py` |
| Maximum holding-time exit | `backtest/simulator.py` |
| Spread and slippage checks | `risk/engine.py` |
| Gap-through-stop handling | `backtest/simulator.py: STOP_GAP` |
| Stop vs. target order from real ticks | `backtest/ticks.py, MQL5 observer EA` |
| Exit-quality analysis (MAE, MFE, attribution) | `memory/outcomes.py` |
| Learning which stop/target shape works per context | `agent/skills.py` |

**Planned:** swing and structure-based stops; break-even management; trailing stops; chandelier exits; partial take-profit; session-end and news-risk exits.

## L. Risk and capital management

| Implemented | Where |
|---|---|
| Risk per trade | `risk/engine.py` |
| Daily loss limit (halts new recommendations) | `risk/engine.py` |
| Maximum drawdown | `risk/engine.py` |
| Consecutive-loss control | `risk/engine.py` |
| Maximum and correlated exposure | `risk/engine.py` |
| Emergency disable | `risk/engine.py` |
| Daily profit goal for reporting | `risk settings, agent status` |

**Planned:** weekly loss limits; margin and free-margin monitoring; volatility-adjusted sizing; risk reduction after losses; risk-of-ruin estimate; strategy-specific risk budgets.

## M. Machine learning

| Implemented | Where |
|---|---|
| Supervised classification with calibration | `ml/training.py` |
| Gradient-boosted ensembles | `ml/models.py (LightGBM, XGBoost, CatBoost)` |
| Online learning after every outcome | `agent/recall.py: OnlineLearner (river)` |
| Similarity retrieval over embeddings | `memory/vector.py, agent/recall.py` |
| Contextual bandit (offline replay) | `selection/bandit.py` |
| Unsupervised regimes (HMM) and change points | `regime/hmm.py, regime/changepoints.py` |
| Walk-forward and purged time-series validation | `evaluation/splits.py` |
| Hyper-parameter optimisation without test leakage | `ml/tuning.py` |
| Concept and data drift detection | `drift/` |
| Out-of-distribution detection | `regime/rules.py` |
| Uncertainty by conformal prediction | `ml/training.py` |
| Champion/challenger, versioning, rollback, fingerprints | `ml/registry.py, ml/promotion.py` |
| Explainability | `ml/explain.py` |

**Planned:** reinforcement-learning research; anomaly detection; time-series forecasting; label-quality assessment; regime-specific model selection.

## N. Autonomy and self-development

New techniques are added as reviewed, versioned code; the agent never writes and runs code by itself.

| Implemented | Where |
|---|---|
| Scheduled observation and data collection | `monitor/service.py` |
| Eligibility, selection and NO_TRADE decisions | `agent/policy.py` |
| Learning cycle after outcomes close | `agent/core.py: learn (LangGraph when installed)` |
| Skill testing, promotion and retirement | `agent/skills.py` |
| Strategy suspension on drift | `drift/monitor.py` |
| Daily learning summaries | `agent/knowledge.py` |
| Memory consolidation into a cached playbook | `agent/knowledge.py` |
| Recovery after restarts (checkpoints, idempotent learning) | `ingest/, agent/core.py` |
| Data-quality monitoring | `ingest/quality.py` |

**Planned:** hypothesis generation; skill creation proposals; weekly and monthly reviews; knowledge-conflict detection; automatic alerts.

## O. Human-trade observation (opt-in, read-only)

Off by default. Nothing is copied, modified, closed or shared.

| Implemented | Where |
|---|---|
| Closed manual trades with symbol, direction, times and result | `agent/user_trades.py` |
| Market context at entry | `agent/user_trades.py` |
| Kept apart from the agent's own evidence | `agent/core.py: source USER never validates a skill` |

**Planned:** open-position detection; stop-loss and take-profit tracking; partial closures; context at exit; comparison with the agent's own decision; private performance feedback.

## P. Memory, RAG and CAG

| Implemented | Where |
|---|---|
| Episodic memory of every evaluated outcome | `storage: episodes` |
| Market-state embeddings | `memory/embed.py` |
| pgvector (HNSW) and SQLite vector backends | `memory/vector.py` |
| Similar-situation retrieval with metadata and time filters | `agent/recall.py` |
| Lesson embeddings and semantic lookup | `agent/knowledge.py` |
| Procedural memory of skills with evidence | `storage: skills, skill_events` |
| Cached, versioned playbook (CAG) | `agent/knowledge.py: playbook_cache` |
| Provenance and timestamps on every memory | `storage: episodes, lessons` |

**Planned:** relevance decay and expiry; knowledge deduplication; contradiction detection; context packs per instrument; multi-user isolation and consent-based sharing; backup and recovery procedures; relational records in PostgreSQL (they stay in SQLite today).
