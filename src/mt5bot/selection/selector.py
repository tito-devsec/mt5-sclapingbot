"""Reliability-weighted selector using champion probabilities and LCB evidence; never overrides risk."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from mt5bot.config.settings import SelectionSettings
from mt5bot.core.types import DecisionStatus, Regime
from mt5bot.ml.training import FittedMetaModel


@dataclass
class CandidateScore:
    index: int
    strategy: str
    strategy_version: str
    direction: str
    eligible: bool
    reason: str
    probability: float | None = None
    lcb_reward: float | None = None
    sample_size: int = 0
    score: float | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "direction": self.direction,
            "eligible": self.eligible,
            "eligibility_reason": self.reason,
            "probability": self.probability,
            "lcb_reward": self.lcb_reward,
            "score": self.score,
            "sample_size": self.sample_size,
        }


@dataclass
class SelectionResult:
    status: DecisionStatus
    chosen: int | None
    scores: list[CandidateScore] = field(default_factory=list)
    rationale: str = ""


def lcb(n: int, mean: float, var: float, z: float) -> float:
    if n <= 1 or not math.isfinite(var):
        return -math.inf
    return mean - z * math.sqrt(var / n)


def select(
    candidates: pd.DataFrame,
    *,
    regime: str,
    data_ok: bool,
    suspended: set[str],
    stats_for: Any,
    cfg: SelectionSettings,
    model: FittedMetaModel | None = None,
    model_input: pd.DataFrame | None = None,
) -> SelectionResult:
    """``stats_for(row) -> (n, mean, var) | None`` reads performance memory for the row's bucket."""
    if not data_ok:
        return SelectionResult(DecisionStatus.UNCERTAIN, None, [], "data quality or freshness check failed")
    if regime == Regime.UNCERTAIN.value:
        return SelectionResult(DecisionStatus.UNCERTAIN, None, [], "market regime is UNCERTAIN")
    if candidates.empty:
        return SelectionResult(DecisionStatus.NO_TRADE, None, [], "no strategy produced a candidate")

    probs: list[float | None] = [None] * len(candidates)
    confident: list[bool] = [False] * len(candidates)
    if model is not None and model_input is not None:
        p = model.predict_proba(model_input)
        conf = model.conformal.confident_positive(p)
        probs, confident = [float(x) for x in p], [bool(x) for x in conf]

    scores: list[CandidateScore] = []
    any_model_uncertain = False
    for i, row in enumerate(candidates.itertuples(index=False)):
        sc = CandidateScore(i, row.strategy, row.strategy_version, row.direction, False, "OK", probs[i])
        st = stats_for(row)
        if st is not None:
            sc.sample_size = int(st[0])
            sc.lcb_reward = lcb(st[0], st[1], st[2], cfg.lcb_z)
        enough = sc.sample_size >= cfg.min_samples
        if not bool(row.eligible):
            sc.reason = str(row.eligibility_reason)
        elif row.strategy in suspended:
            sc.reason = "STRATEGY_SUSPENDED"
        elif (
            enough
            and sc.lcb_reward is not None
            and sc.lcb_reward <= 0
            and (cfg.mode != "rules" or cfg.rules_memory_veto)
        ):
            sc.reason = "NEGATIVE_LCB"
        elif cfg.mode == "rules":
            # Rules mode: the strategy rule decides; prefer the better reward/risk, then the earlier strategy.
            rr = float(getattr(row, "target_distance", 1.0)) / max(float(getattr(row, "stop_distance", 1.0)), 1e-12)
            sc.eligible, sc.score = True, rr
        elif model is None:
            if not enough:
                sc.reason = "INSUFFICIENT_EVIDENCE"
            else:
                sc.eligible, sc.score = True, float(sc.lcb_reward or 0.0)
        else:
            assert sc.probability is not None
            if sc.probability < model.threshold:
                sc.reason = "BELOW_PROBABILITY_THRESHOLD"
            elif not confident[i]:
                sc.reason = "MODEL_UNCERTAIN"
                any_model_uncertain = True
            else:
                bonus = max(sc.lcb_reward, 0.0) if (enough and sc.lcb_reward is not None) else 0.0
                sc.eligible, sc.score = True, sc.probability * (1.0 + bonus)
        scores.append(sc)

    qualified = [s for s in scores if s.eligible]
    if not qualified:
        if any_model_uncertain:
            return SelectionResult(DecisionStatus.UNCERTAIN, None, scores, "model prediction set is not confident")
        reasons = sorted({s.reason for s in scores})
        return SelectionResult(DecisionStatus.NO_TRADE, None, scores, "no candidate qualified: " + ", ".join(reasons))
    best = max(qualified, key=lambda s: (s.score or 0.0, -s.index))
    basis = "rules" if cfg.mode == "rules" else ("model" if model is not None else "evidence")
    return SelectionResult(
        DecisionStatus.RECOMMEND,
        best.index,
        scores,
        f"selected {best.strategy} {best.direction} by {basis} (score {best.score:.3f}, n={best.sample_size})",
    )
