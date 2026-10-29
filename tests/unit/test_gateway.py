from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from mt5bot.config.settings import MT5Settings
from mt5bot.core.errors import (
    AccountValidationError,
    AmbiguousSymbolError,
    ConfigError,
    ExecutionDisabledError,
    GatewayError,
    SymbolResolutionError,
    TerminalUnavailableError,
)
from mt5bot.core.types import Timeframe
from mt5bot.gateway.account import require_valid_account
from mt5bot.gateway.clock import NY_PLUS_7, ServerClock, observed_offset_hours
from mt5bot.gateway.fake import FakeMT5Module, demo_account, symbol_meta
from mt5bot.gateway.mt5_gateway import MT5Gateway, ReadOnlyMT5
from mt5bot.gateway.retry import RateLimiter, retry
from mt5bot.gateway.symbols import resolve_symbol
from mt5bot.testing.synthetic import synthetic_bars


def test_readonly_proxy_blocks_trading_functions():
    proxy = ReadOnlyMT5(FakeMT5Module())
    for name in ("order_send", "order_check", "positions_get", "orders_get", "order_calc_margin", "Buy"):
        with pytest.raises(ExecutionDisabledError):
            getattr(proxy, name)
    assert proxy.TIMEFRAME_M1 == 1
    with pytest.raises(ExecutionDisabledError):
        proxy.order_send = lambda *a: None


def test_account_guard_rejects_real_and_wrong_currency():
    require_valid_account(demo_account(), "USD")
    with pytest.raises(AccountValidationError) as e:
        require_valid_account(demo_account(is_demo=False), "USD")
    assert e.value.code == "ACCOUNT_NOT_DEMO"
    with pytest.raises(AccountValidationError) as e:
        require_valid_account(demo_account(currency="EUR"), "USD")
    assert e.value.code == "ACCOUNT_CURRENCY_MISMATCH"
    with pytest.raises(AccountValidationError):
        require_valid_account(None, "USD")


def test_mt5_gateway_account_modes():
    for mode, demo in ((0, True), (2, False), (1, False)):
        gw = MT5Gateway(MT5Settings(), ServerClock(fixed_offset_hours=0), module=FakeMT5Module(trade_mode=mode))
        gw.connect()
        assert gw.account().is_demo is demo


def test_account_snapshot_has_no_login():
    gw = MT5Gateway(MT5Settings(), ServerClock(fixed_offset_hours=0), module=FakeMT5Module())
    gw.connect()
    snap = gw.account()
    assert not hasattr(snap, "login") and not hasattr(snap, "server")


def test_missing_terminal_fails_after_retries(monkeypatch):
    monkeypatch.setattr("mt5bot.gateway.retry.time.sleep", lambda s: None)
    gw = MT5Gateway(MT5Settings(connect_retries=1), None, module=FakeMT5Module(init_ok=False))
    with pytest.raises(TerminalUnavailableError):
        gw.connect()


def test_missing_package_reports_terminal_unavailable():
    import importlib.util

    if importlib.util.find_spec("MetaTrader5") is not None:
        pytest.skip("MetaTrader5 is installed")
    with pytest.raises(TerminalUnavailableError):
        MT5Gateway(MT5Settings(), None)


def test_login_password_passed_but_not_logged():
    mod = FakeMT5Module()
    gw = MT5Gateway(
        MT5Settings(login="5551234", password="pw-xyz", server="JustMarkets-Demo"),
        ServerClock(fixed_offset_hours=0),
        module=mod,
    )
    gw.connect()
    assert mod.init_kwargs["login"] == 5551234
    with pytest.raises(GatewayError):
        MT5Gateway(MT5Settings(login="abc"), None, module=FakeMT5Module()).connect()


def test_mt5_gateway_converts_server_time_to_utc():
    mod = FakeMT5Module(server_offset_hours=2)
    start = datetime(2024, 1, 8, tzinfo=UTC)
    df = synthetic_bars(start, 120)
    mod.set_bars_from_utc("EURUSD", mod.TIMEFRAME_M1, df)
    gw = MT5Gateway(MT5Settings(), ServerClock(fixed_offset_hours=2), module=mod)
    gw.connect()
    out = gw.bars("EURUSD", Timeframe.M1, start, start + timedelta(minutes=60))
    assert len(out) == 60
    assert out["time_utc"].iloc[0] == pd.Timestamp(start)
    assert str(out["time_utc"].dt.tz) == "UTC"


def test_gateway_without_clock_refuses_history():
    gw = MT5Gateway(MT5Settings(), None, module=FakeMT5Module())
    gw.connect()
    with pytest.raises(ConfigError):
        gw.bars("EURUSD", Timeframe.M1, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC))


def test_clock_ny_plus_7_handles_dst():
    clock = ServerClock(timezone=NY_PLUS_7)
    # Winter: NY = UTC-5 -> server = UTC+2. Summer: NY = UTC-4 -> server = UTC+3.
    winter = datetime(2024, 1, 15, 12, tzinfo=UTC)
    summer = datetime(2024, 7, 15, 12, tzinfo=UTC)
    assert clock.utc_to_server_epoch(winter) - int(winter.timestamp()) == 2 * 3600
    assert clock.utc_to_server_epoch(summer) - int(summer.timestamp()) == 3 * 3600
    back = clock.server_epoch_to_utc(pd.Series([clock.utc_to_server_epoch(summer)]))
    assert back[0] == pd.Timestamp(summer)


def test_clock_requires_configuration():
    with pytest.raises(ConfigError):
        ServerClock()


def test_observed_offset():
    now = datetime(2024, 1, 15, 12, tzinfo=UTC)
    assert observed_offset_hours(int(now.timestamp()) + 7200 + 40, now) == 2.0


def test_symbol_resolution():
    avail = [
        symbol_meta("EURUSD.m", "EUR", "USD"),
        symbol_meta("GBPUSD", "GBP", "USD"),
        symbol_meta("XAUUSD.m", "XAU", "USD"),
    ]
    assert resolve_symbol("EURUSD", avail, {}).name == "EURUSD.m"
    assert resolve_symbol("GBPUSD", avail, {}).name == "GBPUSD"
    with pytest.raises(SymbolResolutionError):
        resolve_symbol("USDJPY", avail, {})


def test_symbol_ambiguity_is_an_error():
    avail = [symbol_meta("EURUSD", "EUR", "USD"), symbol_meta("EURUSD.m", "EUR", "USD")]
    with pytest.raises(AmbiguousSymbolError):
        resolve_symbol("EURUSD", avail, {})
    assert resolve_symbol("EURUSD", avail, {"EURUSD": "EURUSD.m"}).name == "EURUSD.m"
    with pytest.raises(SymbolResolutionError):
        resolve_symbol("EURUSD", avail, {"EURUSD": "EURUSDx"})


def test_symbol_currency_mismatch_not_matched():
    avail = [symbol_meta("EURUSD.m", "EUR", "CAD")]
    with pytest.raises(SymbolResolutionError):
        resolve_symbol("EURUSD", avail, {})


def test_retry_and_rate_limiter():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TerminalUnavailableError("x")
        return 42

    slept: list[float] = []
    assert retry(flaky, attempts=3, retry_on=(TerminalUnavailableError,), sleep=slept.append) == 42
    assert slept == [0.5, 1.0]
    t = [0.0]
    rl = RateLimiter(1.0, clock=lambda: t[0], sleep=lambda s: slept.append(s))
    rl.wait()
    t[0] = 0.25
    rl.wait()
    assert slept[-1] == pytest.approx(0.75)
