"""Deterministic currency-to-instrument relevance map; relevance, not a prediction of direction."""

from __future__ import annotations

RELEVANCE: dict[str, dict[str, tuple[float, float]]] = {
    "USD": {"EURUSD": (1.0, 0.9), "GBPUSD": (1.0, 0.9), "USDJPY": (1.0, 0.9), "XAUUSD": (0.9, 0.8)},
    "EUR": {"EURUSD": (1.0, 0.9), "XAUUSD": (0.3, 0.4)},
    "GBP": {"GBPUSD": (1.0, 0.9)},
    "JPY": {"USDJPY": (1.0, 0.9), "XAUUSD": (0.2, 0.3)},
    "XAU": {"XAUUSD": (1.0, 0.8)},
}


def currencies_for(symbol: str, min_relevance: float = 0.5) -> set[str]:
    return {cur for cur, m in RELEVANCE.items() if m.get(symbol, (0.0, 0.0))[0] >= min_relevance}


def relevance(currency: str, symbol: str) -> tuple[float, float]:
    return RELEVANCE.get(currency, {}).get(symbol, (0.0, 0.0))
