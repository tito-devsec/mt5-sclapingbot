"""Embeddings: a fixed market-state embedding and a deterministic hashing text embedder (both offline)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from mt5bot.core.types import Regime

# (feature, centre, scale): value -> tanh((x - centre) / scale); fixed so stored vectors never need re-embedding.
STATE_SPEC: tuple[tuple[str, float, float], ...] = (
    ("body_ratio", 0.0, 1.0),
    ("range_atr", 1.0, 1.0),
    ("mom_10", 0.0, 3.0),
    ("accel_10_5", 0.0, 3.0),
    ("rsi_14", 50.0, 25.0),
    ("macd_hist_atr", 0.0, 0.5),
    ("ema_20_50_atr", 0.0, 2.0),
    ("ema20_slope_atr", 0.0, 1.0),
    ("close_ema20_atr", 0.0, 2.0),
    ("adx_14", 25.0, 15.0),
    ("vol_pctile", 0.5, 0.5),
    ("range_expansion", 1.0, 0.5),
    ("spread_atr", 0.1, 0.2),
    ("tickvol_z", 0.0, 2.0),
    ("dist_high_20", 1.5, 1.5),
    ("dist_low_20", 1.5, 1.5),
    ("sr_res_dist_atr", 1.0, 3.0),
    ("sr_sup_dist_atr", 1.0, 3.0),
    ("struct_trend", 0.0, 1.0),
    ("tl_slope_atr", 0.0, 0.1),
    ("tl_dev_atr", 0.0, 2.0),
    ("htf_ema_20_50_atr", 0.0, 2.0),
    ("htf_alignment", 0.0, 1.0),
)
STATE_FLAGS: tuple[str, ...] = (
    "cs_bull_engulf",
    "cs_bear_engulf",
    "cs_pin_bull",
    "cs_pin_bear",
    "cs_doji",
    "cs_inside",
    "session_open_30m",
)
SESSIONS: tuple[str, ...] = ("in_asia", "in_london", "in_new_york")
REGIMES: tuple[str, ...] = tuple(r.value for r in Regime)
STATE_DIM = len(STATE_SPEC) + len(STATE_FLAGS) + len(SESSIONS) + 2 + len(REGIMES)
STATE_EMBEDDING_VERSION = "state-1"


def _num(features: dict[str, Any], key: str) -> float | None:
    v = features.get(key)
    if v is None:
        return None
    x = float(v)
    return None if math.isnan(x) or math.isinf(x) else x


def state_embedding(features: dict[str, Any], regime: str) -> np.ndarray:
    """Unit-length market-state vector; missing inputs contribute 0 (neutral) to their dimension."""
    out = np.zeros(STATE_DIM, dtype=np.float32)
    i = 0
    for key, centre, scale in STATE_SPEC:
        x = _num(features, key)
        out[i] = 0.0 if x is None else math.tanh((x - centre) / scale)
        i += 1
    for key in (*STATE_FLAGS, *SESSIONS):
        x = _num(features, key)
        out[i] = 0.0 if x is None else (1.0 if x > 0 else -1.0) * 0.5
        i += 1
    hour = _num(features, "hour_utc")
    if hour is not None:
        out[i], out[i + 1] = math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24)
    i += 2
    if regime in REGIMES:
        out[i + REGIMES.index(regime)] = 1.0
    norm = float(np.linalg.norm(out))
    return out / norm if norm > 0 else out


class HashingEmbedder:
    """Deterministic text embedder: hashed word 1-2 grams, L2-normalised; needs no model download."""

    def __init__(self, dim: int = 256) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self.dim = dim
        self._vec = HashingVectorizer(
            n_features=dim, ngram_range=(1, 2), norm="l2", alternate_sign=True, lowercase=True
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._vec.transform(texts).toarray().astype(np.float32)
