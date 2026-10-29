"""RSS/Atom provider with defusedxml checks, timeouts, retries and a minimum polling interval."""

from __future__ import annotations

import calendar
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from mt5bot.config.settings import ProviderConfig
from mt5bot.core.errors import ProviderError
from mt5bot.news.models import NewsItem, clean_text, fingerprint
from mt5bot.news.sentiment import score as sentiment_score

USER_AGENT = "mt5-sclapingbot/0.1 (research; +https://github.com/tito-devsec/mt5-sclapingbot)"
MAX_BYTES = 5_000_000
FUTURE_TOLERANCE = timedelta(minutes=5)

HttpGet = Callable[[str, float], tuple[int, bytes, dict[str, str]]]


def default_http_get(url: str, timeout: float) -> tuple[int, bytes, dict[str, str]]:
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        r = client.get(url)
        return r.status_code, r.content[: MAX_BYTES + 1], dict(r.headers)


class RSSProvider:
    def __init__(
        self,
        cfg: ProviderConfig,
        *,
        timeout: float,
        max_retries: int,
        http_get: HttpGet = default_http_get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.id = cfg.id
        self.timeout = timeout
        self.max_retries = max_retries
        self._get = http_get
        self._sleep = sleep
        self._clock = clock
        self._last_fetch: float | None = None

    def _download(self) -> bytes:
        last_err = "unknown"
        for attempt in range(self.max_retries + 1):
            try:
                status, body, headers = self._get(self.cfg.url, self.timeout)
            except httpx.HTTPError as exc:
                last_err = f"network error {type(exc).__name__}"
            else:
                if status == 200:
                    if len(body) > MAX_BYTES:
                        raise ProviderError(f"{self.id}: response larger than {MAX_BYTES} bytes", code="NEWS_TOO_LARGE")
                    return body
                if status == 429:
                    retry_after = headers.get("retry-after", headers.get("Retry-After", "60"))
                    wait = min(float(retry_after) if retry_after.isdigit() else 60.0, 300.0)
                    raise ProviderError(f"{self.id}: rate limited (retry after {wait:.0f}s)", code="NEWS_RATE_LIMITED")
                if status in (401, 403):
                    raise ProviderError(f"{self.id}: access denied (HTTP {status})", code="NEWS_ACCESS_DENIED")
                last_err = f"HTTP {status}"
                if status < 500:
                    break
            if attempt < self.max_retries:
                self._sleep(min(8.0, 0.5 * 2**attempt))
        raise ProviderError(f"{self.id}: fetch failed ({last_err})", code="NEWS_FETCH_FAILED")

    def fetch(self, now: datetime) -> list[NewsItem]:
        t = self._clock()
        if self._last_fetch is not None and t - self._last_fetch < self.cfg.min_interval_seconds:
            return []  # respect the provider's minimum polling interval
        self._last_fetch = t
        body = self._download()
        return parse_feed(body, self.cfg, retrieved_at=now)


def _validate_xml(body: bytes) -> None:
    from defusedxml import DefusedXmlException
    from defusedxml.ElementTree import fromstring

    try:
        fromstring(body.lstrip(b"\xef\xbb\xbf").strip(), forbid_dtd=True)
    except DefusedXmlException as exc:
        raise ProviderError(f"unsafe XML rejected ({type(exc).__name__})", code="NEWS_UNSAFE_XML") from exc
    except Exception as exc:
        raise ProviderError(f"malformed feed ({type(exc).__name__})", code="NEWS_MALFORMED") from exc


def _entry_time(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), UTC)
    return None


def parse_feed(body: bytes, cfg: ProviderConfig, retrieved_at: datetime) -> list[NewsItem]:
    import feedparser

    _validate_xml(body)
    parsed = feedparser.parse(body)
    if parsed.get("bozo") and not parsed.get("entries"):
        raise ProviderError(f"{cfg.id}: malformed feed", code="NEWS_MALFORMED")
    source = clean_text(parsed.get("feed", {}).get("title"), 200) or cfg.id
    items = []
    for e in parsed.get("entries", []):
        headline = clean_text(e.get("title"), 500)
        if not headline:
            continue
        link = e.get("link")
        url = link if isinstance(link, str) and link.startswith(("https://", "http://")) else None
        published = _entry_time(e)
        status = "OK"
        if published is None:
            status = "NO_PUBLISH_TIME"
        elif published > retrieved_at + FUTURE_TOLERANCE:
            status = "FUTURE_TIMESTAMP"
        summary = clean_text(e.get("summary"), 2000) if cfg.store_summary else None
        pid = e.get("id") or e.get("guid")
        items.append(
            NewsItem(
                provider_id=cfg.id,
                source_name=source,
                headline=headline,
                retrieved_at=retrieved_at,
                published_at=published,
                category=cfg.category,
                currencies=frozenset(cfg.currencies),
                provider_item_id=str(pid)[:300] if pid else None,
                url=url,
                summary=summary,
                sentiment=sentiment_score(headline),
                quality_status=status,
                fingerprint=fingerprint(
                    cfg.id, str(pid) if pid else None, headline, published.isoformat() if published else None
                ),
            )
        )
    return items
