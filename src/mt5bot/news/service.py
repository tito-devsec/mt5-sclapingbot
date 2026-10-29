"""News refresh orchestration."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from mt5bot.config.settings import NewsSettings
from mt5bot.news.base import ProviderRegistry
from mt5bot.news.providers.calendar_csv import CSVCalendarProvider
from mt5bot.news.providers.rss import RSSProvider
from mt5bot.storage.repositories import NewsRepository


@dataclass
class RefreshReport:
    provider_id: str
    ok: bool
    fetched: int
    inserted: int
    error: str | None


def build_registry(cfg: NewsSettings) -> ProviderRegistry:
    reg = ProviderRegistry()
    for p in cfg.providers:
        if p.enabled and p.type == "rss":
            reg.register(RSSProvider(p, timeout=cfg.request_timeout_seconds, max_retries=cfg.max_retries))
    return reg


def refresh(
    conn: sqlite3.Connection, registry: ProviderRegistry, now: datetime, calendar: CSVCalendarProvider | None = None
) -> list[RefreshReport]:
    repo = NewsRepository(conn)
    reports = []
    for outcome in registry.fetch_all(now):
        inserted = repo.insert_items(outcome.items) if outcome.ok else 0
        repo.record_provider_attempt(outcome.provider_id, outcome.ok, len(outcome.items), outcome.error)
        reports.append(RefreshReport(outcome.provider_id, outcome.ok, len(outcome.items), inserted, outcome.error))
    if calendar is not None:
        try:
            events = calendar.fetch_events(now)
            n = repo.insert_events(events)
            repo.record_provider_attempt(calendar.id, True, len(events), None)
            reports.append(RefreshReport(calendar.id, True, len(events), n, None))
        except Exception as exc:
            repo.record_provider_attempt(calendar.id, False, 0, str(exc)[:300])
            reports.append(RefreshReport(calendar.id, False, 0, 0, str(exc)[:300]))
    return reports
