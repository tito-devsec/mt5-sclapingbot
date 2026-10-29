import math

import numpy as np
import pandas as pd
import pytest

from mt5bot.config.settings import EvaluationSettings, RiskSettings, SelectionSettings
from mt5bot.core.errors import ModelIntegrityError, ModelUnavailableError
from mt5bot.core.types import DecisionStatus, Direction, default_spec
from mt5bot.evaluation.splits import train_val_test, walk_forward
from mt5bot.evaluation.stats import (
    block_bootstrap_ci,
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
    wilson_interval,
)
from mt5bot.gateway.fake import demo_account
from mt5bot.ml.dataset import MetaDataset
from mt5bot.ml.promotion import evaluate_gates
from mt5bot.ml.registry import ModelRegistry
from mt5bot.ml.training import ConformalAbstainer, fit_meta_model, train_and_evaluate
from mt5bot.risk.engine import RiskCandidate, RiskContext, RiskEngine, usd_leg_direction
from mt5bot.selection.bandit import replay
from mt5bot.selection.reward import reward
from mt5bot.selection.selector import select

EMB = pd.Timedelta(minutes=30)


def planted_dataset(n=1500, seed=0, signal=1.0) -> MetaDataset:
    """Synthetic meta-dataset with a planted relation between x0 and the label (pipeline test only)."""
    rng = np.random.default_rng(seed)
    t = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    x = rng.normal(size=(n, 4))
    logit = signal * x[:, 0]
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    net_r = np.where(y == 1, 1.5, -1.0) - 0.05
    X = pd.DataFrame(x, columns=["x0", "x1", "x2", "x3"])
    meta = pd.DataFrame(
        {
            "time_utc": t - pd.Timedelta(minutes=1),
            "available_at": t,
            "label_available_at": t + pd.Timedelta(minutes=20),
            "strategy": "trend",
            "direction": "LONG",
            "regime": "TREND_UP",
            "net_r": net_r,
            "cost_r": 0.05,
            "label": y,
            "eligible": True,
        }
    )
    return MetaDataset(X, y, meta, list(X.columns))


# ---------------- splits and statistics ----------------
def test_splits_are_chronological_and_purged():
    ds = planted_dataset(600)
    val, test = train_val_test(ds.meta, 0.2, 0.2, EMB)
    for sp in (val, test):
        tr_t = ds.meta["available_at"].iloc[sp.train_idx]
        ev_t = ds.meta["available_at"].iloc[sp.eval_idx]
        assert tr_t.max() < ev_t.min()
        # every training label was known before the evaluation window (minus embargo)
        assert (ds.meta["label_available_at"].iloc[sp.train_idx] <= sp.eval_start - EMB).all()
    assert set(test.eval_idx).isdisjoint(val.train_idx) and set(test.eval_idx).isdisjoint(val.eval_idx)
    folds = walk_forward(ds.meta, 4, EMB)
    assert len(folds) == 4
    for f in folds:
        assert (ds.meta["label_available_at"].iloc[f.train_idx] <= f.eval_start - EMB).all()


def test_statistics():
    rng = np.random.default_rng(1)
    x = rng.normal(0.2, 1, 500)
    lo, hi = block_bootstrap_ci(x)
    assert lo < x.mean() < hi
    assert probabilistic_sharpe(x) > 0.95
    assert probabilistic_sharpe(rng.normal(0, 1, 500)) < 0.99
    assert expected_max_sharpe(100, 0.01) > expected_max_sharpe(10, 0.01) > 0
    assert deflated_sharpe(x, 1000) <= deflated_sharpe(x, 1)
    lo, hi = wilson_interval(5, 10)
    assert lo < 0.5 < hi


# ---------------- ML ----------------
def test_training_reproducible_and_learns_planted_signal():
    ds = planted_dataset(1500)
    kw = dict(
        model_type="logreg",
        seed=7,
        threshold=0.55,
        confidence=0.6,
        val_fraction=0.2,
        test_fraction=0.2,
        n_folds=3,
        embargo=EMB,
        cost_multiplier=1.5,
    )
    a = train_and_evaluate(ds, **kw)
    b = train_and_evaluate(ds, **kw)
    assert a.summary()["test"]["selected"] == b.summary()["test"]["selected"]
    assert a.test["classification"]["auc"] > 0.6
    assert len(a.walk_forward) == 3


@pytest.mark.parametrize("model_type", ["random_forest", "lightgbm", "xgboost", "catboost"])
def test_other_model_types_fit(model_type):
    pytest.importorskip(
        {"random_forest": "sklearn", "lightgbm": "lightgbm", "xgboost": "xgboost", "catboost": "catboost"}[model_type]
    )
    ds = planted_dataset(800)
    m = fit_meta_model(ds, model_type=model_type, seed=3, threshold=0.55, confidence=0.6, embargo=EMB)
    p = m.predict_proba(ds.X)
    assert p.shape == (800,) and ((p >= 0) & (p <= 1)).all()


def test_conformal_matches_mapie():
    mapie = pytest.importorskip("mapie")
    from mapie.classification import SplitConformalClassifier
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(0)
    X = rng.normal(size=(900, 3))
    y = (X[:, 0] + rng.normal(size=900) > 0).astype(int)
    est = LogisticRegression().fit(X[:300], y[:300])
    ref = SplitConformalClassifier(estimator=est, confidence_level=0.8, prefit=True).conformalize(
        X[300:600], y[300:600]
    )
    _, sets = ref.predict_set(X[600:])
    ours = ConformalAbstainer(0.8).fit(est.predict_proba(X[300:600])[:, 1], y[300:600])
    np.testing.assert_array_equal(ours.sets(est.predict_proba(X[600:])[:, 1]), sets[:, :, 0])
    assert mapie.__version__


def test_registry_roundtrip_integrity_and_rollback(conn, tmp_path):
    ds = planted_dataset(800)
    reg = ModelRegistry(conn, tmp_path / "models")
    m = fit_meta_model(ds, model_type="logreg", seed=1, threshold=0.55, confidence=0.6, embargo=EMB)
    t0, t1 = ds.meta["available_at"].iloc[0], ds.meta["available_at"].iloc[-1]
    id1 = reg.register(
        m,
        kind="meta_label",
        feature_set="fs",
        train_start=t0,
        train_end=t1,
        dataset_fingerprint=ds.fingerprint(),
        metrics={},
    )
    loaded = reg.load(id1)
    np.testing.assert_allclose(loaded.predict_proba(ds.X), m.predict_proba(ds.X))
    assert loaded.conformal.q_ == m.conformal.q_

    from mt5bot.ml.registry import GateResult

    assert reg.decide(id1, "meta_label", GateResult(True, {})) == "PROMOTE"
    id2 = reg.register(
        m,
        kind="meta_label",
        feature_set="fs",
        train_start=t0,
        train_end=t1,
        dataset_fingerprint=ds.fingerprint(),
        metrics={},
    )
    # a rejected candidate cannot replace the champion
    assert reg.decide(id2, "meta_label", GateResult(False, {"x": {"passed": False}})) == "REJECT"
    assert reg.champion("meta_label")[0] == id1
    id3 = reg.register(
        m,
        kind="meta_label",
        feature_set="fs",
        train_start=t0,
        train_end=t1,
        dataset_fingerprint=ds.fingerprint(),
        metrics={},
    )
    reg.decide(id3, "meta_label", GateResult(True, {}))
    assert reg.champion("meta_label")[0] == id3
    assert reg.rollback("meta_label") == id1
    assert reg.champion("meta_label")[0] == id1

    # tampering is detected; a missing artifact is reported
    path = reg.repo.get(id1)["artifact_path"]
    with open(path, "ab") as fh:
        fh.write(b"tamper")
    with pytest.raises(ModelIntegrityError):
        reg.load(id1)
    import os

    os.remove(reg.repo.get(id3)["artifact_path"])
    with pytest.raises(ModelUnavailableError):
        reg.load(id3)


def test_promotion_gates_reject_weak_candidate():
    ds = planted_dataset(1500, signal=0.0)  # no signal at all
    rep = train_and_evaluate(
        ds,
        model_type="logreg",
        seed=7,
        threshold=0.55,
        confidence=0.6,
        val_fraction=0.2,
        test_fraction=0.2,
        n_folds=3,
        embargo=EMB,
        cost_multiplier=1.5,
    )
    gates = evaluate_gates(rep.summary(), None, EvaluationSettings())
    assert not gates.passed
    assert gates.reason().startswith("failed gates:")


# ---------------- selection ----------------
def cands(**over):
    base = {
        "strategy": "trend",
        "strategy_version": "trend-1",
        "direction": "LONG",
        "eligible": True,
        "eligibility_reason": "OK",
    }
    return pd.DataFrame([{**base, **over}])


CFG = SelectionSettings(min_samples=30)


def test_selector_no_trade_without_evidence_or_model():
    r = select(cands(), regime="TREND_UP", data_ok=True, suspended=set(), stats_for=lambda row: None, cfg=CFG)
    assert r.status is DecisionStatus.NO_TRADE and r.scores[0].reason == "INSUFFICIENT_EVIDENCE"


def test_selector_uses_lcb_and_min_samples():
    good = lambda row: (100, 0.4, 0.25)  # noqa: E731
    r = select(cands(), regime="TREND_UP", data_ok=True, suspended=set(), stats_for=good, cfg=CFG)
    assert r.status is DecisionStatus.RECOMMEND and r.chosen == 0
    tiny = lambda row: (5, 2.0, 0.1)  # noqa: E731  # great mean but too few samples
    r = select(cands(), regime="TREND_UP", data_ok=True, suspended=set(), stats_for=tiny, cfg=CFG)
    assert r.status is DecisionStatus.NO_TRADE
    bad = lambda row: (200, 0.05, 4.0)  # noqa: E731  # LCB below zero
    r = select(cands(), regime="TREND_UP", data_ok=True, suspended=set(), stats_for=bad, cfg=CFG)
    assert r.scores[0].reason == "NEGATIVE_LCB"


def test_selector_ranks_and_respects_suspension_and_regime():
    two = pd.concat([cands(), cands(strategy="breakout", strategy_version="b-1")], ignore_index=True)
    stats = {"trend": (100, 0.2, 0.25), "breakout": (100, 0.6, 0.25)}
    r = select(
        two, regime="TREND_UP", data_ok=True, suspended=set(), stats_for=lambda row: stats[row.strategy], cfg=CFG
    )
    assert r.scores[r.chosen].strategy == "breakout"
    r = select(
        two, regime="TREND_UP", data_ok=True, suspended={"breakout"}, stats_for=lambda row: stats[row.strategy], cfg=CFG
    )
    assert r.scores[r.chosen].strategy == "trend"
    assert (
        select(two, regime="UNCERTAIN", data_ok=True, suspended=set(), stats_for=lambda r: None, cfg=CFG).status
        is DecisionStatus.UNCERTAIN
    )
    assert (
        select(two, regime="TREND_UP", data_ok=False, suspended=set(), stats_for=lambda r: None, cfg=CFG).status
        is DecisionStatus.UNCERTAIN
    )


def test_reward_formula():
    cfg = SelectionSettings()
    assert reward(5.0, 0.2, cfg) == 3.0
    assert reward(-1.0, 1.5, cfg) == pytest.approx(-1.0 - 0.25 * 0.5)
    with pytest.raises(ValueError):
        reward(math.nan, 0, cfg)


def test_bandit_replay_uses_delayed_rewards_only():
    t = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "time_utc": t,
            "available_at": t,
            "label_available_at": t + pd.Timedelta(hours=1),
            "strategy": np.where(np.arange(200) % 2 == 0, "trend", "breakout"),
            "reward": np.where(np.arange(200) % 2 == 0, 0.5, -0.5),
            "x": np.ones(200),
        }
    )
    out = replay(df, ["x"], alpha=0.5)
    assert out["taken"] > 0 and out["decisions"] == 200


# ---------------- risk ----------------
def risk_candidate(**over):
    base = dict(
        symbol="EURUSD",
        strategy="trend",
        direction=Direction.LONG,
        stop_distance=0.0010,
        target_distance=0.0020,
        price=1.1,
        spread_points=10,
        atr=0.0008,
        vol_pctile=0.5,
        est_cost_r=0.14,
        model_uncertainty=0.3,
        spec=default_spec("EURUSD"),
    )
    return RiskCandidate(**{**base, **over})


def test_risk_approves_reasonable_candidate():
    v = RiskEngine(RiskSettings()).evaluate(risk_candidate(), RiskContext(mode="SIMULATED"))
    assert v.approved, [c for c in v.rejections]


@pytest.mark.parametrize(
    "cand,ctx,code",
    [
        ({"spread_points": 80}, {}, "RISK_SPREAD_TOO_WIDE"),
        ({"atr": 0.0002}, {}, "RISK_SPREAD_ATR_TOO_HIGH"),
        ({"vol_pctile": 0.995}, {}, "RISK_VOLATILITY_OUT_OF_BOUNDS"),
        ({"target_distance": 0.0009}, {}, "RISK_REWARD_RISK_TOO_LOW"),
        ({"stop_distance": 0.5, "target_distance": 1.0, "atr": 0.3}, {}, "RISK_SIZE_BELOW_MINIMUM"),
        ({"model_uncertainty": 0.9}, {}, "RISK_MODEL_UNCERTAINTY"),
        ({"price": float("nan")}, {}, "RISK_DATA_INCONSISTENT"),
        ({}, {"data_age_seconds": 999}, "RISK_DATA_STALE"),
        ({}, {"upcoming_high_impact_events": ["USD CPI 12:30"]}, "RISK_EVENT_WINDOW"),
        ({}, {"news_stale": True}, "RISK_NEWS_STATE_UNKNOWN"),
        ({}, {"daily_pnl_usd": -25}, "RISK_DAILY_LOSS_LIMIT"),
        ({}, {"equity_usd": 890, "peak_equity_usd": 1000}, "RISK_MAX_DRAWDOWN"),
        ({}, {"consecutive_losses": 5}, "RISK_CONSECUTIVE_LOSSES"),
        (
            {},
            {"open_positions": [("GBPUSD", "LONG"), ("XAUUSD", "LONG"), ("USDJPY", "SHORT")]},
            "RISK_MAX_OPEN_POSITIONS",
        ),
        ({}, {"open_positions": [("GBPUSD", "LONG"), ("USDJPY", "SHORT")]}, "RISK_CORRELATED_EXPOSURE"),
        ({}, {"suspended_strategies": {"trend"}}, "RISK_STRATEGY_SUSPENDED"),
        ({}, {"mode": "OBSERVED_DEMO", "account": None}, "RISK_ACCOUNT_UNAVAILABLE"),
        ({}, {"mode": "OBSERVED_DEMO", "account": demo_account(is_demo=False)}, "RISK_ACCOUNT_NOT_DEMO"),
        ({}, {"mode": "OBSERVED_DEMO", "account": demo_account(currency="EUR")}, "RISK_ACCOUNT_CURRENCY"),
    ],
)
def test_risk_rejections(cand, ctx, code):
    v = RiskEngine(RiskSettings()).evaluate(risk_candidate(**cand), RiskContext(**{"mode": "SIMULATED", **ctx}))
    assert not v.approved
    assert code in {c.code for c in v.rejections}


def test_emergency_disable_and_fail_closed():
    v = RiskEngine(RiskSettings(emergency_disable=True)).evaluate(risk_candidate(), RiskContext(mode="SIMULATED"))
    assert "RISK_EMERGENCY_DISABLED" in {c.code for c in v.rejections}
    bad = risk_candidate(spec=None)  # broken input -> a check raises -> rejected, not approved
    v = RiskEngine(RiskSettings()).evaluate(bad, RiskContext(mode="SIMULATED"))
    assert not v.approved and "RISK_CHECK_INCOMPLETE" in {c.code for c in v.rejections}


def test_risk_overrides_favourable_selection():
    r = select(
        cands(), regime="TREND_UP", data_ok=True, suspended=set(), stats_for=lambda row: (500, 1.0, 0.1), cfg=CFG
    )
    assert r.status is DecisionStatus.RECOMMEND
    v = RiskEngine(RiskSettings()).evaluate(risk_candidate(spread_points=80), RiskContext(mode="SIMULATED"))
    assert not v.approved  # the pipeline must turn this into REJECTED regardless of the selector


def test_usd_leg_direction():
    assert usd_leg_direction("EURUSD", Direction.LONG) == -1
    assert usd_leg_direction("USDJPY", Direction.LONG) == 1
    assert usd_leg_direction("XAUUSD", Direction.SHORT) == 1


def test_ljung_box_detects_autocorrelation():
    from mt5bot.evaluation.stats import ljung_box_pvalue

    rng = np.random.default_rng(5)
    assert ljung_box_pvalue(rng.normal(size=400)) > 0.01
    ar = np.zeros(400)
    for i in range(1, 400):
        ar[i] = 0.8 * ar[i - 1] + rng.normal()
    assert ljung_box_pvalue(ar) < 0.001


def test_risk_engine_property_never_approves_limit_breaches():
    """Property test (hypothesis): any approved candidate respects every configured limit."""
    from hypothesis import given, settings
    from hypothesis import strategies as st

    cfg = RiskSettings()
    engine = RiskEngine(cfg)
    spec = default_spec("EURUSD")

    @settings(max_examples=300, deadline=None, derandomize=True)
    @given(
        stop=st.floats(0.00005, 0.01),
        rr=st.floats(0.2, 5.0),
        spread=st.floats(0, 120),
        atr=st.floats(0.00002, 0.01),
        vol=st.floats(0, 1),
        cost=st.floats(0, 3),
        unc=st.floats(0, 1),
        daily=st.floats(-60, 20),
        losses=st.integers(0, 10),
        n_open=st.integers(0, 6),
        long_=st.booleans(),
    )
    def check(stop, rr, spread, atr, vol, cost, unc, daily, losses, n_open, long_):
        c = RiskCandidate(
            symbol="EURUSD",
            strategy="trend",
            direction=Direction.LONG if long_ else Direction.SHORT,
            stop_distance=stop,
            target_distance=stop * rr,
            price=1.1,
            spread_points=spread,
            atr=atr,
            vol_pctile=vol,
            est_cost_r=cost,
            model_uncertainty=unc,
            spec=spec,
        )
        ctx = RiskContext(
            mode="SIMULATED",
            daily_pnl_usd=daily,
            consecutive_losses=losses,
            open_positions=[("GBPUSD", "SHORT")] * n_open,
        )
        v = engine.evaluate(c, ctx)
        if v.approved:
            assert spread <= cfg.max_spread_points["*"]
            assert spread * spec.point / atr <= cfg.max_spread_atr_ratio
            assert cfg.vol_percentile_min <= vol <= cfg.vol_percentile_max
            assert (rr - cost) / (1 + cost) >= cfg.min_reward_risk
            assert unc <= cfg.max_model_uncertainty
            assert -daily < cfg.reference_balance_usd * cfg.max_daily_loss_pct / 100
            assert losses < cfg.max_consecutive_losses and n_open < cfg.max_open_positions
            _lots, risk = engine.position_risk_usd(c)
            assert (
                0
                < risk
                <= min(cfg.max_risk_per_trade_usd, cfg.reference_balance_usd * cfg.risk_per_trade_pct / 100) + 1e-9
            )

    check()
