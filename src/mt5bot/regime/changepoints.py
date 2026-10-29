"""Offline ruptures change-point audit of regime labels; never used for features or decisions."""

from __future__ import annotations

import numpy as np
import pandas as pd


def breakpoints(
    features: pd.DataFrame, cols: tuple[str, ...] = ("ret_5", "rv_30"), penalty: float = 10.0, min_size: int = 30
) -> list[int]:
    import ruptures as rpt

    x = features[list(cols)].dropna()
    if len(x) < min_size * 3:
        return []
    z = ((x - x.mean()) / (x.std() + 1e-12)).to_numpy()
    bkps = rpt.Pelt(model="rbf", min_size=min_size).fit(z).predict(pen=penalty)
    return [int(x.index[b - 1]) for b in bkps[:-1]]  # row labels of the last bar before each break


def transition_agreement(regimes: pd.Series, breaks: list[int], tolerance: int = 30) -> dict[str, float]:
    """Share of change points that have a regime transition within ``tolerance`` rows, and vice versa."""
    r = regimes.reset_index(drop=True)
    transitions = np.flatnonzero((r != r.shift()).to_numpy()[1:]) + 1
    if not breaks or len(transitions) == 0:
        return {
            "breaks_matched": 0.0,
            "transitions_matched": 0.0,
            "n_breaks": len(breaks),
            "n_transitions": len(transitions),
        }
    b = np.asarray(breaks)
    bm = np.mean([np.min(np.abs(transitions - x)) <= tolerance for x in b])
    tm = np.mean([np.min(np.abs(b - t)) <= tolerance for t in transitions])
    return {
        "breaks_matched": float(bm),
        "transitions_matched": float(tm),
        "n_breaks": len(breaks),
        "n_transitions": len(transitions),
    }
