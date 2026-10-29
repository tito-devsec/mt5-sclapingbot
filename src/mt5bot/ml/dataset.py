"""Training datasets built from labelled candidates and the features known at decision time."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from mt5bot.core.types import Regime

STRATEGY_NAMES = ("trend", "breakout", "mean_reversion")
META_COLUMNS = [
    "time_utc",
    "available_at",
    "label_available_at",
    "strategy",
    "direction",
    "regime",
    "net_r",
    "cost_r",
    "label",
    "eligible",
]


@dataclass
class MetaDataset:
    X: pd.DataFrame
    y: np.ndarray
    meta: pd.DataFrame
    feature_names: list[str]

    def __len__(self) -> int:
        return len(self.y)

    def subset(self, idx: np.ndarray) -> MetaDataset:
        return MetaDataset(
            self.X.iloc[idx].reset_index(drop=True),
            self.y[idx],
            self.meta.iloc[idx].reset_index(drop=True),
            self.feature_names,
        )

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(",".join(self.feature_names).encode())
        h.update(pd.util.hash_pandas_object(self.X.round(10), index=False).to_numpy().tobytes())
        h.update(self.y.tobytes())
        h.update(pd.util.hash_pandas_object(self.meta["available_at"], index=False).to_numpy().tobytes())
        return h.hexdigest()[:20]


def context_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Candidate context known before entry: strategy, direction and regime indicators."""
    out = pd.DataFrame(index=df.index)
    for s in STRATEGY_NAMES:
        out[f"strat_{s}"] = (df["strategy"] == s).astype(float)
    out["direction_long"] = (df["direction"] == "LONG").astype(float)
    for r in Regime:
        out[f"regime_{r.value.lower()}"] = (df["regime"] == r.value).astype(float)
    return out


def build_meta_dataset(
    labelled: pd.DataFrame, features: pd.DataFrame, feature_cols: list[str], drop_all_nan: bool = True
) -> MetaDataset:
    """Join labelled candidates with features at their bar. Rows with missing features are dropped."""
    if labelled.empty:
        return MetaDataset(
            pd.DataFrame(columns=feature_cols),
            np.array([], dtype=int),
            pd.DataFrame(columns=META_COLUMNS),
            list(feature_cols),
        )
    f = features.set_index("time_utc")[feature_cols]
    joined = labelled.join(f, on="time_utc")
    cols = list(feature_cols)
    if drop_all_nan:
        cols = [c for c in cols if joined[c].notna().any()]
    ctx = context_columns(joined)
    X = pd.concat([joined[cols], ctx], axis=1)
    ok = X.notna().all(axis=1).to_numpy()
    meta = joined.loc[ok, [c for c in META_COLUMNS if c in joined]].reset_index(drop=True)
    X = X.loc[ok].reset_index(drop=True).astype(float)
    order = np.argsort(meta["available_at"].to_numpy(), kind="stable")
    return MetaDataset(
        X.iloc[order].reset_index(drop=True),
        joined.loc[ok, "label"].to_numpy(int)[order],
        meta.iloc[order].reset_index(drop=True),
        list(X.columns),
    )


def inference_frame(features_row: pd.DataFrame, candidates: pd.DataFrame, feature_names: list[str]) -> pd.DataFrame:
    """Build the model input for live candidates with exactly the trained feature order."""
    base = candidates.join(features_row.set_index("time_utc"), on="time_utc")
    X = pd.concat([base, context_columns(base)], axis=1)
    missing = [c for c in feature_names if c not in X]
    if missing:
        raise KeyError(f"inference input lacks trained features: {missing}")
    return X[feature_names].astype(float)
