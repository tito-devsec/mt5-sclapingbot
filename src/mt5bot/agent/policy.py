"""Agent selection policy: only proven skills are recommended, checked against similar past episodes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from mt5bot.agent.recall import EpisodeRecall, OnlineLearner, RecallSummary, learner_input, summarize
from mt5bot.agent.skills import SkillKey
from mt5bot.config.settings import AgentSettings, MemorySettings
from mt5bot.core.types import DecisionStatus, Regime
from mt5bot.selection.selector import CandidateScore, SelectionResult


def select_with_agent(
    candidates: pd.DataFrame,
    *,
    features: dict[str, Any],
    state: np.ndarray,
    regime: str,
    session: str,
    symbol: str,
    timeframe: str,
    at: datetime,
    data_ok: bool,
    suspended: set[str],
    playbook: dict[str, Any],
    recall: EpisodeRecall,
    learner: OnlineLearner | None,
    agent: AgentSettings,
    memory: MemorySettings,
) -> SelectionResult:
    if not data_ok:
        return SelectionResult(DecisionStatus.UNCERTAIN, None, [], "data quality or freshness check failed")
    if regime == Regime.UNCERTAIN.value:
        return SelectionResult(DecisionStatus.UNCERTAIN, None, [], "market regime is UNCERTAIN")
    if candidates.empty:
        return SelectionResult(DecisionStatus.NO_TRADE, None, [], "no technique produced a candidate")
    skills: dict[str, Any] = playbook.get("skills", {})
    neighbors = {d: recall.neighbors(state, symbol, d, at) for d in set(candidates["direction"])}
    use_model = learner is not None and learner.trusted
    scores: list[CandidateScore] = []
    for i, row in enumerate(candidates.itertuples(index=False)):
        sc = CandidateScore(i, row.strategy, row.strategy_version, row.direction, False, "OK")
        skill = skills.get(SkillKey(row.strategy, symbol, timeframe, regime, session).text)
        mem: RecallSummary = summarize(recall.for_entry(neighbors[row.direction], row.strategy))
        rr = float(row.target_distance) / max(float(row.stop_distance), 1e-12)
        if use_model and learner is not None:
            sc.probability = learner.predict(learner_input(features, regime, row.strategy, row.direction, rr))
        if not bool(row.eligible):
            sc.reason = str(row.eligibility_reason)
        elif row.strategy in suspended:
            sc.reason = "STRATEGY_SUSPENDED"
        elif skill is None:
            sc.reason = "SKILL_NOT_PROVEN"
        elif mem.n >= memory.min_neighbors and mem.lcb < 0 and mem.mean < 0:
            sc.reason = "MEMORY_NEGATIVE"
        elif sc.probability is not None and sc.probability * rr - (1 - sc.probability) < agent.online_min_expected_r:
            sc.reason = "ONLINE_MODEL_NEGATIVE"
        else:
            sc.eligible = True
            sc.lcb_reward, sc.sample_size = float(skill["lcb"]), int(skill["n"])
            bonus = 0.5 * max(mem.weighted_mean, 0.0) if mem.n >= memory.min_neighbors else 0.0
            edge = 0.25 * (sc.probability * rr - (1 - sc.probability)) if sc.probability is not None else 0.0
            sc.score = sc.lcb_reward + bonus + edge
        scores.append(sc)
    qualified = [s for s in scores if s.eligible]
    if not qualified:
        reasons = sorted({s.reason for s in scores})
        learning = "SKILL_NOT_PROVEN" in reasons
        note = "still learning these setups (no proven skill yet)" if learning else "no proven skill qualified"
        return SelectionResult(DecisionStatus.NO_TRADE, None, scores, f"{note}: {', '.join(reasons)}")
    best = max(qualified, key=lambda s: (s.score or 0.0, -s.index))
    mem_all = summarize(recall.for_entry(neighbors[best.direction], best.strategy))
    return SelectionResult(
        DecisionStatus.RECOMMEND,
        best.index,
        scores,
        f"selected proven skill {best.strategy} {best.direction} (score {best.score:.3f}, skill n={best.sample_size}, "
        f"{mem_all.n} similar past episodes averaging {mem_all.mean:+.2f}R)",
    )
