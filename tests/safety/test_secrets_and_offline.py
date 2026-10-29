import io
import logging
import socket

import pytest

from mt5bot.config.settings import Settings
from mt5bot.core.logging import JsonFormatter, RedactingFilter, SecretRegistry, register_settings_secrets


def test_secrets_never_reach_logs():
    s = Settings(mt5={"login": "77123456", "password": "pa55-Word!"}, llm={"api_key": "sk-ant-test-0123456789abcdef"})
    register_settings_secrets(s)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(RedactingFilter())
    h.setFormatter(JsonFormatter())
    log = logging.getLogger("secret-test")
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.info("connecting with password=pa55-Word! login 77123456")
    log.info("key sk-ant-test-0123456789abcdef", extra={"ctx": {"auth": "pa55-Word!"}})
    try:
        raise RuntimeError("failed for 77123456 / pa55-Word!")
    except RuntimeError:
        log.exception("boom")
    out = buf.getvalue()
    for secret in ("pa55-Word!", "77123456", "sk-ant-test-0123456789abcdef"):
        assert secret not in out
    assert "REDACTED" in out
    assert "pa55-Word!" not in repr(s) and "77123456" not in s.model_dump_json()


def test_scrub_patterns():
    assert "abc123" not in SecretRegistry.scrub("api_key=abc123")


def test_local_inference_needs_no_network(monkeypatch, tmp_path):
    """Decision-making with news and LLM disabled must not open any socket."""
    from datetime import UTC, datetime

    import pandas as pd

    from mt5bot.core.types import Timeframe
    from mt5bot.pipeline.inference import ResearchPipeline
    from mt5bot.storage.db import open_database
    from mt5bot.storage.repositories import BarRepository
    from mt5bot.testing.synthetic import resample_bars, synthetic_bars

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    s = Settings(
        storage={"db_path": tmp_path / "x.db", "model_dir": tmp_path / "m"}, features={"vol_percentile_window": 100}
    )
    conn = open_database(s.storage.db_path)
    m1 = synthetic_bars(datetime(2024, 3, 4, tzinfo=UTC), 3000)
    BarRepository(conn).insert_bars("EURUSD", Timeframe.M1, m1, source="f", broker_symbol="EURUSD", run_id=None)
    BarRepository(conn).insert_bars(
        "EURUSD", Timeframe.M5, resample_bars(m1, Timeframe.M5), source="f", broker_symbol="EURUSD", run_id=None
    )
    end = (m1["time_utc"].iloc[-1] + pd.Timedelta(minutes=1)).to_pydatetime()
    rec = ResearchPipeline(conn, s).recommend("EURUSD", Timeframe.M1, mode="SIMULATED", now=end)
    assert rec is not None


def test_gitignore_protects_sensitive_files():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for path in (".env", "data/mt5bot.db", "models/x.skops", "logs/a.log", "reports/report.html", "config/local.toml"):
        r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=root, check=False)
        if r.returncode == 128:
            pytest.skip("not a git checkout")
        assert r.returncode == 0, f"{path} is not ignored"
