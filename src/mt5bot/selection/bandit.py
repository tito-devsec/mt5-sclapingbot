"""LinUCB contextual bandit evaluated only by offline replay with delayed rewards."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class LinUCB:
    arms: list[str]
    dim: int
    alpha: float = 1.0
    A: dict[str, np.ndarray] = field(default_factory=dict)
    b: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for a in self.arms:
            self.A[a] = np.eye(self.dim)
            self.b[a] = np.zeros(self.dim)

    def ucb(self, arm: str, x: np.ndarray) -> float:
        inv = np.linalg.inv(self.A[arm])
        theta = inv @ self.b[arm]
        return float(theta @ x + self.alpha * np.sqrt(x @ inv @ x))

    def update(self, arm: str, x: np.ndarray, r: float) -> None:
        self.A[arm] += np.outer(x, x)
        self.b[arm] += r * x


def replay(
    labelled: pd.DataFrame, feature_cols: list[str], *, alpha: float = 1.0, abstain_below: float = 0.0
) -> dict[str, float]:
    """Replay chronologically, revealing each reward only after its label_available_at."""
    df = labelled.sort_values("available_at", kind="stable").reset_index(drop=True)
    arms = sorted(df["strategy"].unique())
    x_all = df[feature_cols].to_numpy(float)
    mu, sd = x_all.mean(axis=0), x_all.std(axis=0) + 1e-9
    model = LinUCB(arms, len(feature_cols) + 1, alpha)
    pending: list[tuple[pd.Timestamp, int, str]] = []
    total, taken = 0.0, 0
    for t, group in df.groupby("available_at", sort=True):
        while pending and pending[0][0] <= t:
            _, j, arm = heapq.heappop(pending)
            model.update(arm, np.append((x_all[j] - mu) / sd, 1.0), float(df.at[j, "reward"]))
        best_j, best_u = None, abstain_below
        for j in group.index:
            u = model.ucb(df.at[j, "strategy"], np.append((x_all[j] - mu) / sd, 1.0))
            if u > best_u:
                best_j, best_u = j, u
        if best_j is not None:
            total += float(df.at[best_j, "reward"])
            taken += 1
            heapq.heappush(pending, (df.at[best_j, "label_available_at"], int(best_j), df.at[best_j, "strategy"]))
    return {
        "decisions": int(df["available_at"].nunique()),
        "taken": taken,
        "total_reward": total,
        "mean_reward": total / taken if taken else 0.0,
    }
