"""Chronological splits with purging (labels known before eval start) and an embargo."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Split:
    name: str
    train_idx: np.ndarray
    eval_idx: np.ndarray
    eval_start: pd.Timestamp
    eval_end: pd.Timestamp


def _purge(df: pd.DataFrame, train_mask: np.ndarray, eval_start: pd.Timestamp, embargo: pd.Timedelta) -> np.ndarray:
    known = (df["label_available_at"] <= eval_start - embargo).to_numpy(bool)
    return train_mask & known


def train_val_test(
    df: pd.DataFrame, val_fraction: float, test_fraction: float, embargo: pd.Timedelta
) -> tuple[Split, Split]:
    """One chronological train / validation / untouched test partition."""
    d = df.sort_values("available_at", kind="stable").reset_index(drop=True)
    t = d["available_at"]
    n = len(d)
    v0 = t.iloc[int(n * (1 - val_fraction - test_fraction))]
    t0 = t.iloc[int(n * (1 - test_fraction))]
    tr = (t < v0).to_numpy()
    va = ((t >= v0) & (t < t0)).to_numpy()
    te = (t >= t0).to_numpy()
    val = Split("validation", np.flatnonzero(_purge(d, tr, v0, embargo)), np.flatnonzero(va), v0, t0)
    # For the test fold, training may include validation rows (after purging).
    test = Split("test", np.flatnonzero(_purge(d, tr | va, t0, embargo)), np.flatnonzero(te), t0, t.iloc[-1])
    return val, test


def walk_forward(
    df: pd.DataFrame, n_folds: int, embargo: pd.Timedelta, *, expanding: bool = True, min_train_fraction: float = 0.3
) -> list[Split]:
    """Rolling-origin folds over the period after ``min_train_fraction`` of the data."""
    d = df.sort_values("available_at", kind="stable").reset_index(drop=True)
    t = d["available_at"]
    n = len(d)
    start = int(n * min_train_fraction)
    edges = np.linspace(start, n, n_folds + 1).astype(int)
    folds = []
    for k in range(n_folds):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        e0 = t.iloc[a]
        e1 = t.iloc[b - 1]
        tr = (t < e0).to_numpy()
        if not expanding:
            window = t.iloc[a] - t.iloc[max(0, a - start)]
            tr &= (t >= e0 - window).to_numpy()
        ev = np.zeros(n, dtype=bool)
        ev[a:b] = True
        folds.append(Split(f"wf{k + 1}", np.flatnonzero(_purge(d, tr, e0, embargo)), np.flatnonzero(ev), e0, e1))
    return folds
