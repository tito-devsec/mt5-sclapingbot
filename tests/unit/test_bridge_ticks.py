"""Bridge file exchange with the MQL5 components and tick-level stop/target resolution."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from mt5bot.backtest.simulator import BarArrays, CostModel, Sizing, simulate_trade
from mt5bot.backtest.ticks import decision_resolver, first_touch, quotes_resolver
from mt5bot.bridge.files import (
    DECISION_HEADER,
    DecisionRow,
    decision_lines,
    import_resolutions,
    import_tick_file,
    safe_text,
    watch_lines,
)
from mt5bot.config.settings import Settings
from mt5bot.core.types import Direction, InstrumentSpec, Timeframe
from mt5bot.gateway.clock import ServerClock
from mt5bot.storage.repositories import DecisionRepository, QuoteRepository

EURUSD = InstrumentSpec("EURUSD", "EUR", "USD", 0.00001, 5, 100_000.0)
T0 = pd.Timestamp("2024-03-05 10:00", tz="UTC")
CLOCK = ServerClock(fixed_offset_hours=2)


TICK_START = T0 + pd.Timedelta(minutes=2)


def ticks(rows, start=TICK_START):
    return pd.DataFrame(
        {
            "time_utc": [start + pd.Timedelta(seconds=i) for i in range(len(rows))],
            "bid": [r[0] for r in rows],
            "ask": [r[1] for r in rows],
        }
    )


def test_first_touch_long_and_short():
    t = ticks([(1.1000, 1.1001), (1.1012, 1.1013), (1.0985, 1.0986)])
    assert first_touch(t, Direction.LONG, stop=1.0990, target=1.1010) == "TARGET"
    t2 = ticks([(1.1000, 1.1001), (1.0985, 1.0986), (1.1012, 1.1013)])
    assert first_touch(t2, Direction.LONG, stop=1.0990, target=1.1010) == "STOP"
    # shorts exit at the ask: the ask reaches the target first here
    t3 = ticks([(1.1000, 1.1002), (1.0988, 1.0989), (1.1012, 1.1014)])
    assert first_touch(t3, Direction.SHORT, stop=1.1010, target=1.0990) == "TARGET"
    assert first_touch(ticks([(1.1, 1.1001)]), Direction.LONG, 1.0, 1.2) is None
    assert first_touch(ticks([]), Direction.LONG, 1.0, 1.2) is None


def ambiguous_bars():
    rows = [(1.1, 1.1, 1.1, 1.1), (1.10000, 1.10010, 1.09990, 1.1), (1.10000, 1.10300, 1.09800, 1.10000)]
    rows += [(1.1, 1.1, 1.1, 1.1)] * 4
    return pd.DataFrame(
        {
            "time_utc": [T0 + i * pd.Timedelta(minutes=1) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "tick_volume": 100,
            "spread_points": 10,
            "real_volume": 0,
        }
    )


def run(resolver):
    return simulate_trade(
        BarArrays(ambiguous_bars(), Timeframe.M1),
        0,
        Direction.LONG,
        0.0010,
        0.0020,
        5,
        EURUSD,
        CostModel(slippage_points=2),
        Sizing(1000, 0.5, 10),
        resolver=resolver,
    )


def test_simulator_uses_tick_verdict_on_ambiguous_bar():
    assumed = run(None)
    assert assumed.ambiguous and assumed.resolution == "ASSUMED_STOP" and assumed.exit_reason == "STOP"
    tp = run(lambda s, e, stop, target: "TARGET")
    assert tp.resolution == "TICKS_TARGET" and tp.exit_reason == "TARGET" and tp.net_r > 0 > assumed.net_r
    sl = run(lambda s, e, stop, target: "STOP")
    assert sl.resolution == "TICKS_STOP" and sl.exit_reason == "STOP"


def test_quotes_resolver_reads_the_ambiguous_bar_window(conn):
    bar_start = T0 + pd.Timedelta(minutes=2)
    r0 = run(None)
    stop, target = r0.entry_price - 0.0010, r0.entry_price + 0.0020
    QuoteRepository(conn).insert(
        "EURUSD",
        ticks([(target + 0.0001, target + 0.0002), (stop - 0.0001, stop)], start=bar_start + pd.Timedelta(seconds=5)),
        source="test",
    )
    res = simulate_trade(
        BarArrays(ambiguous_bars(), Timeframe.M1),
        0,
        Direction.LONG,
        0.0010,
        0.0020,
        5,
        EURUSD,
        CostModel(slippage_points=2),
        Sizing(1000, 0.5, 10),
        resolver=quotes_resolver(conn, "EURUSD", Direction.LONG),
    )
    assert res.resolution == "TICKS_TARGET"


def _decision(conn, bar_time="2024-03-05T10:00:00+00:00"):
    d = {
        "decided_at": bar_time,
        "bar_time_utc": bar_time,
        "symbol": "EURUSD",
        "timeframe": "M1",
        "mode": "OBSERVED_DEMO",
        "status": "RECOMMEND",
        "regime": "TREND_UP",
        "rationale": "t",
        "config_hash": "h",
        "selected_strategy": "trend",
        "direction": "LONG",
        "entry_ref": 1.1,
        "stop_distance": 0.001,
        "target_distance": 0.002,
        "max_hold_bars": 5,
    }
    return DecisionRepository(conn).save(d, [], [], [])


def test_import_resolutions_and_decision_precedence(conn, tmp_path):
    did = _decision(conn)
    hit_utc = T0 + pd.Timedelta(minutes=2, seconds=30)
    hit_server_msc = (CLOCK.utc_to_server_epoch(hit_utc.to_pydatetime()) * 1000) + 250
    f = tmp_path / "resolved_EURUSD.csv"
    f.write_text(
        "id,entry_msc,entry_price,first_hit,hit_msc,hit_price,ticks\r\n"
        f"{did},0,1.10012,TP,{hit_server_msc},1.10212,57\r\n"
        "999,0,1,TP,0,1,1\r\nbad,line\r\n",
        encoding="ascii",
    )
    assert import_resolutions(conn, f, CLOCK) == 1
    assert import_resolutions(conn, f, CLOCK) == 0  # idempotent
    row = conn.execute("SELECT * FROM tick_resolutions WHERE decision_id = ?", (did,)).fetchone()
    assert row["first_hit"] == "TP" and pd.Timestamp(row["hit_time_utc"]) == hit_utc + pd.Timedelta(milliseconds=250)
    resolver = decision_resolver(conn, did, fallback=lambda *a: "STOP")
    bar = T0 + pd.Timedelta(minutes=2)
    assert resolver(bar, bar + pd.Timedelta(minutes=1), 1.0, 2.0) == "TARGET"
    other = T0 + pd.Timedelta(minutes=4)
    assert resolver(other, other + pd.Timedelta(minutes=1), 1.0, 2.0) == "STOP"  # EA hit is not in this bar


def test_import_tick_file_converts_server_time(conn, tmp_path):
    t_utc = T0 + pd.Timedelta(minutes=3)
    msc = CLOCK.utc_to_server_epoch(t_utc.to_pydatetime()) * 1000 + 500
    f = tmp_path / "ticks_EURUSD_20240305.csv"
    f.write_text(f"time_msc,bid,ask\r\n{msc},1.10001,1.10011\r\n{msc + 1},1.10002,1.10012\r\n", encoding="ascii")
    assert import_tick_file(conn, "EURUSD", f, CLOCK) == 2
    row = conn.execute("SELECT time_utc FROM quotes ORDER BY time_utc LIMIT 1").fetchone()
    assert pd.Timestamp(row["time_utc"]) == t_utc + pd.Timedelta(milliseconds=500)


def test_decision_and_watch_lines():
    rows = [
        DecisionRow(7, T0, "RECOMMEND", "TREND_UP", "trend", "LONG", 1.1, 0.001, 0.002, 5, [], "ok, fine é"),
        DecisionRow(
            8,
            T0,
            "REJECTED",
            "RANGE",
            "breakout",
            "SHORT",
            1.1,
            0.001,
            0.002,
            5,
            ["RISK_SPREAD_TOO_WIDE", "RISK_EVENT_WINDOW"],
            "x",
        ),
        DecisionRow(9, T0, "NO_TRADE", "RANGE", None, None, 1.1, None, None, None, [], "none"),
    ]
    lines = decision_lines(rows, Timeframe.M1, CLOCK, 5)
    assert lines[0] == DECISION_HEADER and all(len(x.split(",")) == 14 for x in lines)
    f = lines[1].split(",")
    assert int(f[1]) - int(f[2]) == 7200  # server time is UTC+2 here
    assert f[8] == "1.09900" and f[9] == "1.10200"  # long: stop below, target above
    s = lines[2].split(",")
    assert s[8] == "1.10100" and s[9] == "1.09800" and s[12] == "RISK_SPREAD_TOO_WIDE|RISK_EVENT_WINDOW"
    assert lines[3].split(",")[8] == ""  # no levels for NO_TRADE
    assert lines[1].isascii()
    w = watch_lines(rows, Timeframe.M1, CLOCK, T0 + pd.Timedelta(minutes=2), 1e-5, 2, 5)
    assert len(w) == 3  # header + two selected candidates still inside their window
    assert watch_lines(rows, Timeframe.M1, CLOCK, T0 + pd.Timedelta(hours=1), 1e-5, 2, 5) == [w[0]]


def test_safe_text():
    assert safe_text("a,b\nc é <x>") == "a;b c   <x>"


def test_export_and_monitor_write_bridge_files(tmp_path, m1_bars):
    from mt5bot.bridge.service import export_state, import_results
    from mt5bot.gateway.fake import FakeGateway
    from mt5bot.monitor.service import DemoMonitor
    from mt5bot.storage.db import open_database
    from mt5bot.testing.synthetic import resample_bars

    bdir = tmp_path / "bridge"
    s = Settings(
        storage={"db_path": tmp_path / "b.db", "model_dir": tmp_path / "m"},
        mt5={"server_utc_offset_hours": 0},
        instruments={"symbols": ["EURUSD"], "timeframes": ["M1"]},
        collection={"request_pause_seconds": 0},
        features={"vol_percentile_window": 100},
        bridge={"directory": bdir},
        risk={"block_on_stale_news": False},
    )
    conn = open_database(s.storage.db_path)
    gw = FakeGateway()
    gw.bar_data[("EURUSD", Timeframe.M1)] = m1_bars
    gw.bar_data[("EURUSD", Timeframe.M5)] = resample_bars(m1_bars, Timeframe.M5)
    gw.connect()
    clock = ServerClock(fixed_offset_hours=0)
    stats = DemoMonitor(gw, conn, s, sleep=lambda x: None, clock=clock).run(max_cycles=1)
    assert stats.bridge_files == 3
    for name in ("decisions_EURUSD_M1.csv", "watch_EURUSD.csv", "status.txt"):
        assert (bdir / name).exists(), name
    status = (bdir / "status.txt").read_text()
    assert "execution=DISABLED" in status and "mode=OBSERVED_DEMO" in status
    rep = export_state(conn, s, clock, pd.Timestamp(datetime(2024, 3, 9, tzinfo=UTC)))
    assert len(rep.files_written) == 3
    (bdir / "heartbeat_EURUSD.txt").write_text("server_time=1\r\ntrade_mode=DEMO\r\n")
    assert import_results(conn, s, clock).heartbeats["EURUSD"].startswith("server_time=1")


def test_bridge_disabled_or_unknown_dir(tmp_path, monkeypatch):
    from mt5bot.bridge.files import bridge_dir
    from mt5bot.config.settings import BridgeSettings

    monkeypatch.delenv("APPDATA", raising=False)
    assert bridge_dir(BridgeSettings()) is None
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert bridge_dir(BridgeSettings()) == tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files" / "mt5bot"


def test_collect_ticks_via_python_api(conn, settings):
    from mt5bot.gateway.fake import FakeGateway
    from mt5bot.pipeline.ticks_job import collect_ticks

    gw = FakeGateway()
    gw.quote_data["EURUSD"] = ticks([(1.1, 1.1001), (1.1002, 1.1003)], start=T0)
    gw.connect()
    rep = collect_ticks(gw, conn, settings, "EURUSD", T0.to_pydatetime(), (T0 + pd.Timedelta(hours=1)).to_pydatetime())
    assert rep.ticks_inserted == 2


@pytest.mark.parametrize("bad", ["", "x"])
def test_invalid_resolution_lines_ignored(conn, tmp_path, bad):
    did = _decision(conn)
    f = tmp_path / "r.csv"
    f.write_text(f"header\r\n{did},0,1,MAYBE,0,1,1\r\n{bad}\r\n", encoding="ascii")
    assert import_resolutions(conn, f, CLOCK) == 0


def test_outcome_uses_ea_tick_verdict(conn):
    import json

    from mt5bot.config.settings import SelectionSettings
    from mt5bot.memory.outcomes import evaluate_pending
    from mt5bot.storage.repositories import BarRepository

    bars = ambiguous_bars()
    BarRepository(conn).insert_bars("EURUSD", Timeframe.M1, bars, source="t", broker_symbol="EURUSD", run_id=None)
    did = _decision(conn)
    hit = (T0 + pd.Timedelta(minutes=2, seconds=20)).isoformat()
    conn.execute(
        "INSERT INTO tick_resolutions(decision_id, source, first_hit, hit_time_utc, ticks_seen, imported_at) "
        "VALUES (?, 'MT5_EA', 'TP', ?, 10, 'now')",
        (did, hit),
    )

    def spec_for(symbol):
        return EURUSD

    evaluate_pending(
        conn,
        spec_for=spec_for,
        costs_for=lambda s: CostModel(slippage_points=2),
        sizing=Sizing(1000, 0.5, 10),
        selection=SelectionSettings(),
        now=T0 + pd.Timedelta(hours=2),
    )
    out = conn.execute("SELECT * FROM decision_outcomes WHERE decision_id = ?", (did,)).fetchone()
    ev = json.loads(out["evidence_json"])
    assert out["exit_reason"] == "TARGET" and ev["resolution"] == "TICKS_TARGET" and ev["ambiguous_bar"] is True


def test_partial_last_tick_line_ignored(conn, tmp_path):
    msc = CLOCK.utc_to_server_epoch(T0.to_pydatetime()) * 1000
    f = tmp_path / "ticks_EURUSD_x.csv"
    f.write_text(f"time_msc,bid,ask\r\n{msc},1.1,1.1001\r\n{msc + 5},1.10", encoding="ascii")
    assert import_tick_file(conn, "EURUSD", f, CLOCK) == 1
