from datetime import UTC, datetime, timedelta

import pandas as pd

from mt5bot.core.types import Timeframe
from mt5bot.ingest.collector import Collector
from mt5bot.ingest.quality import is_weekend_gap, validate_bars
from mt5bot.storage.repositories import BarRepository, IngestionRepository
from mt5bot.testing.synthetic import synthetic_bars

T0 = datetime(2024, 3, 4, 0, 0, tzinfo=UTC)
FAR_FUTURE = pd.Timestamp("2030-01-01", tz="UTC")


def _issues(res, kind):
    return [i for i in res.issues if i["issue"] == kind]


def test_valid_bars_pass():
    df = synthetic_bars(T0, 100)
    r = validate_bars(df, "EURUSD", Timeframe.M1, FAR_FUTURE)
    assert len(r.clean) == 100 and r.rejected == 0


def test_invalid_ohlc_and_prices_rejected_not_repaired():
    df = synthetic_bars(T0, 10)
    df.loc[2, "high"] = df.loc[2, "low"] - 0.0005
    df.loc[5, "close"] = float("nan")
    df.loc[7, "open"] = -1.0
    r = validate_bars(df, "EURUSD", Timeframe.M1, FAR_FUTURE)
    assert len(r.clean) == 7 and r.rejected == 3
    assert _issues(r, "INVALID_OHLC") and len(_issues(r, "INVALID_PRICE")) == 2


def test_duplicates_identical_and_conflicting():
    df = synthetic_bars(T0, 10)
    dup = pd.concat([df, df.iloc[[3]]], ignore_index=True)
    r = validate_bars(dup, "EURUSD", Timeframe.M1, FAR_FUTURE)
    assert len(r.clean) == 10 and _issues(r, "DUPLICATE")
    conflict = df.iloc[[4]].copy()
    conflict["close"] += 0.0001
    conflict["high"] += 0.0001
    r2 = validate_bars(pd.concat([df, conflict]), "EURUSD", Timeframe.M1, FAR_FUTURE)
    assert len(r2.clean) == 9 and _issues(r2, "CONFLICTING_DUPLICATE")


def test_out_of_order_sorted_and_flagged():
    df = synthetic_bars(T0, 10).sample(frac=1, random_state=1)
    r = validate_bars(df, "EURUSD", Timeframe.M1, FAR_FUTURE)
    assert r.clean["time_utc"].is_monotonic_increasing and _issues(r, "OUT_OF_ORDER")


def test_misaligned_timestamp_rejected():
    df = synthetic_bars(T0, 10, Timeframe.M5)
    df.loc[3, "time_utc"] = df.loc[3, "time_utc"] + pd.Timedelta(minutes=2)
    r = validate_bars(df, "EURUSD", Timeframe.M5, FAR_FUTURE)
    assert len(r.clean) == 9 and _issues(r, "MISALIGNED_TIMESTAMP")


def test_missing_intervals_reported_not_filled():
    df = synthetic_bars(T0, 30).drop(index=range(10, 15)).reset_index(drop=True)
    r = validate_bars(df, "EURUSD", Timeframe.M1, FAR_FUTURE)
    gaps = _issues(r, "GAP_UNEXPECTED")
    assert len(gaps) == 1 and "5 bar(s) missing" in gaps[0]["detail"]
    assert len(r.clean) == 25  # nothing fabricated


def test_weekend_gap_expected():
    assert is_weekend_gap(pd.Timestamp("2024-03-08 20:59", tz="UTC"), pd.Timestamp("2024-03-10 21:00", tz="UTC"))
    assert not is_weekend_gap(pd.Timestamp("2024-03-05 10:00", tz="UTC"), pd.Timestamp("2024-03-05 11:00", tz="UTC"))


def test_forming_candle_excluded():
    df = synthetic_bars(T0, 10)
    now = df["time_utc"].iloc[-1] + pd.Timedelta(seconds=30)  # last bar still forming
    r = validate_bars(df, "EURUSD", Timeframe.M1, now)
    assert len(r.clean) == 9 and r.forming_dropped == 1


def test_unmappable_time_rejected():
    df = synthetic_bars(T0, 5)
    df.loc[2, "time_utc"] = pd.NaT
    r = validate_bars(df, "EURUSD", Timeframe.M1, FAR_FUTURE)
    assert len(r.clean) == 4 and _issues(r, "UNMAPPABLE_SERVER_TIME")


def test_collector_idempotent_and_resumable(fake_gateway, conn, settings, m1_bars):
    fake_gateway.connect()
    col = Collector(fake_gateway, conn, settings, source="fixture", sleep=lambda s: None)
    end = m1_bars["time_utc"].iloc[2999] + pd.Timedelta(minutes=1)
    rep = col.collect(["EURUSD"], [Timeframe.M1], start=T0, end=end.to_pydatetime())[0]
    assert rep.status == "OK" and rep.inserted == 3000
    # resume: only new bars are requested and inserted
    end2 = m1_bars["time_utc"].iloc[-1] + pd.Timedelta(minutes=1)
    rep2 = col.collect(["EURUSD"], [Timeframe.M1], start=T0, end=end2.to_pydatetime())[0]
    assert rep2.inserted == len(m1_bars) - 3000
    rep3 = col.collect(["EURUSD"], [Timeframe.M1], start=T0, end=end2.to_pydatetime())[0]
    assert rep3.inserted == 0
    assert len(BarRepository(conn).load("EURUSD", Timeframe.M1)) == len(m1_bars)
    assert IngestionRepository(conn).checkpoint("EURUSD", Timeframe.M1) == m1_bars["time_utc"].iloc[-1]


def test_collector_retries_then_succeeds(fake_gateway, conn, settings, m1_bars):
    fake_gateway.connect()
    fake_gateway.fail_bars_times = 2
    col = Collector(fake_gateway, conn, settings, source="fixture", sleep=lambda s: None)
    end = m1_bars["time_utc"].iloc[99] + pd.Timedelta(minutes=1)
    rep = col.collect(["EURUSD"], [Timeframe.M1], start=T0, end=end.to_pydatetime())[0]
    assert rep.status == "OK" and rep.inserted == 100


def test_collector_failure_recorded(fake_gateway, conn, settings, m1_bars):
    fake_gateway.connect()
    fake_gateway.fail_bars_times = 99
    col = Collector(fake_gateway, conn, settings, source="fixture", sleep=lambda s: None)
    rep = col.collect(["EURUSD"], [Timeframe.M1], start=T0, end=(T0 + timedelta(hours=1)))[0]
    assert rep.status == "FAILED" and rep.inserted == 0
    runs = IngestionRepository(conn).recent_runs()
    assert runs[0]["status"] == "FAILED" and runs[0]["error_code"] == "GATEWAY_ERROR"


def test_collector_unresolvable_symbol(fake_gateway, conn, settings):
    fake_gateway.connect()
    col = Collector(fake_gateway, conn, settings, source="fixture", sleep=lambda s: None)
    rep = col.collect(["AUDCAD"], [Timeframe.M1], start=T0, end=T0 + timedelta(hours=1))
    assert rep[0].status == "FAILED" and "SYMBOL_UNRESOLVED" in rep[0].error
