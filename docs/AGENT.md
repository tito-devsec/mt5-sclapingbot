# The learning agent

The agent turns every evaluated outcome into memory, tracks which techniques work in which market conditions, and recommends only what it has confirmed. It is **research software**: it never sends, changes or closes an order, and it promises no profit.

Turn it on with `selection.mode = "agent"` in `config/local.toml`.

## How it learns

```mermaid
flowchart TD
    C["Every closed bar: 10 entry rules x stop/target/hold profiles"] --> D["Decision recorded with all candidates"]
    D --> O["Horizon closes: every rule-eligible candidate is simulated after costs"]
    O --> E["Episode stored (episodic memory) + market-state vector"]
    E --> S["Skill book updated (procedural memory)"]
    E --> M["Online model updated (test first, then train)"]
    E --> L["Daily lessons written and embedded (semantic memory)"]
    S --> P["Playbook cache of validated skills (CAG, working memory)"]
    P --> POL["Policy: validated skill + similar past episodes (RAG) + online model"]
    M --> POL
    POL --> R["Independent risk engine"]
    R --> OUT["BUY_CANDIDATE / SELL_CANDIDATE / NO_TRADE / UNCERTAIN / RISK_REJECTED"]
```

Because every eligible candidate is evaluated, whether or not it was selected, the agent learns from setups it did **not** take. It never needs to gamble to explore.

## The four memories

| Memory | What it holds | Where |
|---|---|---|
| Episodic | Every evaluated outcome, with the market state, technique, regime, session and result | `episodes` table + vectors |
| Semantic | Daily lessons with their evidence, sample size, context and confidence, each marked as an observation | `lessons` table + vectors |
| Procedural | Skills (technique × symbol × timeframe × regime × session), with evidence and status history | `skills`, `skill_events` |
| Working | The current playbook of validated skills, plus the live decision context | `playbook_cache` |

## Vector memory: pgvector or SQLite

| Backend | Use it when | Search |
|---|---|---|
| `sqlite` (default) | Getting started, offline work, tests | Exact cosine similarity in memory |
| `pgvector` | Long-running use and large memories | HNSW index in PostgreSQL 16 with the `vector` extension |

Vectors and their metadata live in the chosen backend. **Relational records (decisions, skills, episodes, lessons) stay in SQLite for now**; moving them to PostgreSQL is planned.

To use pgvector:

```powershell
$env:MT5BOT_PG_PASSWORD = "choose-a-password"
docker compose up -d
```

Then set in `.env`: `MT5BOT_MEMORY__DSN=postgresql://mt5bot:choose-a-password@127.0.0.1:5432/mt5bot`, and in `config/local.toml`:

```toml
[memory]
backend = "pgvector"
```

If pgvector is configured but unreachable, the agent stops with an error. It never falls back silently. Switching backends does not copy existing vectors; episodes stay in SQLite and can be re-embedded.

## Embeddings

- **Market state** (`memory/embed.py`): a fixed 42-dimension vector built from trend, momentum, volatility, structure, candlestick, session, hour and regime values. It is hand-specified, not trained, so stored vectors never need re-embedding.
- **Lesson text**: a deterministic hashing embedder (word 1–2 grams). It needs no model download and works offline. A neural text model is planned.

## RAG and CAG

- **RAG (retrieval):** before each decision, the agent retrieves the most similar past episodes for the same symbol and direction that were **already known at that moment**. If similar episodes for the same entry rule lost on average with enough evidence, the candidate is vetoed (`MEMORY_NEGATIVE`). Similarity is supporting evidence only; it never approves a trade by itself.
- **CAG (cache):** validated skills, skill counts and recent lessons are precomputed into a versioned playbook. The policy reads it in memory; the playbook is rewritten only when its content changes.

No language model is called for any decision.

## Skill lifecycle

| Status | Meaning | Moves when |
|---|---|---|
| `EXPERIMENTAL` | Being observed; never recommended | ≥ 30 outcomes and the lower confidence bound (z = 3.0) of mean reward is above 0 → `PROBATION` |
| `PROBATION` | Discovered; must repeat on **fresh** outcomes | ≥ 20 new outcomes with lower bound (z = 1.5) above 0 → `VALIDATED`; otherwise back to `EXPERIMENTAL` |
| `VALIDATED` | May be recommended | Results since validation turn negative → `RETIRED` |
| `RETIRED` | Not used | After a 14-day cooldown it restarts as `EXPERIMENTAL` and must earn it again |

Reward is the net result in R after spread, slippage, commission and swap (see [ML_GUIDE.md](ML_GUIDE.md)).

**How strict is this?** Measured on 1,500 simulated skills per case:

| True edge per trade | Share ever validated |
|---|---|
| None (pure noise) | 0.33% |
| −0.1R (typical after costs) | 0% |
| +0.15R | 18% within 400 outcomes |
| +0.3R | 80% within 400 outcomes |

With thousands of skills under observation, a few false ones will still be validated. They are caught by retirement when their results turn negative.

"Hundreds of skills" is a possible outcome, not a setting. With 120 technique variants, 4 symbols, 4 timeframes and several regimes and sessions, thousands of skills are observed. How many become `VALIDATED` depends entirely on real evidence. It may be few, or none.

## Techniques

Ten entry rules (`strategies/techniques.py`): `trend_pullback`, `donchian_breakout`, `rsi_reversion`, `sr_bounce`, `engulfing_trend`, `pinbar_extreme`, `session_momentum`, `trendline_break`, `inside_bar_break`, `macd_momentum`.

Each is tested with six ATR stop/target shapes (1:1.5, 1:2, 1:3, 1.5:2.25, 1.5:3, 2:3) and the holding times in `agent.hold_minutes`. So the agent also learns which stop-loss and take-profit shape suits each condition.

The full list of what exists and what is only planned is in [SKILLS.md](SKILLS.md).

## Online model

A logistic model (river) is updated after every outcome, test-first-then-train. It is used only while its log loss beats the base rate by `agent.online_min_gain`. A candidate is dropped when the model's expected value is negative: `p × reward/risk − (1 − p) < 0`. The model is rebuilt from stored episodes at start-up, so no pickled file is ever loaded.

## Adding a trained model (LightGBM and others)

The agent works without any trained model. When enough outcomes exist, you can add one as an extra filter:

```powershell
pip install -e ".[boost]"
mt5bot train --symbols EURUSD --timeframe M5 --model lightgbm   # run with selection.mode = "agent"
mt5bot models list
```

```toml
[agent]
use_champion_model = true
```

- In agent mode, training uses the same technique candidates the agent decides on. The model sees the market features plus the entry rule, reward/risk, stop size and holding time.
- Only a model that passed every promotion gate (the **champion**) is used. A setup is dropped when the model's probability is below its threshold (`CHAMPION_BELOW_THRESHOLD`) or its conformal check is not confident (`CHAMPION_UNCERTAIN`).
- The model can only reject. A setup still needs a proven skill first.
- A model trained in another mode is refused, and in replays a model is never used on dates before its training ended.

Model files live in [`models/`](../models/README.md).

## Goals and limits

| Setting | Default | Behaviour |
|---|---|---|
| `risk.daily_profit_target_usd` | 100 | **Reporting only.** It never sizes, forces or widens a trade. |
| `risk.stop_after_daily_target` | false | Optionally stop new recommendations once the goal is reached. |
| `risk.max_daily_loss_pct` | 2.0 | Hard boundary: at −2% of the reference balance, new recommendations stop for the day. |
| `risk.risk_per_trade_pct` | 0.5 | Risk on one trade. This is separate from, and much smaller than, the daily limit. |

On a $1,000 reference balance, risk per trade is $5. A $100 day would need +20R net, which is not a realistic expectation. `mt5bot agent status` prints this arithmetic.

## Learning from your own trades (opt-in)

Set `agent.observe_user_trades = true` to let the agent read your **closed** MT5 deals (`history_deals_get`, read-only). For each closed trade it stores the symbol, direction, times, result and the market state at entry, as an episode with source `USER`.

- Your trades are never copied, modified or closed, and nothing leaves your machine.
- A profitable manual trade never validates a skill: `USER` episodes are kept out of skill statistics and out of the online model.
- Open positions, stop/take-profit changes and partial closes are not tracked yet.

## Commands

```powershell
mt5bot replay --symbol EURUSD --timeframe M1 --from 2026-09-01   # learn from history, on the replay clock
mt5bot monitor start                                             # observe, decide, learn every cycle
mt5bot agent status                                              # memory size, skill counts, model trust, goal arithmetic
mt5bot agent skills --state VALIDATED
mt5bot agent events                                              # promotions and retirements with reasons
mt5bot agent lessons --query "london breakout failed"
mt5bot agent learn                                               # run one learning cycle now
mt5bot agent import-trades                                       # opt-in: study your closed trades
mt5bot agent catalogue --out docs/SKILLS.md
```

The learning cycle runs as a LangGraph state graph when `langgraph` is installed (`pip install -e ".[agent]"`), and as plain sequential steps otherwise. Both give the same result.

## What has and has not been verified

| Item | Status |
|---|---|
| Structure features, techniques, embeddings, SQLite vector store, skill lifecycle, policy, lessons, playbook, learning cycle, CLI | Tested (`tests/unit/test_agent.py`) |
| pgvector backend | Tested against a real PostgreSQL 16 + pgvector 0.6 locally, and in a dedicated CI job |
| Full agent run on pgvector | Run locally on synthetic data (5,760 episodes, HNSW indexes) |
| Champion model as an agent filter (LightGBM) | Tested on synthetic data: training, loading, scoring and the full decision path. No model trained on real data. |
| Reading real MT5 deal history | **Not run.** Tested with a fake terminal only. |
| Learning on real market data | **Not run.** No skill has been validated on real data, so no performance exists or is claimed. |
