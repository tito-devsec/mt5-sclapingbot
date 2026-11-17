"""The learning agent: turns evaluated outcomes into episodes, skills, lessons and a cached playbook."""

from __future__ import annotations

import itertools
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict, cast

import numpy as np
import pandas as pd

from mt5bot.agent.knowledge import build_playbook, load_playbook, save_playbook, write_lessons
from mt5bot.agent.policy import select_with_agent
from mt5bot.agent.recall import EPISODES, LESSONS, EpisodeRecall, OnlineLearner, learner_input
from mt5bot.agent.skills import SkillBook, SkillKey
from mt5bot.config.settings import Settings
from mt5bot.core.types import Timeframe
from mt5bot.memory.embed import STATE_DIM, HashingEmbedder, state_embedding
from mt5bot.memory.vector import Hit, Item, VectorStore, open_vector_store
from mt5bot.ml.training import FittedMetaModel
from mt5bot.selection.context import session_name
from mt5bot.selection.selector import SelectionResult
from mt5bot.storage.db import transaction
from mt5bot.strategies.techniques import entry_of


class CycleState(TypedDict):
    now: pd.Timestamp
    report: Any


@dataclass
class LearnReport:
    episodes: int = 0
    lessons: int = 0
    playbook_updated: bool = False
    skills: dict[str, int] = field(default_factory=dict)
    engine: str = "sequential"


class TradingAgent:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, store: VectorStore | None = None) -> None:
        self.conn = conn
        self.s = settings
        dsn = settings.memory.dsn.get_secret_value() if settings.memory.dsn else None
        dims = {EPISODES: STATE_DIM, LESSONS: settings.memory.text_dim}
        self.store = store or open_vector_store(conn, settings.memory.backend, dsn, dims)
        self.book = SkillBook(conn, settings.agent)
        self.embedder = HashingEmbedder(settings.memory.text_dim)
        self.recall = EpisodeRecall(self.store, settings.memory.top_k, settings.memory.min_similarity)
        self.playbook = load_playbook(conn)
        self.learner = self._make_learner()

    def _make_learner(self) -> OnlineLearner | None:
        if not self.s.agent.online_learner:
            return None
        try:
            learner = OnlineLearner(self.s.agent.online_min_samples, self.s.agent.online_min_gain)
        except ImportError:
            return None
        rows = self.conn.execute(
            "SELECT technique, direction, regime, reward, reward_risk, features_json FROM episodes "
            "WHERE source = 'AGENT' ORDER BY available_at, id"
        ).fetchall()
        for r in rows:  # replaying stored episodes rebuilds the model without loading any pickled object
            x = learner_input(
                json.loads(r["features_json"]), r["regime"], r["technique"], r["direction"], r["reward_risk"]
            )
            learner.learn(x, r["reward"] > 0)
        return learner

    # -- learning -----------------------------------------------------------
    def ingest(self, limit: int = 20000) -> int:
        """Turn newly evaluated candidate outcomes into episodes, memory vectors, skill updates and model updates."""
        rows = self.conn.execute(
            "SELECT c.id AS cid, c.strategy, c.direction, d.symbol, d.timeframe, d.regime, d.features_json, "
            "d.bar_time_utc, co.exit_time_utc, co.net_r, co.reward, c.stop_distance, c.target_distance "
            "FROM candidate_outcomes co "
            "JOIN strategy_candidates c ON c.id = co.candidate_id JOIN decisions d ON d.id = c.decision_id "
            "LEFT JOIN episodes e ON e.candidate_id = c.id WHERE e.id IS NULL AND co.net_r IS NOT NULL "
            "AND co.exit_time_utc IS NOT NULL ORDER BY co.exit_time_utc, c.id LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return 0
        items: list[Item] = []
        with transaction(self.conn):
            for r in rows:
                feats = json.loads(r["features_json"] or "{}")
                session = session_name(feats)
                available = pd.Timestamp(r["exit_time_utc"]) + Timeframe(r["timeframe"]).delta
                rr = float(r["target_distance"] or 1.0) / max(float(r["stop_distance"] or 1.0), 1e-12)
                eid = self._add_episode(
                    candidate_id=int(r["cid"]),
                    user_ticket=None,
                    source="AGENT",
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    technique=r["strategy"],
                    direction=r["direction"],
                    regime=r["regime"],
                    session=session,
                    decided_at=r["bar_time_utc"],
                    available=available,
                    net_r=float(r["net_r"]),
                    reward=float(r["reward"]),
                    reward_risk=rr,
                    feats=feats,
                )
                items.append(
                    self._item(
                        eid,
                        feats,
                        r["regime"],
                        r["symbol"],
                        r["timeframe"],
                        r["strategy"],
                        r["direction"],
                        float(r["reward"]),
                        "AGENT",
                        available,
                    )
                )
                self.book.observe(
                    SkillKey(r["strategy"], r["symbol"], r["timeframe"], r["regime"], session),
                    float(r["reward"]),
                    available,
                )
                if self.learner is not None:
                    self.learner.learn(
                        learner_input(feats, r["regime"], r["strategy"], r["direction"], rr), float(r["reward"]) > 0
                    )
            self.store.add_many(EPISODES, items)
        return len(rows)

    def _add_episode(
        self,
        *,
        candidate_id: int | None,
        user_ticket: int | None,
        source: str,
        symbol: str,
        timeframe: str,
        technique: str,
        direction: str,
        regime: str,
        session: str,
        decided_at: str,
        available: pd.Timestamp,
        net_r: float,
        reward: float,
        reward_risk: float,
        feats: dict[str, Any],
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO episodes(candidate_id, user_ticket, source, symbol, timeframe, technique, direction, regime, "
            "session, decided_at, available_at, net_r, reward, reward_risk, features_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id,
                user_ticket,
                source,
                symbol,
                timeframe,
                technique,
                direction,
                regime,
                session,
                decided_at,
                available.isoformat(),
                net_r,
                reward,
                reward_risk,
                json.dumps(feats, default=float),
            ),
        )
        return int(cur.lastrowid or 0)

    @staticmethod
    def _item(
        eid: int,
        feats: dict[str, Any],
        regime: str,
        symbol: str,
        timeframe: str,
        technique: str,
        direction: str,
        reward: float,
        source: str,
        available: pd.Timestamp,
    ) -> Item:
        meta = {
            "symbol": symbol,
            "timeframe": timeframe,
            "technique": technique,
            "entry": entry_of(technique),
            "direction": direction,
            "regime": regime,
            "reward": round(reward, 6),
            "source": source,
        }
        return Item(eid, state_embedding(feats, regime), meta, available.to_pydatetime())

    def add_user_episode(
        self,
        *,
        ticket: int,
        symbol: str,
        timeframe: str,
        direction: str,
        regime: str,
        feats: dict[str, Any],
        decided_at: pd.Timestamp,
        available: pd.Timestamp,
        move_atr: float,
        reward: float,
    ) -> bool:
        """Store one of the user's own closed trades as an episode; returns False if it is already known."""
        if self.conn.execute("SELECT 1 FROM episodes WHERE user_ticket = ?", (ticket,)).fetchone():
            return False
        eid = self._add_episode(
            candidate_id=None,
            user_ticket=ticket,
            source="USER",
            symbol=symbol,
            timeframe=timeframe,
            technique="user_manual",
            direction=direction,
            regime=regime,
            session=session_name(feats),
            decided_at=decided_at.isoformat(),
            available=available,
            net_r=move_atr,
            reward=reward,
            reward_risk=1.0,
            feats=feats,
        )
        self.store.add_many(
            EPISODES,
            [self._item(eid, feats, regime, symbol, timeframe, "user_manual", direction, reward, "USER", available)],
        )
        return True

    def learn(self, now: pd.Timestamp) -> LearnReport:
        """One learning cycle: ingest outcomes, write lessons, refresh the playbook; uses LangGraph when installed."""
        steps = [("ingest", self._step_ingest), ("lessons", self._step_lessons), ("playbook", self._step_playbook)]
        state: CycleState = {"now": now, "report": LearnReport()}
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            for _, fn in steps:
                state = fn(state)
        else:
            graph = StateGraph(CycleState)
            for name, fn in steps:
                graph.add_node(name, fn)
            graph.add_edge(START, steps[0][0])
            for (a, _), (b, _) in itertools.pairwise(steps):
                graph.add_edge(a, b)
            graph.add_edge(steps[-1][0], END)
            state = cast(CycleState, graph.compile().invoke(state))
            state["report"].engine = "langgraph"
        report: LearnReport = state["report"]
        return report

    def _step_ingest(self, state: CycleState) -> CycleState:
        state["report"].episodes = self.ingest()
        return state

    def _step_lessons(self, state: CycleState) -> CycleState:
        state["report"].lessons = write_lessons(self.conn, self.store, self.embedder, state["now"])
        return state

    def _step_playbook(self, state: CycleState) -> CycleState:
        playbook = build_playbook(self.conn, self.book)
        state["report"].playbook_updated = save_playbook(self.conn, playbook)
        self.playbook = playbook
        state["report"].skills = playbook["counts"]
        return state

    # -- acting ---------------------------------------------------------------
    def select(
        self,
        candidates: pd.DataFrame,
        *,
        features: dict[str, Any],
        regime: str,
        symbol: str,
        timeframe: str,
        at: datetime,
        data_ok: bool,
        suspended: set[str],
        model: FittedMetaModel | None = None,
        model_input: pd.DataFrame | None = None,
    ) -> SelectionResult:
        return select_with_agent(
            candidates,
            features=features,
            state=state_embedding(features, regime),
            regime=regime,
            session=session_name(features),
            symbol=symbol,
            timeframe=timeframe,
            at=at,
            data_ok=data_ok,
            suspended=suspended,
            playbook=self.playbook,
            recall=self.recall,
            learner=self.learner,
            agent=self.s.agent,
            memory=self.s.memory,
            model=model,
            model_input=model_input,
        )

    # -- inspection -------------------------------------------------------------
    def recall_lessons(self, text: str, k: int = 5) -> list[Hit]:
        vec = self.embedder.embed([text])[0]
        return self.store.search(LESSONS, vec, k, datetime.fromisoformat("9999-01-01T00:00:00+00:00"))

    def recall_state(
        self, features: dict[str, Any], regime: str, symbol: str, direction: str, at: datetime
    ) -> list[Hit]:
        return self.recall.neighbors(state_embedding(features, regime), symbol, direction, at)

    def status(self) -> dict[str, Any]:
        r = self.s.risk
        budget = min(r.reference_balance_usd * r.risk_per_trade_pct / 100, r.max_risk_per_trade_usd)
        learner = self.learner
        return {
            "memory_backend": self.s.memory.backend,
            "episodes": self.store.count(EPISODES),
            "lessons": self.store.count(LESSONS),
            "skills": self.book.counts(),
            "proven_skills_in_playbook": len(self.playbook.get("skills", {})),
            "online_learner": None
            if learner is None
            else {
                "outcomes_seen": learner.n,
                "trusted": learner.trusted,
                "log_loss": None if np.isnan(learner.log_loss) else round(learner.log_loss, 4),
                "base_rate_log_loss": None if np.isnan(learner.base_log_loss) else round(learner.base_log_loss, 4),
            },
            "daily_target_usd": r.daily_profit_target_usd,
            "risk_per_trade_usd": budget,
            "target_in_r_per_day": round(r.daily_profit_target_usd / budget, 1) if budget else None,
            "max_daily_loss_usd": r.reference_balance_usd * r.max_daily_loss_pct / 100,
        }
