"""Drift detectors (PSI, chi-square, Brier, Page-Hinkley/ADWIN, staleness, spread shift)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    ref = np.asarray(reference, float)
    cur = np.asarray(current, float)
    ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
    if len(ref) < bins * 5 or len(cur) < bins * 2:
        return math.nan
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return math.nan
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-6, None), np.clip(c, 1e-6, None)
    return float(np.sum((c - r) * np.log(c / r)))


def regime_frequency_pvalue(reference: pd.Series, current: pd.Series) -> float:
    cats = sorted(set(reference) | set(current))
    if len(cats) < 2 or len(current) < 30:
        return math.nan
    ref_p = reference.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy() + 1e-6
    obs = current.value_counts().reindex(cats, fill_value=0).to_numpy()
    exp = ref_p / ref_p.sum() * obs.sum()
    return float(stats.chisquare(obs, exp).pvalue)


@dataclass
class PageHinkley:
    """Detects a downward shift in the mean of a stream (e.g. strategy rewards)."""

    delta: float = 0.05
    threshold: float = 8.0
    min_samples: int = 30

    def detect(self, x: np.ndarray) -> int | None:
        """Return the index where a decrease is detected (U_t - min U > threshold), or None."""
        mean, u, u_min = 0.0, 0.0, 0.0
        for i, v in enumerate(np.asarray(x, float)):
            mean += (v - mean) / (i + 1)
            u += mean - v - self.delta
            u_min = min(u_min, u)
            if i + 1 >= self.min_samples and u - u_min > self.threshold:
                return i
        return None


def adwin_change(x: np.ndarray, delta: float = 0.002) -> int | None:
    try:
        from river.drift import ADWIN
    except ImportError:
        return None
    det = ADWIN(delta=delta)
    for i, v in enumerate(np.asarray(x, float)):
        det.update(float(v))
        if det.drift_detected:
            return i
    return None


def brier_degradation(baseline_brier: float, y: np.ndarray, p: np.ndarray) -> float:
    if len(y) == 0 or not math.isfinite(baseline_brier) or baseline_brier <= 0:
        return math.nan
    recent = float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))
    return recent / baseline_brier - 1.0


def spread_shift(reference: np.ndarray, current: np.ndarray) -> tuple[float, float]:
    """(p95 ratio current/reference, KS p-value)."""
    ref, cur = np.asarray(reference, float), np.asarray(current, float)
    if len(ref) < 50 or len(cur) < 20:
        return math.nan, math.nan
    ratio = float(np.quantile(cur, 0.95) / max(np.quantile(ref, 0.95), 1e-9))
    return ratio, float(stats.ks_2samp(ref, cur).pvalue)


def missed_bars(last_bar: pd.Timestamp | None, now: pd.Timestamp, tf_minutes: int) -> int | None:
    if last_bar is None:
        return None
    return max(0, int((now - (last_bar + pd.Timedelta(minutes=tf_minutes))) / pd.Timedelta(minutes=tf_minutes)))
