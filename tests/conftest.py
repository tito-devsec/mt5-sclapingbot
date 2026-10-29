from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from mt5bot.config.settings import Settings
from mt5bot.core.types import Timeframe
from mt5bot.gateway.fake import FakeGateway
from mt5bot.storage.db import open_database
from mt5bot.testing.synthetic import resample_bars, synthetic_bars

START = datetime(2024, 3, 4, 0, 0, tzinfo=UTC)  # a Monday


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith("MT5BOT_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env / config/local.toml from the repo


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        storage={
            "db_path": tmp_path / "t.db",
            "model_dir": tmp_path / "models",
            "report_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
        },
        collection={"request_pause_seconds": 0, "max_bars_per_request": 2000},
        mt5={"server_utc_offset_hours": 0},
    )


@pytest.fixture
def conn():
    c = open_database(":memory:")
    yield c
    c.close()


@pytest.fixture(scope="session")
def m1_bars() -> pd.DataFrame:
    return synthetic_bars(START, 6000, Timeframe.M1, seed=11)


@pytest.fixture
def fake_gateway(m1_bars) -> FakeGateway:
    gw = FakeGateway()
    gw.bar_data[("EURUSD", Timeframe.M1)] = m1_bars
    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
        gw.bar_data[("EURUSD", tf)] = resample_bars(m1_bars, tf)
    return gw
