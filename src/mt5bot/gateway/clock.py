"""Conversion between broker server time (as reported by MT5) and UTC, including DST."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mt5bot.core.errors import ConfigError

NY_PLUS_7 = "NY+7"


class ServerClock:
    def __init__(self, timezone: str | None = None, fixed_offset_hours: float | None = None) -> None:
        if timezone is None and fixed_offset_hours is None:
            raise ConfigError(
                "broker server time zone is unknown; set mt5.server_timezone (e.g. 'NY+7') "
                "or mt5.server_utc_offset_hours. Run `mt5bot check` to see the current offset.",
                code="SERVER_TIME_UNKNOWN",
            )
        self.timezone = timezone
        self.fixed_offset_hours = fixed_offset_hours
        self._zone = None
        if timezone == NY_PLUS_7:
            self._zone = ZoneInfo("America/New_York")
        elif timezone is not None:
            try:
                self._zone = ZoneInfo(timezone)
            except Exception as exc:
                raise ConfigError(f"unknown time zone {timezone!r}") from exc

    def describe(self) -> str:
        if self.timezone:
            return self.timezone
        return f"fixed UTC{self.fixed_offset_hours:+g}h"

    def server_epoch_to_utc(self, seconds: np.ndarray | pd.Series) -> pd.DatetimeIndex:
        """Convert MT5 'server epoch' seconds to an aware UTC DatetimeIndex."""
        naive = pd.to_datetime(np.asarray(seconds, dtype="int64"), unit="s")
        if self._zone is None:
            assert self.fixed_offset_hours is not None
            return (naive - pd.Timedelta(hours=self.fixed_offset_hours)).tz_localize(UTC)
        if self.timezone == NY_PLUS_7:
            ny_wall = naive - pd.Timedelta(hours=7)
            local = ny_wall.tz_localize(self._zone, ambiguous="NaT", nonexistent="NaT")
        else:
            local = naive.tz_localize(self._zone, ambiguous="NaT", nonexistent="NaT")
        # NaT marks DST gap/overlap wall times; callers treat them as data-quality issues, never guess.
        return local.tz_convert(UTC)

    def utc_to_server_epoch(self, ts: datetime) -> int:
        ts = ts.astimezone(UTC)
        if self._zone is None:
            assert self.fixed_offset_hours is not None
            wall = ts + timedelta(hours=self.fixed_offset_hours)
        elif self.timezone == NY_PLUS_7:
            wall = ts.astimezone(self._zone) + timedelta(hours=7)
        else:
            wall = ts.astimezone(self._zone)
        return int(wall.replace(tzinfo=UTC).timestamp())


def observed_offset_hours(server_epoch_now: int, utc_now: datetime) -> float:
    """Offset implied by a fresh tick, rounded to the nearest half hour."""
    diff = server_epoch_now - utc_now.timestamp()
    return round(diff / 1800.0) / 2.0
