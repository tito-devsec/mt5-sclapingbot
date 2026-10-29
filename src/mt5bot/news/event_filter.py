"""Event-risk filter for scheduled events, breaking news and feed staleness (outage means unknown)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from mt5bot.config.settings import NewsSettings, RiskSettings
from mt5bot.news.relevance import currencies_for
from mt5bot.storage.repositories import NewsRepository, parse_ts


@dataclass
class EventRiskResult:
    upcoming_high_impact: list[str] = field(default_factory=list)
    breaking_news: bool = False
    stale: bool = False
    stale_reason: str = ""
    news_ids: list[int] = field(default_factory=list)


class EventRiskFilter:
    def __init__(self, conn: sqlite3.Connection, news: NewsSettings, risk: RiskSettings) -> None:
        self.repo = NewsRepository(conn)
        self.news = news
        self.risk = risk

    def _staleness(self, decision_time: datetime) -> tuple[bool, str]:
        enabled = [p.id for p in self.news.providers if p.enabled]
        if not self.news.enabled or not enabled:
            return True, "no news provider is enabled; news state unknown"
        status = {r["provider_id"]: r for r in self.repo.provider_status()}
        limit = pd.Timestamp(decision_time) - pd.Timedelta(minutes=self.news.stale_after_minutes)
        for pid in enabled:
            row = status.get(pid)
            last_ok = parse_ts(row["last_success_at"]) if row else None
            if last_ok is None or last_ok < limit or last_ok > pd.Timestamp(decision_time):
                return (
                    True,
                    f"provider {pid} has no successful fetch in the {self.news.stale_after_minutes} min "
                    "before the decision",
                )
        return False, ""

    def evaluate(self, symbol: str, decision_time: datetime) -> EventRiskResult:
        res = EventRiskResult()
        res.stale, res.stale_reason = self._staleness(decision_time)
        curs = currencies_for(symbol)
        before = timedelta(minutes=self.risk.event_window_before_minutes)
        after = timedelta(minutes=self.risk.event_window_after_minutes)
        events = self.repo.events_between(decision_time - after, decision_time + before, known_at=decision_time)
        for e in events:
            if e["currency"] in curs and e["importance"] == "HIGH":
                res.upcoming_high_impact.append(f"{e['currency']} {e['title']} at {e['scheduled_at']}")
        recent = self.repo.available_at(decision_time, self.risk.event_window_after_minutes, curs)
        res.news_ids = [int(r["id"]) for r in recent]
        res.breaking_news = any(r["category"] in ("central_bank", "statistics") for r in recent)
        return res
