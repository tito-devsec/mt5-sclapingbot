"""Context buckets for strategy-performance memory."""

from __future__ import annotations

from typing import Any


def vol_bucket(vol_pctile: float | None) -> str:
    if vol_pctile is None or vol_pctile != vol_pctile:
        return "unknown"
    return "low" if vol_pctile < 1 / 3 else ("high" if vol_pctile > 2 / 3 else "mid")


def session_name(features_row: dict[str, Any]) -> str:
    active = [k[3:] for k, v in features_row.items() if k.startswith("in_") and v == 1.0]
    return "+".join(sorted(active)) if active else "off"


def bucket(
    strategy: str, symbol: str, timeframe: str, regime: str, session: str, vol: str, news: str
) -> dict[str, str]:
    return {
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "regime": regime,
        "session": session,
        "vol": vol,
        "news": news,
    }
