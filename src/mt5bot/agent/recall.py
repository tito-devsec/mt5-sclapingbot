"""Retrieval over episode memory (RAG) and an online learner updated after every outcome."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from mt5bot.memory.embed import STATE_SPEC
from mt5bot.memory.vector import Hit, VectorStore
from mt5bot.strategies.techniques import entry_of

EPISODES = "episodes"
LESSONS = "lessons"


@dataclass(frozen=True)
class RecallSummary:
    n: int
    mean: float
    weighted_mean: float
    win_rate: float
    lcb: float

    @staticmethod
    def empty() -> RecallSummary:
        return RecallSummary(0, 0.0, 0.0, 0.0, -math.inf)


def summarize(hits: list[Hit], z: float = 1.0) -> RecallSummary:
    if not hits:
        return RecallSummary.empty()
    r = np.asarray([float(h.metadata["reward"]) for h in hits])
    w = np.clip(np.asarray([h.similarity for h in hits]), 1e-6, None)
    mean = float(r.mean())
    lcb = mean - z * float(r.std(ddof=1)) / math.sqrt(len(r)) if len(r) > 1 else -math.inf
    return RecallSummary(len(r), mean, float(np.average(r, weights=w)), float((r > 0).mean()), lcb)


class EpisodeRecall:
    """Finds past situations most similar to the current market state, known before the decision time."""

    def __init__(self, store: VectorStore, top_k: int, min_similarity: float) -> None:
        self.store = store
        self.top_k = top_k
        self.min_similarity = min_similarity

    def neighbors(self, state: np.ndarray, symbol: str, direction: str, at: datetime) -> list[Hit]:
        hits = self.store.search(EPISODES, state, self.top_k, at, {"symbol": symbol, "direction": direction})
        return [h for h in hits if h.similarity >= self.min_similarity]

    @staticmethod
    def for_entry(hits: list[Hit], technique: str) -> list[Hit]:
        entry = entry_of(technique)
        return [h for h in hits if h.metadata.get("entry") == entry]


def learner_input(
    features: dict[str, Any], regime: str, technique: str, direction: str, reward_risk: float
) -> dict[str, float]:
    x: dict[str, float] = {"reward_risk": reward_risk}
    for key, _, _ in STATE_SPEC:
        v = features.get(key)
        if v is not None and math.isfinite(float(v)):
            x[key] = float(v)
    x[f"entry={entry_of(technique)}"] = 1.0
    x[f"regime={regime}"] = 1.0
    x["long"] = 1.0 if direction == "LONG" else 0.0
    return x


class OnlineLearner:
    """Logistic model (river) trained one outcome at a time; trusted only while it beats the base rate."""

    def __init__(self, min_samples: int, min_gain: float = 0.01, window: int = 500) -> None:
        from river import compose, linear_model, optim, preprocessing

        self.model = compose.Pipeline(
            preprocessing.StandardScaler(), linear_model.LogisticRegression(optimizer=optim.SGD(0.01))
        )
        self.min_samples = min_samples
        self.min_gain = min_gain
        self.n = 0
        self.wins = 0
        self._model_ll: deque[float] = deque(maxlen=window)
        self._base_ll: deque[float] = deque(maxlen=window)

    def predict(self, x: dict[str, float]) -> float:
        return float(self.model.predict_proba_one(x).get(True, 0.5))

    def learn(self, x: dict[str, float], won: bool) -> None:
        """Test-then-train: score the prediction made before seeing the outcome, then update."""
        p = min(max(self.predict(x), 1e-6), 1 - 1e-6)
        base = min(max((self.wins + 1) / (self.n + 2), 1e-6), 1 - 1e-6)
        self._model_ll.append(-math.log(p if won else 1 - p))
        self._base_ll.append(-math.log(base if won else 1 - base))
        self.model.learn_one(x, won)
        self.n += 1
        self.wins += int(won)

    @property
    def trusted(self) -> bool:
        return self.n >= self.min_samples and self.base_log_loss - self.log_loss >= self.min_gain

    @property
    def log_loss(self) -> float:
        return float(np.mean(self._model_ll)) if self._model_ll else math.nan

    @property
    def base_log_loss(self) -> float:
        return float(np.mean(self._base_ll)) if self._base_ll else math.nan
