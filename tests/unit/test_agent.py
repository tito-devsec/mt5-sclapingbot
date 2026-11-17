"""Agent tests: structure features, techniques, embeddings, vector memory, skills, recall, policy and learning."""

import os
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mt5bot.agent.catalogue import CATALOGUE, catalogue_markdown, counts
from mt5bot.agent.core import TradingAgent
from mt5bot.agent.knowledge import build_playbook, load_playbook, save_playbook
from mt5bot.agent.policy import select_with_agent
from mt5bot.agent.recall import EPISODES, EpisodeRecall, OnlineLearner, learner_input, summarize
from mt5bot.agent.skills import SkillBook, SkillKey
from mt5bot.agent.user_trades import import_user_trades, pair_deals
from mt5bot.config.settings import AgentSettings, MemorySettings, RiskSettings, Settings
from mt5bot.core.errors import ExecutionDisabledError, StorageError
from mt5bot.core.types import DecisionStatus, Direction, Timeframe, default_spec, research_label
from mt5bot.features import structure
from mt5bot.features.pipeline import FeatureConfig, build_features
from mt5bot.gateway.fake import FakeGateway, FakeMT5Module
from mt5bot.gateway.mt5_gateway import ReadOnlyMT5
from mt5bot.memory.embed import STATE_DIM, HashingEmbedder, state_embedding
from mt5bot.memory.vector import Hit, Item, PgVectorStore, SqliteVectorStore, open_vector_store
from mt5bot.pipeline.inference import ResearchPipeline
from mt5bot.risk.engine import RiskCandidate, RiskContext, RiskEngine
from mt5bot.storage.db import open_database
from mt5bot.storage.repositories import BarRepository, ModelRepository
from mt5bot.strategies.techniques import ENTRY_RULES, EXIT_SHAPES, TechniqueSet, entry_of
from mt5bot.testing.synthetic import resample_bars, synthetic_bars

T0 = datetime(2024, 3, 4, tzinfo=UTC)
NOW = pd.Timestamp("2024-06-01", tz="UTC")
PG_DSN = os.environ.get("MT5BOT_TEST_PG_DSN")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def feats():
    b = synthetic_bars(T0, 3000, seed=5)
    f = build_features(b, Timeframe.M1, 1e-5, resample_bars(b, Timeframe.M5), FeatureConfig(vol_percentile_window=100))
    return f


# ---------------- structure features and techniques ----------------
def test_candlestick_known_cases():
    o = pd.Series([1.10, 1.09, 1.100, 1.1000])
    h = pd.Series([1.11, 1.12, 1.101, 1.1005])
    lo = pd.Series([1.08, 1.085, 1.090, 1.0995])
    c = pd.Series([1.09, 1.115, 1.1005, 1.1000])
    cs = structure.candlesticks(o, h, lo, c)
    assert cs["cs_bull_engulf"].tolist()[1] == 1.0  # bearish bar then a bullish bar covering its body
    assert cs["cs_pin_bull"].tolist()[2] == 1.0  # long lower wick, small body near the top
    assert cs["cs_doji"].tolist()[3] == 1.0 and cs["cs_inside"].tolist()[3] == 1.0
    assert np.isnan(cs["cs_bull_engulf"].iloc[0])


def test_swing_levels_are_confirmed_late():
    h = pd.Series([1, 2, 3, 9, 3, 2, 1, 1, 1, 1], dtype=float)
    last, _ = structure._confirmed_pivots(h, 3, True)
    assert last.iloc[:6].isna().all() and last.iloc[6] == 9.0  # the high at bar 3 is only known at bar 6


def test_technique_set_generates_every_profile(feats):
    from mt5bot.regime.rules import classify_rules

    ts = TechniqueSet([30, 60])
    assert len(ts.names) == len(ENTRY_RULES) * len(EXIT_SHAPES) * 2 == 120
    c = ts.candidates(feats, classify_rules(feats)["regime"], Timeframe.M1)
    assert not c.empty and set(c["strategy"]) <= set(ts.names)
    one = c[(c["time_utc"] == c["time_utc"].iloc[0]) & (c["direction"] == c["direction"].iloc[0])]
    assert one.groupby(one["strategy"].map(entry_of)).size().iloc[0] == 12  # one signal, twelve exit profiles
    assert (c["target_distance"] > c["stop_distance"]).all()
    assert c.loc[c["regime"] == "UNCERTAIN", "eligible"].sum() == 0


# ---------------- embeddings ----------------
def test_state_embedding_is_stable_and_safe():
    f = {"rsi_14": 70.0, "adx_14": 30.0, "in_london": 1.0, "hour_utc": 9.0, "mom_10": float("nan"), "x": None}
    v = state_embedding(f, "TREND_UP")
    assert v.shape == (STATE_DIM,) and np.isfinite(v).all() and abs(float(np.linalg.norm(v)) - 1) < 1e-6
    assert np.array_equal(v, state_embedding(dict(f), "TREND_UP"))
    near = state_embedding({**f, "rsi_14": 68.0}, "TREND_UP")
    far = state_embedding({"rsi_14": 20.0, "adx_14": 10.0, "in_asia": 1.0, "hour_utc": 2.0}, "RANGE")
    assert float(v @ near) > float(v @ far)


def test_hashing_embedder():
    e = HashingEmbedder(128)
    a, b, c = e.embed(
        [
            "sr_bounce worked in RANGE during london",
            "sr_bounce worked in RANGE during asia",
            "macd_momentum failed in TREND_UP",
        ]
    )
    assert a.shape == (128,) and float(a @ b) > float(a @ c)
    assert np.array_equal(e.embed(["x y"]), e.embed(["x y"])) and e.embed([]).shape == (0, 128)


# ---------------- vector stores ----------------
def _items():
    rng = np.random.default_rng(0)
    base = rng.normal(size=8).astype(np.float32)
    items = [
        Item(1, base, {"symbol": "EURUSD", "direction": "LONG", "reward": 1.0}, T0),
        Item(2, base + 0.05, {"symbol": "EURUSD", "direction": "LONG", "reward": -1.0}, T0),
        Item(3, -base, {"symbol": "EURUSD", "direction": "LONG", "reward": 0.5}, T0),
        Item(4, base, {"symbol": "GBPUSD", "direction": "LONG", "reward": 2.0}, T0),
        Item(5, base, {"symbol": "EURUSD", "direction": "LONG", "reward": 3.0}, datetime(2024, 5, 1, tzinfo=UTC)),
    ]
    return base, items


def _check_store(store):
    base, items = _items()
    assert store.add_many("episodes", items) == 5 and store.add_many("episodes", items) == 0
    assert store.count("episodes") == 5
    hits = store.search("episodes", base, 10, datetime(2024, 4, 1, tzinfo=UTC), {"symbol": "EURUSD"})
    assert [h.item_id for h in hits] == [1, 2, 3]  # most similar first; future item 5 and GBPUSD excluded
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-5) and hits[2].similarity == pytest.approx(-1.0, abs=1e-5)
    assert hits[0].metadata["reward"] == 1.0
    assert next(h.item_id for h in store.search("episodes", base, 1, datetime(2024, 6, 1, tzinfo=UTC), None)) in (
        1,
        4,
        5,
    )
    assert store.search("episodes", base, 5, datetime(2020, 1, 1, tzinfo=UTC), None) == []


def test_sqlite_vector_store(conn):
    store = SqliteVectorStore(conn)
    _check_store(store)
    again = SqliteVectorStore(conn)  # a new instance reloads from the database
    assert again.count("episodes") == 5
    with pytest.raises(StorageError):
        store.count("bad name; drop")


@pytest.mark.skipif(not PG_DSN, reason="set MT5BOT_TEST_PG_DSN to run pgvector tests")
def test_pgvector_store():
    import psycopg

    with psycopg.connect(PG_DSN, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS mt5bot_mem_episodes")
    store = PgVectorStore(PG_DSN, {"episodes": 8})
    try:
        _check_store(store)
    finally:
        store.close()


def test_pgvector_requires_dsn_and_reports_failure(conn):
    with pytest.raises(StorageError, match="dsn"):
        open_vector_store(conn, "pgvector", None, {"episodes": 8})
    pytest.importorskip("psycopg")
    with pytest.raises(StorageError) as e:
        open_vector_store(conn, "pgvector", "postgresql://u:secretpw@127.0.0.1:1/x", {"episodes": 8})
    assert "secretpw" not in str(e.value)


# ---------------- skills ----------------
KEY = SkillKey("sr_bounce|s1_t2_h30", "EURUSD", "M1", "RANGE", "london")


def _feed(book, rewards, start=NOW, key=KEY):
    state = ""
    for i, r in enumerate(rewards):
        state = book.observe(key, float(r), start + pd.Timedelta(minutes=i))
    return state


def test_skill_needs_discovery_then_fresh_confirmation(conn):
    book = SkillBook(conn, AgentSettings())
    rng = np.random.default_rng(1)
    good = rng.normal(0.6, 1.0, 200)
    assert _feed(book, good[:29]) == "EXPERIMENTAL"  # below the minimum sample size
    state = _feed(book, good[29:45], NOW + pd.Timedelta(hours=1))
    assert state == "PROBATION"  # discovered, but not usable yet
    assert _feed(book, good[45:100], NOW + pd.Timedelta(hours=2)) == "VALIDATED"
    events = [e["to_state"] for e in book.events()]
    assert events == ["VALIDATED", "PROBATION"] and book.counts()["VALIDATED"] == 1


def test_skill_is_retired_when_it_stops_working_and_reevaluated_later(conn):
    book = SkillBook(conn, AgentSettings())
    rng = np.random.default_rng(2)
    _feed(book, rng.normal(0.7, 1.0, 120))
    assert book.counts()["VALIDATED"] == 1
    assert _feed(book, rng.normal(-0.6, 1.0, 80), NOW + pd.Timedelta(days=1)) == "RETIRED"
    assert _feed(book, [0.1], NOW + pd.Timedelta(days=2)) == "RETIRED"  # still cooling down
    assert _feed(book, [0.1], NOW + pd.Timedelta(days=40)) == "EXPERIMENTAL"  # must earn it again


def test_noise_rarely_becomes_a_validated_skill(conn):
    book = SkillBook(conn, AgentSettings())
    rng = np.random.default_rng(3)
    for k in range(300):
        _feed(book, rng.normal(0.0, 1.2, 150), key=SkillKey(f"t{k}", "EURUSD", "M1", "RANGE", "london"))
    assert book.counts()["VALIDATED"] <= 2  # discovery plus fresh confirmation keeps false discoveries under 1%


def test_failed_confirmation_returns_to_experimental(conn):
    book = SkillBook(conn, AgentSettings())
    rng = np.random.default_rng(4)
    _feed(book, rng.normal(0.8, 1.0, 40))
    assert _feed(book, rng.normal(-0.8, 1.0, 30), NOW + pd.Timedelta(hours=3)) == "EXPERIMENTAL"


# ---------------- recall, learner, policy ----------------
def test_recall_summary_and_entry_filter():
    hits = [
        Hit(1, 0.9, {"reward": 1.0, "entry": "sr_bounce"}),
        Hit(2, 0.8, {"reward": -1.0, "entry": "sr_bounce"}),
        Hit(3, 0.7, {"reward": 2.0, "entry": "user_manual"}),
    ]
    s = summarize(EpisodeRecall.for_entry(hits, "sr_bounce|s1_t2_h30"))
    assert s.n == 2 and s.mean == 0.0 and s.win_rate == 0.5
    assert summarize([]).n == 0


def test_online_learner_trusts_itself_only_when_it_beats_the_base_rate():
    pytest.importorskip("river")
    rng = np.random.default_rng(0)
    good, noise = OnlineLearner(200), OnlineLearner(200)
    for _ in range(1500):
        x = float(rng.normal())
        feats = {"rsi_14": 50 + 20 * x}
        good.learn(
            learner_input(feats, "RANGE", "sr_bounce|s1_t2_h30", "LONG", 2.0), rng.random() < 1 / (1 + np.exp(-2 * x))
        )
        noise.learn(learner_input(feats, "RANGE", "sr_bounce|s1_t2_h30", "LONG", 2.0), rng.random() < 0.4)
    assert good.trusted and good.log_loss < good.base_log_loss
    assert not noise.trusted
    hi = good.predict(learner_input({"rsi_14": 90.0}, "RANGE", "sr_bounce|s1_t2_h30", "LONG", 2.0))
    assert hi > good.predict(learner_input({"rsi_14": 10.0}, "RANGE", "sr_bounce|s1_t2_h30", "LONG", 2.0))


def _cands(**over):
    base = {
        "strategy": "sr_bounce|s1_t2_h30",
        "strategy_version": "tech-1",
        "direction": "LONG",
        "eligible": True,
        "eligibility_reason": "OK",
        "stop_distance": 0.001,
        "target_distance": 0.002,
    }
    return pd.DataFrame([{**base, **over}])


def _select(conn, cands, playbook, regime="RANGE", store=None, data_ok=True, model=None):
    store = store or SqliteVectorStore(conn)
    feats = {"in_london": 1.0, "rsi_14": 30.0}
    return select_with_agent(
        cands,
        features=feats,
        state=state_embedding(feats, regime),
        regime=regime,
        session="london",
        symbol="EURUSD",
        timeframe="M1",
        at=NOW.to_pydatetime(),
        data_ok=data_ok,
        suspended=set(),
        playbook=playbook,
        recall=EpisodeRecall(store, 40, 0.6),
        learner=None,
        agent=AgentSettings(),
        memory=MemorySettings(),
        model=model,
        model_input=cands if model is not None else None,
    )


class _Champion:
    threshold = 0.55

    def __init__(self, p, confident=True):
        self.p, self.conformal = p, self
        self.confident = confident

    def predict_proba(self, X):
        return np.full(len(X), self.p)

    def confident_positive(self, p):
        return np.full(len(p), self.confident)


def test_champion_model_can_only_filter_proven_skills(conn):
    proven = {"skills": {KEY.text: {"n": 80, "mean": 0.4, "lcb": 0.2}}}
    ok = _select(conn, _cands(), proven, model=_Champion(0.7))
    assert ok.status is DecisionStatus.RECOMMEND and ok.scores[0].probability == pytest.approx(0.7)
    assert _select(conn, _cands(), proven, model=_Champion(0.5)).scores[0].reason == "CHAMPION_BELOW_THRESHOLD"
    assert _select(conn, _cands(), proven, model=_Champion(0.7, False)).scores[0].reason == "CHAMPION_UNCERTAIN"
    # a confident model never approves a setup without a proven skill
    assert _select(conn, _cands(), {"skills": {}}, model=_Champion(0.99)).scores[0].reason == "SKILL_NOT_PROVEN"


def test_agent_mode_trains_lightgbm_on_technique_candidates(learned):
    pytest.importorskip("lightgbm", reason="install the boost extra to train LightGBM")
    from mt5bot.ml.dataset import TECHNIQUE_CONTEXT, inference_frame, is_technique_model
    from mt5bot.pipeline.jobs import build_dataset, candidates_for, train_job

    s, conn, pipe, end = learned["s"], learned["conn"], learned["pipe"], learned["end"].to_pydatetime()
    ds = build_dataset(conn, s, ["EURUSD"], Timeframe.M1, None, end)
    assert len(ds) > 200 and set(TECHNIQUE_CONTEXT) <= set(ds.feature_names) and is_technique_model(ds.feature_names)
    assert not any(c.startswith("strat_") for c in ds.feature_names)
    out = train_job(conn, s, ["EURUSD"], Timeframe.M1, None, end, model_type="lightgbm")
    assert out.action in ("PROMOTE", "REJECT") and out.model_id
    model = pipe.registry.load(out.model_id)
    feats = pipe.feature_frame("EURUSD", Timeframe.M1, None, end)
    cands = candidates_for(s, feats, Timeframe.M1, techniques=True)
    t = cands["time_utc"].iloc[-1]
    X = inference_frame(feats[feats["time_utc"] == t], cands[cands["time_utc"] == t], model.feature_names)
    p = model.predict_proba(X)
    assert len(p) == len(X) and ((p >= 0) & (p <= 1)).all()
    # the full decision path runs with the model as an extra filter, and refuses a model from another mode
    ModelRepository(conn).set_status(out.model_id, "CHAMPION")
    conn.commit()
    row_idx = int(feats.index[feats["time_utc"] == t][0])
    now = t + pd.Timedelta(minutes=1)
    with_model = ResearchPipeline(conn, s.model_copy(update={"agent": AgentSettings(use_champion_model=True)}))
    rec = with_model.decide(feats, row_idx, "EURUSD", Timeframe.M1, mode="SIMULATED", decided_at=now)
    assert rec.model_id == out.model_id and f"champion {out.model_id}" in rec.rationale
    assert pipe.decide(feats, row_idx, "EURUSD", Timeframe.M1, mode="SIMULATED", decided_at=now).model_id is None
    ml = s.model_copy(update={"selection": s.selection.model_copy(update={"mode": "ml"})})
    other = ResearchPipeline(conn, ml).decide(feats, row_idx, "EURUSD", Timeframe.M1, mode="SIMULATED", decided_at=now)
    assert other.model_id is None and "different selection mode" in other.rationale
    ModelRepository(conn).set_status(out.model_id, "REJECTED")
    conn.commit()


def test_scaffold_guide_is_tracked_but_artifacts_are_ignored():
    assert (ROOT / "models" / "README.md").exists()
    ignore = (ROOT / ".gitignore").read_text()
    assert "models/*" in ignore and "!models/README.md" in ignore


def test_policy_abstains_until_a_skill_is_proven(conn):
    r = _select(conn, _cands(), {"skills": {}})
    assert (
        r.status is DecisionStatus.NO_TRADE and r.scores[0].reason == "SKILL_NOT_PROVEN" and "learning" in r.rationale
    )
    proven = {"skills": {KEY.text: {"n": 80, "mean": 0.4, "lcb": 0.2}}}
    r = _select(conn, _cands(), proven)
    assert r.status is DecisionStatus.RECOMMEND and r.scores[0].sample_size == 80
    assert (
        _select(conn, _cands(eligible=False, eligibility_reason="REGIME_NOT_ELIGIBLE"), proven).status
        is DecisionStatus.NO_TRADE
    )
    assert _select(conn, _cands(), proven, regime="UNCERTAIN").status is DecisionStatus.UNCERTAIN
    assert _select(conn, _cands(), proven, data_ok=False).status is DecisionStatus.UNCERTAIN


def test_policy_memory_veto_uses_only_past_episodes(conn):
    store = SqliteVectorStore(conn)
    feats = {"in_london": 1.0, "rsi_14": 30.0}
    vec = state_embedding(feats, "RANGE")
    meta = {"symbol": "EURUSD", "direction": "LONG", "entry": "sr_bounce", "reward": -1.0}
    store.add_many(EPISODES, [Item(i, vec, meta, (NOW - pd.Timedelta(days=1)).to_pydatetime()) for i in range(1, 16)])
    proven = {"skills": {KEY.text: {"n": 80, "mean": 0.4, "lcb": 0.2}}}
    r = _select(conn, _cands(), proven, store=store)
    assert r.status is DecisionStatus.NO_TRADE and r.scores[0].reason == "MEMORY_NEGATIVE"
    future = SqliteVectorStore(open_database(":memory:"))
    future.add_many(EPISODES, [Item(i, vec, meta, (NOW + pd.Timedelta(days=1)).to_pydatetime()) for i in range(1, 16)])
    assert _select(conn, _cands(), proven, store=future).status is DecisionStatus.RECOMMEND  # not known yet


# ---------------- learning cycle end to end ----------------
@pytest.fixture(scope="module")
def learned(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agent")
    s = Settings(
        storage={"db_path": tmp / "a.db", "model_dir": tmp / "m"},
        features={"vol_percentile_window": 100},
        risk={"block_on_stale_news": False},
        selection={"mode": "agent"},
        mt5={"server_utc_offset_hours": 0},
        instruments={"symbols": ["EURUSD"], "timeframes": ["M1"]},
        agent={"observe_user_trades": True},
    )
    conn = open_database(s.storage.db_path)
    m1 = synthetic_bars(T0, 5000, seed=21)
    BarRepository(conn).insert_bars("EURUSD", Timeframe.M1, m1, source="t", broker_symbol="EURUSD", run_id=None)
    BarRepository(conn).insert_bars(
        "EURUSD", Timeframe.M5, resample_bars(m1, Timeframe.M5), source="t", broker_symbol="EURUSD", run_id=None
    )
    pipe = ResearchPipeline(conn, s)
    end = m1["time_utc"].iloc[-1] + pd.Timedelta(minutes=1)
    counts_ = pipe.replay(
        "EURUSD", Timeframe.M1, (pd.Timestamp(T0) + pd.Timedelta(days=1)).to_pydatetime(), end.to_pydatetime()
    )
    return {"s": s, "conn": conn, "pipe": pipe, "counts": counts_, "m1": m1, "end": end}


def test_agent_learns_episodes_skills_lessons_and_playbook(learned):
    conn, agent = learned["conn"], learned["pipe"].agent
    st = agent.status()
    n = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    assert n > 500 and st["episodes"] == n == conn.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0]
    assert sum(st["skills"].values()) > 50
    assert conn.execute("SELECT COALESCE(SUM(n), 0) FROM skills").fetchone()[0] == n
    assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] > 0
    assert set(learned["counts"]) <= {"NO_TRADE", "UNCERTAIN", "RECOMMEND", "REJECTED"}
    assert st["target_in_r_per_day"] == 20.0 and st["max_daily_loss_usd"] == 20.0
    assert load_playbook(conn)["counts"] == agent.book.counts()
    assert agent.learn(pd.Timestamp(learned["end"])).episodes == 0  # idempotent
    lesson = conn.execute("SELECT text FROM lessons WHERE kind = 'summary' LIMIT 1").fetchone()["text"]
    assert "signals studied" in lesson
    hits = agent.recall_lessons("signals studied outcomes", 3)
    assert hits and "text" in hits[0].metadata


def test_agent_rebuilds_its_online_model_from_memory(learned):
    pytest.importorskip("river")
    again = TradingAgent(learned["conn"], learned["s"])
    assert again.learner is not None and again.learner.n == learned["pipe"].agent.learner.n > 0


def test_learn_cycle_same_result_with_and_without_langgraph(learned, monkeypatch):
    pytest.importorskip("langgraph")
    agent = learned["pipe"].agent
    with_graph = agent.learn(pd.Timestamp(learned["end"]))
    assert with_graph.engine == "langgraph"
    import builtins

    real = builtins.__import__

    def no_langgraph(name, *a, **k):
        if name.startswith("langgraph"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_langgraph)
    plain = agent.learn(pd.Timestamp(learned["end"]))
    assert plain.engine == "sequential" and plain.skills == with_graph.skills


def test_playbook_cache_only_changes_with_content(conn):
    book = SkillBook(conn, AgentSettings())
    assert save_playbook(conn, build_playbook(conn, book)) is True
    assert save_playbook(conn, build_playbook(conn, book)) is False
    _feed(book, np.random.default_rng(1).normal(0.6, 1.0, 100))
    pb = build_playbook(conn, book)
    assert save_playbook(conn, pb) is True and KEY.text in load_playbook(conn)["skills"]


# ---------------- user trades (opt-in, read-only) ----------------
def _deals(m1):
    t_in, t_out = m1["time_utc"].iloc[2000], m1["time_utc"].iloc[2030]
    return pd.DataFrame(
        [
            {
                "ticket": 1,
                "position_id": 77,
                "symbol": "EURUSD",
                "type": 0,
                "entry": 0,
                "volume": 0.02,
                "price": 1.1000,
                "profit": 0.0,
                "time_utc": t_in,
            },
            {
                "ticket": 2,
                "position_id": 77,
                "symbol": "EURUSD",
                "type": 1,
                "entry": 1,
                "volume": 0.02,
                "price": 1.1010,
                "profit": 2.0,
                "time_utc": t_out,
            },
            {
                "ticket": 3,
                "position_id": 78,
                "symbol": "EURUSD",
                "type": 1,
                "entry": 0,
                "volume": 0.01,
                "price": 1.1,
                "profit": 0.0,
                "time_utc": t_out,
            },
        ]
    )


def test_pair_deals_ignores_open_positions(learned):
    trades = pair_deals(_deals(learned["m1"]))
    assert len(trades) == 1 and trades.iloc[0]["direction"] == "LONG" and trades.iloc[0]["profit_usd"] == 2.0


def test_user_trades_need_opt_in_and_never_validate_skills(learned):
    conn, pipe, s = learned["conn"], learned["pipe"], learned["s"]
    gw = FakeGateway(deal_data=_deals(learned["m1"]))
    gw.connect()
    from mt5bot.storage.repositories import InstrumentRepository

    InstrumentRepository(conn).upsert(default_spec("EURUSD"), "EURUSD", "t")
    off = s.model_copy(update={"agent": s.agent.model_copy(update={"observe_user_trades": False})})
    assert import_user_trades(gw, conn, off, pipe.agent, pipe, learned["end"].to_pydatetime()).enabled is False
    skills_before = conn.execute("SELECT COALESCE(SUM(n), 0) FROM skills").fetchone()[0]
    rep = import_user_trades(gw, conn, s, pipe.agent, pipe, learned["end"].to_pydatetime())
    assert rep.trades_found == 1 and rep.trades_stored == 1 and rep.episodes_added == 1
    again = import_user_trades(gw, conn, s, pipe.agent, pipe, learned["end"].to_pydatetime())
    assert again.trades_stored == 0 and again.episodes_added == 0
    row = conn.execute("SELECT * FROM episodes WHERE source = 'USER'").fetchone()
    assert row["technique"] == "user_manual" and row["net_r"] > 0
    assert conn.execute("SELECT COALESCE(SUM(n), 0) FROM skills").fetchone()[0] == skills_before
    assert conn.execute("SELECT COUNT(*) FROM skills WHERE technique = 'user_manual'").fetchone()[0] == 0


def test_gateway_reads_deal_history_but_still_cannot_trade():
    proxy = ReadOnlyMT5(FakeMT5Module())
    assert proxy.history_deals_get(T0, T0) == ()
    for name in ("order_send", "positions_get", "orders_get", "order_check"):
        with pytest.raises(ExecutionDisabledError):
            getattr(proxy, name)


# ---------------- goals, labels, catalogue ----------------
def test_daily_goal_never_blocks_unless_asked_and_loss_limit_always_does():
    cand = RiskCandidate(
        symbol="EURUSD",
        strategy="t",
        direction=Direction.LONG,
        stop_distance=0.001,
        target_distance=0.002,
        price=1.1,
        spread_points=10,
        atr=0.0008,
        vol_pctile=0.5,
        est_cost_r=0.14,
        model_uncertainty=0.3,
        spec=default_spec("EURUSD"),
    )
    ahead = RiskContext(mode="SIMULATED", daily_pnl_usd=150.0)
    assert RiskEngine(RiskSettings()).evaluate(cand, ahead).approved
    stop = RiskEngine(RiskSettings(stop_after_daily_target=True)).evaluate(cand, ahead)
    assert "RISK_DAILY_TARGET_REACHED" in {c.code for c in stop.rejections}
    down = RiskEngine(RiskSettings()).evaluate(cand, RiskContext(mode="SIMULATED", daily_pnl_usd=-20.0))
    assert "RISK_DAILY_LOSS_LIMIT" in {c.code for c in down.rejections}
    assert RiskSettings().risk_per_trade_pct == 0.5 and RiskSettings().max_daily_loss_pct == 2.0


def test_research_labels():
    assert (
        research_label("RECOMMEND", "LONG") == "BUY_CANDIDATE"
        and research_label("RECOMMEND", "SHORT") == "SELL_CANDIDATE"
    )
    assert research_label("REJECTED", "LONG") == "RISK_REJECTED" and research_label("NO_TRADE", None) == "NO_TRADE"
    assert research_label("UNCERTAIN", None) == "UNCERTAIN" and research_label("", None) == "DATA_UNAVAILABLE"


def test_catalogue_only_claims_things_that_exist():
    rules = {r.name for r in ENTRY_RULES}
    for cat in CATALOGUE:
        for name, where in cat.implemented:
            for path in re.findall(r"[a-z_]+/[a-z_/]*[a-z_]+\.py", where):
                assert (ROOT / "src" / "mt5bot" / path).exists(), (name, path)
            for tech in re.findall(r"techniques: ([a-z_, ]+)", where):
                assert {t.strip() for t in tech.split(",")} <= rules, (name, tech)
    done, planned = counts()
    md = catalogue_markdown()
    assert f"{done} capabilities are implemented" in md and f"{planned} are planned" in md and done > 80


def test_agent_cli(tmp_path, monkeypatch, capsys):
    from mt5bot import cli

    toml = tmp_path / "c.toml"
    toml.write_text(
        f'[storage]\ndb_path = "{(tmp_path / "c.db").as_posix()}"\n[selection]\nmode = "agent"\n'
        '[logging]\nlevel = "WARNING"\n'
    )
    monkeypatch.setenv("MT5BOT_CONFIG_FILE", str(toml))
    assert cli.main(["agent", "status"]) == 0
    out = capsys.readouterr().out
    assert '"memory_backend": "sqlite"' in out and "aspiration for reporting" in out
    assert cli.main(["agent", "learn"]) == 0 and "learned 0 new outcomes" in capsys.readouterr().out
    assert cli.main(["agent", "skills"]) == 0 and cli.main(["agent", "events"]) == 0
    assert cli.main(["agent", "lessons", "--query", "london"]) == 0
    assert cli.main(["agent", "import-trades"]) == 2  # observation is opt-in
    assert "opt in" in capsys.readouterr().out
    out_file = tmp_path / "SKILLS.md"
    assert (
        cli.main(["agent", "catalogue", "--out", str(out_file)]) == 0
        and "## A. Market structure" in out_file.read_text()
    )


def test_generated_skill_doc_is_current():
    assert (ROOT / "docs" / "SKILLS.md").read_text(encoding="utf-8") == catalogue_markdown()
