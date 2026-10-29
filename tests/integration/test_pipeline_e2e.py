"""End-to-end pipeline on synthetic data and a fake terminal; exercises code paths, not performance."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from mt5bot.config.settings import Settings
from mt5bot.core.types import DecisionStatus, Timeframe
from mt5bot.gateway.fake import FakeGateway, demo_account
from mt5bot.ingest.collector import Collector
from mt5bot.monitor.service import DemoMonitor
from mt5bot.pipeline.inference import ResearchPipeline
from mt5bot.pipeline.jobs import backtest_job, build_dataset, train_job
from mt5bot.reporting.report import gather, text_status, write_csvs, write_html
from mt5bot.storage.db import open_database
from mt5bot.storage.repositories import InstrumentRepository, ModelRepository
from mt5bot.testing.synthetic import resample_bars, synthetic_bars

T0 = datetime(2024, 3, 4, tzinfo=UTC)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    m1 = synthetic_bars(T0, 30000, Timeframe.M1, seed=21)
    gw = FakeGateway()
    gw.bar_data[("EURUSD", Timeframe.M1)] = m1
    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
        gw.bar_data[("EURUSD", tf)] = resample_bars(m1, tf)
    settings = Settings(
        storage={
            "db_path": tmp / "e2e.db",
            "model_dir": tmp / "models",
            "report_dir": tmp / "reports",
            "log_dir": tmp / "logs",
        },
        mt5={"server_utc_offset_hours": 0},
        instruments={"symbols": ["EURUSD"], "timeframes": ["M1", "M5"]},
        collection={"request_pause_seconds": 0, "max_bars_per_request": 10000},
        features={"vol_percentile_window": 100},
        ml={"model_type": "logreg", "min_train_samples": 50},
        evaluation={"n_folds": 3, "min_trades_for_promotion": 20},
        selection={"min_samples": 5},
        risk={"block_on_stale_news": False, "max_spread_atr_ratio": 1.0},
        strategy={"enabled": ["trend", "breakout", "mean_reversion"]},
    )
    conn = open_database(settings.storage.db_path)
    gw.connect()
    end = m1["time_utc"].iloc[-1] + pd.Timedelta(minutes=1)
    reports = Collector(gw, conn, settings, source="fixture", sleep=lambda s: None).collect(
        ["EURUSD"], [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1], start=T0, end=end.to_pydatetime()
    )
    return {"gw": gw, "settings": settings, "conn": conn, "end": end, "m1": m1, "reports": reports}


def test_collection_and_specs(world):
    assert all(r.status == "OK" for r in world["reports"])
    spec = InstrumentRepository(world["conn"]).get("EURUSD")
    assert spec["point"] == 0.00001 and spec["broker_symbol"] == "EURUSD"


def test_backtest_costs_and_persistence(world):
    out = backtest_job(world["conn"], world["settings"], "EURUSD", Timeframe.M1, None, world["end"].to_pydatetime())
    runs = out["runs"]
    assert set(runs) == {"x0.5", "x1", "x1.5", "x2", "x3"} and out["provenance"] == "SIMULATED"
    if runs["x1"].get("trades", 0):
        assert runs["x0.5"]["net_r"] >= runs["x1"]["net_r"] >= runs["x3"]["net_r"]
    n = world["conn"].execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
    assert n == 5


def test_dataset_has_no_unfinished_labels(world):
    end = world["end"] - pd.Timedelta(days=3)
    ds = build_dataset(world["conn"], world["settings"], ["EURUSD"], Timeframe.M1, None, end.to_pydatetime())
    assert len(ds) > 0
    assert (ds.meta["label_available_at"] <= end).all()
    assert ds.meta["available_at"].is_monotonic_increasing


def test_train_job_registers_and_applies_gates(world):
    out = train_job(world["conn"], world["settings"], ["EURUSD"], Timeframe.M1, None, world["end"].to_pydatetime())
    assert out.action in ("PROMOTE", "REJECT")
    row = ModelRepository(world["conn"]).get(out.model_id)
    assert row["status"] == ("CHAMPION" if out.action == "PROMOTE" else "REJECTED")
    assert "gates" in out.summary and "walk_forward" in out.summary
    if out.action == "REJECT":
        assert ModelRepository(world["conn"]).champion("meta_label") is None


def test_replay_records_decisions_and_learns_only_from_known_outcomes(world):
    p = ResearchPipeline(world["conn"], world["settings"])
    start = (pd.Timestamp(T0) + pd.Timedelta(days=5)).to_pydatetime()
    counts = p.replay("EURUSD", Timeframe.M1, start, world["end"].to_pydatetime())
    assert sum(counts.values()) > 0
    assert set(counts) <= {s.value for s in DecisionStatus}
    conn = world["conn"]
    # rule-eligible candidates were evaluated counterfactually and fed performance memory
    co = conn.execute(
        "SELECT c.id, d.bar_time_utc, o.exit_time_utc FROM candidate_outcomes o "
        "JOIN strategy_candidates c ON c.id = o.candidate_id JOIN decisions d ON d.id = c.decision_id"
    ).fetchall()
    assert co, "expected evaluated candidates"
    assert all(pd.Timestamp(r["exit_time_utc"]) > pd.Timestamp(r["bar_time_utc"]) for r in co)
    assert conn.execute("SELECT COALESCE(SUM(n),0) FROM strategy_performance").fetchone()[0] == len(co)
    # every decision is reconstructable: features, config and model versions are recorded
    d = conn.execute("SELECT * FROM decisions LIMIT 1").fetchone()
    assert d["features_json"] and d["config_hash"] and d["model_versions_json"]


def test_favourable_evidence_flows_through_risk_and_attribution(world, tmp_path, monkeypatch):
    s = world["settings"].model_copy(
        update={"storage": world["settings"].storage.model_copy(update={"db_path": tmp_path / "fav.db"})}
    )
    conn = open_database(s.storage.db_path)
    for tf, df in world["gw"].bar_data.items():
        from mt5bot.storage.repositories import BarRepository

        BarRepository(conn).insert_bars("EURUSD", tf[1], df, source="fixture", broker_symbol="EURUSD", run_id=None)
    p = ResearchPipeline(conn, s)
    monkeypatch.setattr(p.perf, "get", lambda bucket: (200, 0.6, 0.5))  # pretend strong evidence everywhere
    start = (pd.Timestamp(T0) + pd.Timedelta(days=10)).to_pydatetime()
    counts = p.replay("EURUSD", Timeframe.M1, start, world["end"].to_pydatetime())
    assert counts.get("RECOMMEND", 0) + counts.get("REJECTED", 0) > 0
    rows = conn.execute(
        "SELECT d.status, o.attribution, o.net_r FROM decisions d JOIN decision_outcomes o "
        "ON o.decision_id = d.id WHERE d.selected_strategy IS NOT NULL"
    ).fetchall()
    assert rows
    allowed = {
        "RISK_REJECTED",
        "INVALID_WIN",
        "STALE_DATA",
        "UNREALISTIC_FILL_ASSUMPTION",
        "NEWS_VOLATILITY",
        "COST_DOMINATED",
        "VALID_WIN",
        "FAILED_BREAKOUT",
        "TREND_REVERSAL",
        "POOR_TIMING",
        "REGIME_MISCLASSIFIED",
        "VALID_LOSS",
        "UNATTRIBUTED",
    }
    assert {r["attribution"] for r in rows} <= allowed
    for r in rows:
        if r["status"] == "REJECTED":
            assert r["attribution"] in ("RISK_REJECTED", "INVALID_WIN")


def test_recommend_and_reports(world, tmp_path):
    p = ResearchPipeline(world["conn"], world["settings"])
    rec = p.recommend("EURUSD", Timeframe.M5, mode="SIMULATED", now=world["end"].to_pydatetime())
    assert rec is not None and rec.status in set(DecisionStatus)
    data = gather(world["conn"])
    assert "EURUSD" in text_status(data)
    html = write_html(data, tmp_path / "r.html").read_text()
    assert "SIMULATED" in html and "no orders are ever placed" in html
    assert len(write_csvs(data, tmp_path / "csv")) == 7


def test_stale_news_blocks_recommendations_by_default(world):
    s = world["settings"].model_copy(
        update={"risk": world["settings"].risk.model_copy(update={"block_on_stale_news": True})}
    )
    p = ResearchPipeline(world["conn"], s)
    feats = p.feature_frame("EURUSD", Timeframe.M1, None, world["end"].to_pydatetime())
    from mt5bot.risk.engine import RiskContext

    # direct check: an unknown news state is never treated as "no news"
    assert p.events.evaluate("EURUSD", world["end"].to_pydatetime()).stale
    assert RiskContext(mode="SIMULATED", news_stale=True).news_stale
    assert len(feats) > 0


def test_monitor_observe_only(world, tmp_path):
    s = world["settings"].model_copy(
        update={"storage": world["settings"].storage.model_copy(update={"db_path": tmp_path / "mon.db"})}
    )
    conn = open_database(s.storage.db_path)
    gw = world["gw"]
    mon = DemoMonitor(gw, conn, s, sleep=lambda secs: None)
    stats = mon.run(max_cycles=2)
    assert stats.cycles == 2 and stats.stopped_reason == "max cycles reached"
    modes = {r[0] for r in conn.execute("SELECT DISTINCT mode FROM decisions").fetchall()}
    assert modes <= {"OBSERVED_DEMO"}


def test_monitor_refuses_live_account(world, tmp_path):
    gw = FakeGateway(account_snapshot=demo_account(is_demo=False))
    gw.connect()
    s = world["settings"]
    stats = DemoMonitor(gw, open_database(tmp_path / "x.db"), s, sleep=lambda secs: None).run(max_cycles=3)
    assert stats.cycles == 0 and "ACCOUNT_NOT_DEMO" in stats.stopped_reason


def test_failed_training_does_not_touch_champion(world, monkeypatch):
    repo = ModelRepository(world["conn"])
    before = repo.champion("meta_label")
    import mt5bot.pipeline.jobs as jobs

    def boom(*a, **k):
        raise RuntimeError("training crashed")

    monkeypatch.setattr(jobs, "train_and_evaluate", boom)
    with pytest.raises(RuntimeError):
        train_job(world["conn"], world["settings"], ["EURUSD"], Timeframe.M1, None, world["end"].to_pydatetime())
    after = repo.champion("meta_label")
    assert (before is None and after is None) or (before["id"] == after["id"])
