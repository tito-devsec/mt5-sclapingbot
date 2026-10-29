"""Economic-calendar import from a local CSV you are permitted to use; no website is scraped."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

from mt5bot.core.errors import ProviderError
from mt5bot.news.models import EconomicEvent, clean_text, fingerprint

REQUIRED = {"scheduled_at_utc", "currency", "title"}


class CSVCalendarProvider:
    def __init__(self, path: Path, provider_id: str = "calendar_csv") -> None:
        self.path = Path(path)
        self.id = provider_id

    def fetch_events(self, now: datetime) -> list[EconomicEvent]:
        if not self.path.exists():
            raise ProviderError(f"calendar file {self.path} not found", code="CALENDAR_MISSING")
        with open(self.path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or not set(reader.fieldnames) >= REQUIRED:
                raise ProviderError(f"calendar CSV needs columns {sorted(REQUIRED)}", code="CALENDAR_MALFORMED")
            out = []
            for i, row in enumerate(reader, start=2):
                try:
                    ts = pd.Timestamp(row["scheduled_at_utc"])
                    if ts.tzinfo is None:
                        raise ValueError("timestamp must include a UTC offset")
                except ValueError as exc:
                    raise ProviderError(f"calendar line {i}: bad timestamp ({exc})", code="CALENDAR_MALFORMED") from exc
                imp = (row.get("importance") or "").strip().upper() or None
                if imp not in (None, "LOW", "MEDIUM", "HIGH"):
                    raise ProviderError(
                        f"calendar line {i}: importance must be LOW/MEDIUM/HIGH", code="CALENDAR_MALFORMED"
                    )
                cur = (row["currency"] or "").strip().upper()[:3]
                title = clean_text(row["title"], 300)
                source = clean_text(row.get("source"), 100) or self.id
                out.append(
                    EconomicEvent(
                        source=source,
                        scheduled_at=ts.tz_convert("UTC").to_pydatetime(),
                        currency=cur,
                        title=title,
                        retrieved_at=now,
                        importance=imp,
                        actual=(row.get("actual") or None),
                        forecast=(row.get("forecast") or None),
                        previous=(row.get("previous") or None),
                        fingerprint=fingerprint(source, cur, title, ts.isoformat()),
                    )
                )
            return out
