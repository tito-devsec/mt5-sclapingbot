"""Uncertainty and multiple-testing statistics for strategy evaluation."""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def block_bootstrap_ci(
    x: np.ndarray, *, block: int = 10, n_boot: int = 2000, alpha: float = 0.05, seed: int = 42
) -> tuple[float, float]:
    """Moving-block bootstrap CI for the mean (keeps short-range dependence)."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return (math.nan, math.nan)
    block = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    means = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n - block + 1, n_blocks)
        sample = np.concatenate([x[s : s + block] for s in starts])[:n]
        means[i] = sample.mean()
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def probabilistic_sharpe(x: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """P(true Sharpe > benchmark) given sample skew/kurtosis (Bailey & Lopez de Prado, 2012)."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 3 or x.std(ddof=1) == 0:
        return math.nan
    sr = x.mean() / x.std(ddof=1)
    skew = stats.skew(x)
    kurt = stats.kurtosis(x, fisher=False)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr**2))
    return float(stats.norm.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / denom))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe among ``n_trials`` skill-less strategies."""
    if n_trials <= 1:
        return 0.0
    z1 = stats.norm.ppf(1 - 1 / n_trials)
    z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
    return float(math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe(x: np.ndarray, n_trials: int, sr_variance: float | None = None) -> float:
    """PSR against the expected-max Sharpe of n_trials trials (Bailey & Lopez de Prado, 2014)."""
    x = np.asarray(x, float)
    if len(x) < 3:
        return math.nan
    var = sr_variance if sr_variance is not None else 1.0 / len(x)
    return probabilistic_sharpe(x, expected_max_sharpe(n_trials, var))


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n * n)) / denom
    return (centre - half, centre + half)
