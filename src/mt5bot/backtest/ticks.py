"""Tick-level resolution of bars where both stop and target were touched."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from mt5bot.backtest.simulator import AmbiguityResolver
from mt5bot.core.types import Direction
from mt5bot.storage.repositories import parse_ts


def first_touch(ticks: pd.DataFrame, direction: Direction, stop: float, target: float) -> str | None:
    """Return "STOP" or "TARGET" for whichever level the ticks reached first (longs exit on bid, shorts on ask)."""
    if ticks.empty:
        return None
    t = ticks.sort_values("time_utc", kind="stable")
    px = t["bid"].to_numpy(float) if direction is Direction.LONG else t["ask"].to_numpy(float)
    if direction is Direction.LONG:
        s_hit, t_hit = np.flatnonzero(px <= stop), np.flatnonzero(px >= target)
    else:
        s_hit, t_hit = np.flatnonzero(px >= stop), np.flatnonzero(px <= target)
    first_s = s_hit[0] if len(s_hit) else None
    first_t = t_hit[0] if len(t_hit) else None
    if first_s is None and first_t is None:
        return None
    if first_t is None or (first_s is not None and first_s <= first_t):
        return "STOP"  # a tick touching both at once counts as the stop
    return "TARGET"


def quotes_resolver(conn: sqlite3.Connection, symbol: str, direction: Direction) -> AmbiguityResolver:
    """Resolver that reads stored ticks (quotes table) for the ambiguous bar's time window."""

    def resolve(start: pd.Timestamp, end: pd.Timestamp, stop: float, target: float) -> str | None:
        df = pd.read_sql_query(
            "SELECT time_utc, bid, ask FROM quotes WHERE symbol = ? AND time_utc >= ? AND time_utc < ? "
            "ORDER BY time_utc",
            conn,
            params=(symbol, start.isoformat(), end.isoformat()),
        )
        return first_touch(df, direction, stop, target)

    return resolve


def decision_resolver(
    conn: sqlite3.Connection, decision_id: int, fallback: AmbiguityResolver | None
) -> AmbiguityResolver:
    """Prefer the observer EA's live verdict for this decision when its hit falls inside the ambiguous bar."""
    row = conn.execute(
        "SELECT first_hit, hit_time_utc FROM tick_resolutions WHERE decision_id = ?", (decision_id,)
    ).fetchone()

    def resolve(start: pd.Timestamp, end: pd.Timestamp, stop: float, target: float) -> str | None:
        if row is not None and row["first_hit"] in ("TP", "SL"):
            hit = parse_ts(row["hit_time_utc"])
            if hit is not None and start <= hit < end:
                return "TARGET" if row["first_hit"] == "TP" else "STOP"
        return fallback(start, end, stop, target) if fallback else None

    return resolve
