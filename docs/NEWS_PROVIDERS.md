# News and economic-event providers

The news subsystem (`src/mt5bot/news/`) is optional. Market-data collection, features and inference keep working when every provider is disabled or failing.

## Providers implemented

| Provider id | Source | Status | Credentials |
|---|---|---|---|
| `fed_press` | `https://www.federalreserve.gov/feeds/press_all.xml` (Federal Reserve press releases) | Implemented. Live fetch and parse verified on 2026-10-29 (20 items). | none |
| `ecb_press` | `https://www.ecb.europa.eu/rss/press.html` (ECB press releases, speeches) | Implemented. Live fetch and parse verified on 2026-10-29 (15 items). | none |
| `boe_news` | `https://www.bankofengland.co.uk/rss/news` (Bank of England news) | Implemented. Live fetch and parse verified on 2026-10-29 (50 items). | none |
| `calendar_csv` | A local CSV economic calendar that you are permitted to use | Implemented (tested with fixtures) | none |
| Finnhub, NewsAPI | Licensed APIs | **Not implemented.** The configuration keys are reserved. | your own API key |
| BLS, BEA, Bank of Japan | Official statistics and central-bank sources | **Not implemented.** Endpoints and terms not yet verified. | - |

All RSS providers ship **disabled** (`enabled = false`). Review each publisher's website terms, then enable a provider in `config/local.toml`:

```toml
[[news.providers]]
id = "fed_press"
url = "https://www.federalreserve.gov/feeds/press_all.xml"
currencies = ["USD"]
category = "central_bank"
enabled = true
min_interval_seconds = 300
```

Then run `mt5bot news refresh`. The monitor also refreshes the enabled providers each cycle, subject to `min_interval_seconds`.

## Handling rules

- **Untrusted input:**
  - XML is pre-checked with defusedxml, so DTDs and entity expansion are rejected.
  - Markup and control characters are stripped.
  - Only `http(s)` links are kept.
  - Headlines are never executed or interpreted as instructions.
- **Stored fields:** source, headline, published time (UTC), retrieval time (UTC), provider item id, URL, currencies, category, sentiment, quality status and a deduplication fingerprint.
- **Summaries** are stored only when `store_summary = true`, which you should set only if the provider's terms allow it.
- **Importance** is stored only when the source supplies it. The RSS feeds above do not.
- **Availability rule:** `available_at = max(published_at, first retrieval)`. A news item can influence a decision only if `available_at ≤ decision time`.
  - Duplicates keep the first retrieval time.
  - Because availability depends on our own retrieval time, today's article collection can never simulate what was known in the past.
- **Staleness:** if any enabled provider has had no successful fetch within `news.stale_after_minutes` before a decision, news state is **unknown**. It is never treated as "no news".
- **Failures:**
  - Timeouts and HTTP 5xx are retried (`news.max_retries`).
  - HTTP 429 and 401/403 fail fast with their own codes.
  - One provider failing never stops the others.

## Relevance and sentiment

**Relevance mapping** (`news/relevance.py`):

| Currency | Relevant instruments |
|---|---|
| USD | EURUSD, GBPUSD, USDJPY, XAUUSD |
| EUR | EURUSD |
| GBP | GBPUSD |
| JPY | USDJPY |
| XAU | XAUUSD |

Each mapping carries a relevance score and a confidence. Neither is a prediction of price direction.

**Sentiment** (`news/sentiment.py`) is a small lexicon written for this project, with scores in [−1, 1]. It is stored as a candidate feature only and is not yet used by any model.

## Economic-calendar CSV

The CSV needs these columns: `scheduled_at_utc,currency,title,importance,actual,forecast,previous,source`.

- `scheduled_at_utc` must include a UTC offset.
- `importance` must be `LOW`, `MEDIUM`, `HIGH` or empty.
- Values are stored exactly as given; nothing is fabricated.

Set `news.calendar_csv` to the file's path. Only events retrieved before a decision time are used for that decision.
