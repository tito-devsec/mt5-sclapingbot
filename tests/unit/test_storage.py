import sqlite3
from datetime import UTC, datetime

import pytest

from mt5bot.core.types import Timeframe
from mt5bot.storage.db import connect, current_version, migrate, open_database, transaction
from mt5bot.storage.repositories import BarRepository, DecisionRepository
from mt5bot.testing.synthetic import synthetic_bars


def test_migration_idempotent_and_preserves_data(tmp_path):
    path = tmp_path / "x.db"
    c = open_database(path)
    repo = BarRepository(c)
    bars = synthetic_bars(datetime(2024, 1, 8, tzinfo=UTC), 50)
    assert repo.insert_bars("EURUSD", Timeframe.M1, bars, source="fixture", broker_symbol="EURUSD", run_id=None) == 50
    c.close()
    c2 = connect(path)
    assert migrate(c2, path) == []  # nothing pending
    assert current_version(c2) == 3
    assert len(BarRepository(c2).load("EURUSD", Timeframe.M1)) == 50


def test_duplicate_bars_ignored(conn):
    repo = BarRepository(conn)
    bars = synthetic_bars(datetime(2024, 1, 8, tzinfo=UTC), 30)
    assert repo.insert_bars("EURUSD", Timeframe.M1, bars, source="f", broker_symbol="EURUSD", run_id=None) == 30
    assert repo.insert_bars("EURUSD", Timeframe.M1, bars, source="f", broker_symbol="EURUSD", run_id=None) == 0
    loaded = repo.load("EURUSD", Timeframe.M1)
    assert len(loaded) == 30 and str(loaded["time_utc"].dt.tz) == "UTC"


def test_db_constraint_rejects_invalid_ohlc(conn):
    bars = synthetic_bars(datetime(2024, 1, 8, tzinfo=UTC), 3)
    bars.loc[1, "high"] = bars.loc[1, "low"] - 0.001
    with pytest.raises(sqlite3.IntegrityError):
        BarRepository(conn).insert_bars("EURUSD", Timeframe.M1, bars, source="f", broker_symbol="EURUSD", run_id=None)
    # the whole batch rolled back - no partial write
    assert BarRepository(conn).load("EURUSD", Timeframe.M1).empty


def test_transaction_rollback(conn):
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO feature_sets(hash, version, definition, created_at) VALUES ('h','1','{}','t')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM feature_sets").fetchone()[0] == 0


def test_naive_timestamp_rejected(conn):
    bars = synthetic_bars(datetime(2024, 1, 8, tzinfo=UTC), 3)
    bars["time_utc"] = bars["time_utc"].dt.tz_localize(None)
    with pytest.raises(ValueError):
        BarRepository(conn).insert_bars("EURUSD", Timeframe.M1, bars, source="f", broker_symbol="EURUSD", run_id=None)


def test_decision_unique_per_bar_and_atomic(conn):
    repo = DecisionRepository(conn)
    d = {
        "decided_at": "2024-01-08T00:01:00+00:00",
        "bar_time_utc": "2024-01-08T00:00:00+00:00",
        "symbol": "EURUSD",
        "timeframe": "M1",
        "mode": "SIMULATED",
        "status": "NO_TRADE",
        "regime": "RANGE",
        "rationale": "test",
        "config_hash": "abc",
    }
    assert repo.save(d, [], [{"code": "RISK_OK", "passed": True}], []) is not None
    assert repo.save(d, [], [], []) is None
    with pytest.raises(sqlite3.IntegrityError):
        repo.save({**d, "bar_time_utc": "2024-01-08T00:01:00+00:00", "status": "BOGUS"}, [], [], [])
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_only_one_champion_per_kind(conn):
    base = {
        "kind": "meta_label",
        "model_type": "logreg",
        "artifact_path": "p",
        "artifact_sha256": "s",
        "feature_set": "f",
        "feature_names": "[]",
        "train_start": "a",
        "train_end": "b",
        "dataset_fingerprint": "d",
        "params_json": "{}",
        "metrics_json": "{}",
        "seed": 1,
        "library_versions": "{}",
        "created_at": "t",
    }
    from mt5bot.storage.repositories import ModelRepository

    r = ModelRepository(conn)
    r.add({**base, "id": "m1", "status": "CHAMPION"})
    with pytest.raises(sqlite3.IntegrityError):
        r.add({**base, "id": "m2", "status": "CHAMPION"})
