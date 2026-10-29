import pytest
from pydantic import ValidationError

from mt5bot.config.settings import Settings, load_settings
from mt5bot.core.errors import ConfigError


def test_defaults_load():
    s = load_settings()
    assert s.instruments.symbols == ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    assert s.mt5.require_demo is True
    assert s.risk.reference_balance_usd == 1000.0
    assert s.llm.enabled is False


def test_demo_requirement_cannot_be_disabled():
    with pytest.raises(ValidationError):
        Settings(mt5={"require_demo": False})


def test_unknown_key_rejected():
    with pytest.raises(ConfigError, match=r"(?i)extra"):
        load_settings(risk={"risk_per_trade_pcnt": 1.0})


@pytest.mark.parametrize(
    "bad",
    [
        {"risk": {"risk_per_trade_pct": -1}},
        {"risk": {"vol_percentile_min": 0.9, "vol_percentile_max": 0.5}},
        {"instruments": {"symbols": ["EUR/USD"]}},
        {"news": {"request_timeout_seconds": 0}},
        {"monitor": {"interval_seconds": 1}},
    ],
)
def test_invalid_values_rejected(bad):
    with pytest.raises(ConfigError):
        load_settings(**bad)


def test_config_error_does_not_echo_secret_values():
    with pytest.raises(ConfigError) as exc:
        load_settings(mt5={"login": "secret-login-123", "timeout_ms": "not-a-number"})
    assert "secret-login-123" not in str(exc.value)


def test_env_override_and_secret(monkeypatch):
    monkeypatch.setenv("MT5BOT_RISK__RISK_PER_TRADE_PCT", "0.25")
    monkeypatch.setenv("MT5BOT_MT5__PASSWORD", "hunter2-pw")
    s = load_settings()
    assert s.risk.risk_per_trade_pct == 0.25
    assert "hunter2-pw" not in repr(s)
