"""Test doubles: an in-memory gateway and a fake MetaTrader5 module whose trading calls raise."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from mt5bot.core.errors import GatewayError, TerminalUnavailableError
from mt5bot.core.types import Timeframe
from mt5bot.gateway.base import BAR_COLUMNS, QUOTE_COLUMNS, AccountSnapshot, SymbolMeta, TerminalStatus


def demo_account(currency: str = "USD", is_demo: bool = True) -> AccountSnapshot:
    return AccountSnapshot(
        is_demo=is_demo,
        trade_mode_name="DEMO" if is_demo else "REAL",
        currency=currency,
        balance=1000.0,
        equity=1000.0,
        leverage=100,
        company="Fake Broker Ltd",
    )


def symbol_meta(
    name: str, base: str, quote: str, point: float = 0.00001, digits: int = 5, contract: float = 100_000.0
) -> SymbolMeta:
    return SymbolMeta(name, base, quote, point, digits, contract, 0.01, 0.01, 100.0, True)


DEFAULT_SYMBOLS = [
    symbol_meta("EURUSD", "EUR", "USD"),
    symbol_meta("GBPUSD", "GBP", "USD"),
    symbol_meta("USDJPY", "USD", "JPY", 0.001, 3),
    symbol_meta("XAUUSD", "XAU", "USD", 0.01, 2, 100.0),
]


@dataclass
class FakeGateway:
    """In-memory gateway with UTC data. Configure failures with the flags."""

    account_snapshot: AccountSnapshot | None = field(default_factory=demo_account)
    symbol_list: list[SymbolMeta] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    bar_data: dict[tuple[str, Timeframe], pd.DataFrame] = field(default_factory=dict)
    quote_data: dict[str, pd.DataFrame] = field(default_factory=dict)
    terminal_running: bool = True
    fail_bars_times: int = 0
    connected: bool = False
    calls: list[str] = field(default_factory=list)

    def connect(self) -> None:
        self.calls.append("connect")
        if not self.terminal_running:
            raise TerminalUnavailableError("MT5 initialize failed (error code -10003)")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def terminal_status(self) -> TerminalStatus:
        return TerminalStatus(self.terminal_running and self.connected, False, 4000, True)

    def _require(self) -> None:
        if not self.connected:
            raise TerminalUnavailableError("gateway is not connected")

    def account(self) -> AccountSnapshot:
        self._require()
        if self.account_snapshot is None:
            raise TerminalUnavailableError("no account information")
        return self.account_snapshot

    def symbols(self) -> list[SymbolMeta]:
        self._require()
        return list(self.symbol_list)

    def server_epoch_now(self, broker_symbol: str) -> int | None:
        return None

    def bars(self, broker_symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        self._require()
        self.calls.append(f"bars:{broker_symbol}:{timeframe}")
        if self.fail_bars_times > 0:
            self.fail_bars_times -= 1
            raise GatewayError("simulated copy_rates_range failure")
        df = self.bar_data.get((broker_symbol, timeframe))
        if df is None:
            return pd.DataFrame(columns=BAR_COLUMNS)
        m = (df["time_utc"] >= pd.Timestamp(start)) & (df["time_utc"] < pd.Timestamp(end))
        return df.loc[m, BAR_COLUMNS].reset_index(drop=True)

    def quotes(self, broker_symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        self._require()
        df = self.quote_data.get(broker_symbol)
        if df is None:
            return pd.DataFrame(columns=QUOTE_COLUMNS)
        m = (df["time_utc"] >= pd.Timestamp(start)) & (df["time_utc"] < pd.Timestamp(end))
        return df.loc[m, QUOTE_COLUMNS].reset_index(drop=True)


class FakeMT5Module:
    """Minimal stand-in for the MetaTrader5 package (data functions only)."""

    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_H1 = 1, 5, 15, 16385
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
    COPY_TICKS_INFO = 1

    def __init__(
        self, *, trade_mode: int = 0, currency: str = "USD", init_ok: bool = True, server_offset_hours: int = 2
    ) -> None:
        self.trade_mode = trade_mode
        self.currency = currency
        self.init_ok = init_ok
        self.offset = server_offset_hours * 3600
        self.rates: dict[tuple[str, int], np.ndarray] = {}
        self.init_kwargs: dict[str, Any] = {}

    def initialize(self, **kwargs: Any) -> bool:
        self.init_kwargs = kwargs
        return self.init_ok

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (-10003, "IPC initialize failed")

    def version(self) -> tuple[int, int, str]:
        return (500, 4000, "fake")

    def terminal_info(self) -> Any:
        return SimpleNamespace(connected=True, trade_allowed=False, build=4000, path="C:/MT5")

    def account_info(self) -> Any:
        return SimpleNamespace(
            trade_mode=self.trade_mode,
            currency=self.currency,
            balance=1000.0,
            equity=1000.0,
            leverage=100,
            company="Fake Broker Ltd",
            login=12345678,
            server="Fake-Demo",
        )

    def symbols_get(self) -> tuple[Any, ...]:
        return tuple(
            SimpleNamespace(
                name=s.name,
                currency_base=s.base_currency,
                currency_profit=s.profit_currency,
                point=s.point,
                digits=s.digits,
                trade_contract_size=s.contract_size,
                volume_min=0.01,
                volume_step=0.01,
                volume_max=100.0,
                visible=True,
            )
            for s in DEFAULT_SYMBOLS
        )

    def symbol_info(self, name: str) -> Any:
        return next((s for s in self.symbols_get() if s.name == name), None)

    def symbol_info_tick(self, name: str) -> Any:
        return None

    def symbol_select(self, name: str, enable: bool) -> bool:
        return True

    def set_bars_from_utc(self, symbol: str, tf_const: int, df: pd.DataFrame) -> None:
        """Store bars, converting UTC times to fake server epoch (UTC + offset)."""
        dt = np.dtype(
            [
                ("time", "<i8"),
                ("open", "<f8"),
                ("high", "<f8"),
                ("low", "<f8"),
                ("close", "<f8"),
                ("tick_volume", "<u8"),
                ("spread", "<i4"),
                ("real_volume", "<u8"),
            ]
        )
        arr = np.zeros(len(df), dtype=dt)
        arr["time"] = df["time_utc"].dt.as_unit("s").astype("int64").to_numpy() + self.offset
        for c in ("open", "high", "low", "close", "tick_volume", "real_volume"):
            arr[c] = df[c].to_numpy()
        arr["spread"] = df["spread_points"].to_numpy()
        self.rates[(symbol, tf_const)] = arr

    def copy_rates_range(self, symbol: str, tf: int, start: datetime, end: datetime) -> np.ndarray | None:
        arr = self.rates.get((symbol, tf))
        if arr is None:
            return None
        s, e = int(start.timestamp()), int(end.timestamp())
        return arr[(arr["time"] >= s) & (arr["time"] <= e)]

    def copy_ticks_range(self, symbol: str, start: datetime, end: datetime, flags: int) -> np.ndarray:
        dt = np.dtype([("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("time_msc", "<i8")])
        return np.zeros(0, dtype=dt)

    # Trading functions exist on the real module; the fake makes reaching them fatal.
    def _forbidden(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("a trading function was reached - this must never happen")

    order_send = order_check = positions_get = orders_get = _forbidden
