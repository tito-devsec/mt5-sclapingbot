"""Normalized news and economic-event records."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(text: str | None, limit: int) -> str:
    """Strip markup and control characters. The result is display data, never instructions."""
    if not text:
        return ""
    t = _TAG.sub(" ", text)
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C" or ch in " \t")
    return _WS.sub(" ", t).strip()[:limit]


def fingerprint(*parts: str | None) -> str:
    norm = "|".join(_WS.sub(" ", (p or "").strip().lower()) for p in parts)
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class NewsItem:
    provider_id: str
    source_name: str
    headline: str
    retrieved_at: datetime
    published_at: datetime | None
    category: str
    currencies: frozenset[str]
    provider_item_id: str | None = None
    url: str | None = None
    summary: str | None = None
    importance: str | None = None  # only if the source supplies it
    sentiment: float | None = None
    quality_status: str = "OK"
    fingerprint: str = ""

    @property
    def available_at(self) -> datetime:
        """Earliest time this item could influence a decision: max(published_at, first retrieval)."""
        if self.published_at is None or self.quality_status == "FUTURE_TIMESTAMP":
            return self.retrieved_at
        return max(self.published_at, self.retrieved_at)


@dataclass(frozen=True)
class EconomicEvent:
    source: str
    scheduled_at: datetime
    currency: str
    title: str
    retrieved_at: datetime
    category: str | None = None
    importance: str | None = None
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    published_at: datetime | None = None
    fingerprint: str = field(default="")
