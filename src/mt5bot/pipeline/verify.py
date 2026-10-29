"""End-to-end MT5 verification: rules-based recommendations whose stop/target the observer EA resolved on live ticks."""

from __future__ import annotations

import sqlite3


def verification_results(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, object]]:
    """Latest OBSERVED_DEMO recommendations or rejections with the EA's tick verdict (None while still tracked)."""
    rows = conn.execute(
        "SELECT d.id, d.symbol, d.timeframe, d.status, d.selected_strategy AS strategy, d.direction, d.bar_time_utc, "
        "t.first_hit, t.hit_time_utc, t.entry_price, t.hit_price, t.ticks_seen FROM decisions d "
        "LEFT JOIN tick_resolutions t ON t.decision_id = d.id "
        "WHERE d.mode = 'OBSERVED_DEMO' AND d.selected_strategy IS NOT NULL ORDER BY d.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
