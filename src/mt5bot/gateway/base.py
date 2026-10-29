"""Read-only market-data gateway interface with no order, modify, close or cancel methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

from mt5bot.core.types import InstrumentSpec, Timeframe

BAR_COLUMNS = ["time_utc", "open", "high", "low", "close", "tick_volume", "spread_points", "real_volume"]
QUOTE_COLUMNS = ["time_utc", "bid", "ask"]


@dataclass(frozen=True)
class TerminalStatus:
    connected: bool
    trade_allowed_in_terminal: bool  # reported only; this project never trades
    build: int | None
    path_known: bool


@dataclass(frozen=True)
class AccountSnapshot:
    """Account metadata without login, password or server name."""

    is_demo: bool
    trade_mode_name: str
    currency: str
    balance: float
    equity: float
    leverage: int
    company: str


@dataclass(frozen=True)
class SymbolMeta:
    name: str
    base_currency: str
    profit_currency: str
    point: float
    digits: int
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    visible: bool

    def to_spec(self, canonical: str) -> InstrumentSpec:
        return InstrumentSpec(
            symbol=canonical,
            base=self.base_currency,
            quote=self.profit_currency,
            point=self.point,
            digits=self.digits,
            contract_size=self.contract_size,
            volume_min=self.volume_min,
            volume_step=self.volume_step,
            volume_max=self.volume_max,
        )


class MarketGateway(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def terminal_status(self) -> TerminalStatus: ...
    def account(self) -> AccountSnapshot: ...
    def symbols(self) -> list[SymbolMeta]: ...
    def server_epoch_now(self, broker_symbol: str) -> int | None: ...
    def bars(self, broker_symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        """Bars whose open time is in [start, end), as UTC, columns ``BAR_COLUMNS``."""
        ...

    def quotes(self, broker_symbol: str, start: datetime, end: datetime) -> pd.DataFrame: ...
