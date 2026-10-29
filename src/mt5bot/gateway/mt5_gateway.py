"""MetaTrader 5 read-only gateway; the module is reached only via an allow-list proxy."""

from __future__ import annotations

from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pandas as pd

from mt5bot.config.settings import MT5Settings
from mt5bot.core.errors import ExecutionDisabledError, GatewayError, TerminalUnavailableError
from mt5bot.core.types import Timeframe, ensure_utc
from mt5bot.gateway.base import (
    BAR_COLUMNS,
    QUOTE_COLUMNS,
    AccountSnapshot,
    SymbolMeta,
    TerminalStatus,
)
from mt5bot.gateway.clock import ServerClock
from mt5bot.gateway.retry import retry

ALLOWED_FUNCTIONS = frozenset(
    {
        "initialize",
        "shutdown",
        "last_error",
        "version",
        "terminal_info",
        "account_info",
        "symbols_get",
        "symbol_info",
        "symbol_info_tick",
        "symbol_select",  # adds a symbol to Market Watch so history can load; not a trade
        "copy_rates_range",
        "copy_ticks_range",
    }
)
_ALLOWED_CONSTANT_PREFIXES = ("TIMEFRAME_", "COPY_TICKS_", "ACCOUNT_TRADE_MODE_")


class ReadOnlyMT5:
    """Allow-list proxy around the MetaTrader5 module."""

    def __init__(self, module: ModuleType | Any) -> None:
        object.__setattr__(self, "_m", module)

    def __getattr__(self, name: str) -> Any:
        if name in ALLOWED_FUNCTIONS or name.startswith(_ALLOWED_CONSTANT_PREFIXES):
            return getattr(object.__getattribute__(self, "_m"), name)
        raise ExecutionDisabledError(f"MetaTrader5.{name} is not available: this project is read-only and never trades")

    def __setattr__(self, name: str, value: Any) -> None:
        raise ExecutionDisabledError("ReadOnlyMT5 is immutable")


def _load_module() -> ModuleType:
    try:
        import MetaTrader5
    except ImportError as exc:  # non-Windows or package missing
        raise TerminalUnavailableError(
            "the MetaTrader5 package is not installed (it is only available on Windows)"
        ) from exc
    return MetaTrader5


class MT5Gateway:
    def __init__(self, settings: MT5Settings, clock: ServerClock | None, module: Any | None = None) -> None:
        self._settings = settings
        self._clock = clock
        self._mt5 = ReadOnlyMT5(module if module is not None else _load_module())
        self._connected = False

    # -- connection -------------------------------------------------------
    def connect(self) -> None:
        kwargs: dict[str, Any] = {"timeout": self._settings.timeout_ms}
        if self._settings.terminal_path:
            kwargs["path"] = self._settings.terminal_path
        if self._settings.login is not None:
            login = self._settings.login.get_secret_value()
            if not login.isdigit():
                raise GatewayError("mt5.login must be the numeric account login", code="CONFIG_INVALID")
            kwargs["login"] = int(login)
            if self._settings.password is not None:
                kwargs["password"] = self._settings.password.get_secret_value()
            if self._settings.server:
                kwargs["server"] = self._settings.server

        def attempt() -> None:
            if not self._mt5.initialize(**kwargs):
                code, _msg = self._error()
                raise TerminalUnavailableError(f"MT5 initialize failed (error code {code})")

        retry(attempt, attempts=self._settings.connect_retries + 1, retry_on=(TerminalUnavailableError,))
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self._mt5.shutdown()
            self._connected = False

    def _error(self) -> tuple[int, str]:
        err = self._mt5.last_error()
        try:
            return int(err[0]), str(err[1])
        except (TypeError, IndexError, ValueError):
            return -1, "unknown"

    def _require(self) -> None:
        if not self._connected:
            raise TerminalUnavailableError("gateway is not connected; call connect() first")

    # -- metadata ---------------------------------------------------------
    def terminal_status(self) -> TerminalStatus:
        info = self._mt5.terminal_info()
        if info is None:
            return TerminalStatus(False, False, None, False)
        return TerminalStatus(
            connected=bool(getattr(info, "connected", False)),
            trade_allowed_in_terminal=bool(getattr(info, "trade_allowed", False)),
            build=getattr(info, "build", None),
            path_known=bool(getattr(info, "path", "")),
        )

    def account(self) -> AccountSnapshot:
        self._require()
        info = self._mt5.account_info()
        if info is None:
            raise TerminalUnavailableError("no account information (is the terminal logged in?)")
        demo = getattr(self._mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        names = {
            getattr(self._mt5, "ACCOUNT_TRADE_MODE_DEMO", 0): "DEMO",
            getattr(self._mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1): "CONTEST",
            getattr(self._mt5, "ACCOUNT_TRADE_MODE_REAL", 2): "REAL",
        }
        mode = int(info.trade_mode)
        return AccountSnapshot(
            is_demo=mode == demo,
            trade_mode_name=names.get(mode, f"UNKNOWN({mode})"),
            currency=str(info.currency),
            balance=float(info.balance),
            equity=float(info.equity),
            leverage=int(info.leverage),
            company=str(getattr(info, "company", "")),
        )

    def symbols(self) -> list[SymbolMeta]:
        self._require()
        raw = self._mt5.symbols_get() or ()
        return [
            SymbolMeta(
                name=str(s.name),
                base_currency=str(s.currency_base),
                profit_currency=str(s.currency_profit),
                point=float(s.point),
                digits=int(s.digits),
                contract_size=float(s.trade_contract_size),
                volume_min=float(s.volume_min),
                volume_step=float(s.volume_step),
                volume_max=float(s.volume_max),
                visible=bool(s.visible),
            )
            for s in raw
        ]

    def server_epoch_now(self, broker_symbol: str) -> int | None:
        self._require()
        tick = self._mt5.symbol_info_tick(broker_symbol)
        return int(tick.time) if tick is not None else None

    # -- history ----------------------------------------------------------
    def _need_clock(self) -> ServerClock:
        if self._clock is None:
            from mt5bot.core.errors import ConfigError

            raise ConfigError("server time zone not configured", code="SERVER_TIME_UNKNOWN")
        return self._clock

    @staticmethod
    def _server_window(clock: ServerClock, start: datetime, end: datetime) -> tuple[datetime, datetime]:
        # MetaTrader5 uses .timestamp(), so pass aware UTC datetimes carrying the server-time epoch.
        s = datetime.fromtimestamp(clock.utc_to_server_epoch(ensure_utc(start)), UTC)
        e = datetime.fromtimestamp(clock.utc_to_server_epoch(ensure_utc(end)), UTC)
        return s, e

    def bars(self, broker_symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        self._require()
        clock = self._need_clock()
        self._mt5.symbol_select(broker_symbol, True)
        tf_const = getattr(self._mt5, timeframe.mt5_constant)
        # copy_rates_range takes datetimes that MT5 interprets as server time.
        s, e = self._server_window(clock, start, end)
        rates = self._mt5.copy_rates_range(broker_symbol, tf_const, s, e)
        if rates is None:
            code, _ = self._error()
            raise GatewayError(f"copy_rates_range failed for {broker_symbol} {timeframe} (error {code})")
        df = pd.DataFrame(rates)
        if df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        df["time_utc"] = clock.server_epoch_to_utc(df["time"].to_numpy())
        df = df.rename(columns={"spread": "spread_points"})
        out = df[BAR_COLUMNS].copy()
        in_window = (out["time_utc"] >= pd.Timestamp(start)) & (out["time_utc"] < pd.Timestamp(end))
        # Rows whose server time could not be mapped (NaT) are kept for the quality checker.
        return out[in_window | out["time_utc"].isna()].reset_index(drop=True)

    def quotes(self, broker_symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        self._require()
        clock = self._need_clock()
        flags = getattr(self._mt5, "COPY_TICKS_INFO", 1)
        s, e = self._server_window(clock, start, end)
        ticks = self._mt5.copy_ticks_range(broker_symbol, s, e, flags)
        if ticks is None:
            code, _ = self._error()
            raise GatewayError(f"copy_ticks_range failed for {broker_symbol} (error {code})")
        df = pd.DataFrame(ticks)
        if df.empty:
            return pd.DataFrame(columns=QUOTE_COLUMNS)
        if "time_msc" in df:
            secs = df["time_msc"].to_numpy() // 1000
            ms = df["time_msc"].to_numpy() % 1000
            df["time_utc"] = clock.server_epoch_to_utc(secs) + pd.to_timedelta(ms, unit="ms")
        else:
            df["time_utc"] = clock.server_epoch_to_utc(df["time"].to_numpy())
        return df[QUOTE_COLUMNS].reset_index(drop=True)
