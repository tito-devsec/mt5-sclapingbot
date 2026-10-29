"""Resolve canonical symbol names (EURUSD) to the broker's names (EURUSD.m, ...)."""

from __future__ import annotations

import re

from mt5bot.core.errors import AmbiguousSymbolError, SymbolResolutionError
from mt5bot.gateway.base import SymbolMeta

_ALNUM = re.compile(r"[^A-Z0-9]")


def _core(name: str) -> str:
    return _ALNUM.sub("", name.upper())


def resolve_symbol(canonical: str, available: list[SymbolMeta], explicit: dict[str, str]) -> SymbolMeta:
    """Resolve a canonical symbol to exactly one broker symbol (explicit map first); ambiguity raises."""
    by_name = {s.name: s for s in available}
    if canonical in explicit:
        target = explicit[canonical]
        if target not in by_name:
            raise SymbolResolutionError(
                f"configured symbol_map[{canonical!r}] = {target!r} is not offered by the broker"
            )
        return by_name[target]
    base, quote = canonical[:3], canonical[3:]
    candidates: list[SymbolMeta] = []
    for s in available:
        core = _core(s.name)
        if canonical not in core:
            continue
        extra = core.replace(canonical, "", 1)
        if len(extra) > 4:  # longer decorations usually denote a different product
            continue
        if s.base_currency.upper() not in (base, "") or s.profit_currency.upper() not in (quote, ""):
            continue
        candidates.append(s)

    if not candidates:
        raise SymbolResolutionError(f"no broker symbol found for {canonical}")
    if len(candidates) > 1:
        names = ", ".join(sorted(c.name for c in candidates))
        raise AmbiguousSymbolError(
            f"{canonical} matches several broker symbols ({names}); set mt5.symbol_map.{canonical} to the one you want"
        )
    return candidates[0]
