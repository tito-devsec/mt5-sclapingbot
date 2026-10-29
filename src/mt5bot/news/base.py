"""Provider interface and registry. The decision engine depends only on this module."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mt5bot.core.errors import ProviderError
from mt5bot.core.logging import log_event
from mt5bot.news.models import EconomicEvent, NewsItem

log = logging.getLogger(__name__)


class NewsProvider(Protocol):
    id: str

    def fetch(self, now: datetime) -> list[NewsItem]: ...


class CalendarProvider(Protocol):
    id: str

    def fetch_events(self, now: datetime) -> list[EconomicEvent]: ...


@dataclass
class FetchOutcome:
    provider_id: str
    ok: bool
    items: list[NewsItem]
    error: str | None = None


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, NewsProvider] = {}

    def register(self, provider: NewsProvider) -> None:
        if provider.id in self._providers:
            raise ValueError(f"duplicate provider id {provider.id}")
        self._providers[provider.id] = provider

    @property
    def ids(self) -> list[str]:
        return sorted(self._providers)

    def fetch_all(self, now: datetime) -> list[FetchOutcome]:
        """Fetch from every provider; one failing provider never stops the others."""
        out = []
        for pid in self.ids:
            try:
                items = self._providers[pid].fetch(now)
                out.append(FetchOutcome(pid, True, items))
            except ProviderError as exc:
                log_event(log, logging.WARNING, "news provider failed", provider=pid, code=exc.code)
                out.append(FetchOutcome(pid, False, [], f"{exc.code}: {exc}"))
            except Exception as exc:  # unexpected provider bug: isolate it
                log_event(log, logging.ERROR, "news provider crashed", provider=pid, error=type(exc).__name__)
                out.append(FetchOutcome(pid, False, [], f"UNEXPECTED: {type(exc).__name__}"))
        return out
