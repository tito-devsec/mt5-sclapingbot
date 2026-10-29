import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest

from mt5bot import cli
from mt5bot.core.types import Timeframe
from mt5bot.gateway.fake import FakeGateway, demo_account
from mt5bot.storage.db import current_version, migrate, open_database
from mt5bot.storage.repositories import BarRepository
from mt5bot.testing.synthetic import resample_bars, synthetic_bars

T0 = datetime(2024, 3, 4, tzinfo=UTC)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    toml = tmp_path / "local.toml"
    toml.write_text(
        f"""[mt5]
server_utc_offset_hours = 0
[instruments]
symbols = ["EURUSD"]
timeframes = ["M1", "M5"]
[storage]
db_path = "{(tmp_path / "cli.db").as_posix()}"
model_dir = "{(tmp_path / "models").as_posix()}"
report_dir = "{(tmp_path / "reports").as_posix()}"
log_dir = "{(tmp_path / "logs").as_posix()}"
[collection]
request_pause_seconds = 0
[features]
vol_percentile_window = 100
[ml]
min_train_samples = 50
[logging]
level = "WARNING"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MT5BOT_CONFIG_FILE", str(toml))
    m1 = synthetic_bars(T0, 8000, seed=4)
    gw = FakeGateway()
    gw.bar_data[("EURUSD", Timeframe.M1)] = m1
    for tf in (Timeframe.M5, Timeframe.M15):
        gw.bar_data[("EURUSD", tf)] = resample_bars(m1, tf)
    monkeypatch.setattr(cli, "_gateway", lambda s: gw)
    end = (m1["time_utc"].iloc[-1] + pd.Timedelta(minutes=1)).isoformat()
    return {"tmp": tmp_path, "gw": gw, "end": end}


def test_cli_workflow(cfg, capsys):
    assert cli.main(["collect", "--from", T0.isoformat(), "--to", cfg["end"]]) == 0
    assert "inserted=8000" in capsys.readouterr().out
    assert cli.main(["inspect", "--symbol", "EURUSD"]) == 0
    assert cli.main(["backtest", "--symbol", "EURUSD", "--timeframe", "M1", "--to", cfg["end"]]) == 0
    assert "SIMULATED" in capsys.readouterr().out
    assert cli.main(["train", "--timeframe", "M1", "--to", cfg["end"]]) == 0
    assert cli.main(["recommend", "--symbol", "EURUSD", "--timeframe", "M1", "--at", cfg["end"]]) == 0
    assert "No order was or will be sent" in capsys.readouterr().out
    assert cli.main(["outcomes"]) == 0
    assert cli.main(["models", "list"]) == 0
    assert cli.main(["status"]) == 0
    assert cli.main(["report"]) == 0
    assert (cfg["tmp"] / "reports" / "report.html").exists()
    assert cli.main(["features", "docs"]) == 0
    assert cli.main(["news", "refresh"]) == 2  # no provider enabled -> explicit validation exit code


def test_cli_check_and_live_account_refusal(cfg, capsys):
    assert cli.main(["check"]) == 0
    assert "ACCOUNT_DEMO" in capsys.readouterr().out
    cfg["gw"].account_snapshot = demo_account(is_demo=False)
    assert cli.main(["check"]) == 2
    assert cli.main(["collect"]) == 2  # collection refuses a non-demo account
    assert "ACCOUNT_NOT_DEMO" in capsys.readouterr().err


def test_cli_rejects_bad_config(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[risk]\nrisk_per_trade_pct = 50\n", encoding="utf-8")
    monkeypatch.setenv("MT5BOT_CONFIG_FILE", str(bad))
    assert cli.main(["status"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_upgrade_existing_database_preserves_data(tmp_path):
    path = tmp_path / "old.db"
    conn = open_database(path)
    BarRepository(conn).insert_bars(
        "EURUSD", Timeframe.M1, synthetic_bars(T0, 25), source="f", broker_symbol="EURUSD", run_id=None
    )
    # simulate a database created before migrations 0002 and 0003 existed
    conn.execute("DROP TABLE tick_resolutions")
    conn.execute("DROP INDEX ix_quotes_time")
    conn.execute("DROP TABLE candidate_outcomes")
    conn.execute("DROP TABLE instrument_specs")
    conn.execute("DELETE FROM schema_version WHERE version > 1")
    conn.close()
    raw = sqlite3.connect(path)
    cols = [r[1] for r in raw.execute("PRAGMA table_info(strategy_candidates)")]
    raw.close()
    if "rule_eligible" in cols:  # rebuild the v1 shape of strategy_candidates
        raw = sqlite3.connect(path)
        raw.executescript("""
            CREATE TABLE sc_old AS SELECT id, decision_id, strategy, strategy_version, direction, eligible,
              eligibility_reason, probability, lcb_reward, score, sample_size FROM strategy_candidates;
            DROP TABLE strategy_candidates; ALTER TABLE sc_old RENAME TO strategy_candidates;""")
        raw.close()
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    assert current_version(conn) == 1
    applied = migrate(conn, path)
    assert applied == ["0002_instrument_specs.sql", "0003_candidate_outcomes.sql", "0004_tick_resolutions.sql"]
    assert current_version(conn) == 4
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 25
    assert list(tmp_path.glob("old.pre-v0002-*.bak")), "a backup is taken before upgrading"
