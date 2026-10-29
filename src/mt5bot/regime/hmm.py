"""Gaussian HMM regime discovery fitted on training rows, predicted with forward filtering only."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mt5bot.core.types import Regime

HMM_FEATURES = ["ret_5", "rv_30", "adx_14", "range_expansion"]


@dataclass
class HMMRegimeModel:
    n_states: int = 4
    seed: int = 42
    model: object | None = None
    state_labels: dict[int, str] = field(default_factory=dict)
    mean_: np.ndarray | None = None
    std_: np.ndarray | None = None

    def fit(self, train: pd.DataFrame) -> HMMRegimeModel:
        from hmmlearn.hmm import GaussianHMM  # optional dependency

        x = train[HMM_FEATURES].dropna().to_numpy()
        if len(x) < self.n_states * 50:
            raise ValueError("not enough training rows for the HMM")
        self.mean_, self.std_ = x.mean(axis=0), x.std(axis=0) + 1e-12
        z = (x - self.mean_) / self.std_
        m = GaussianHMM(n_components=self.n_states, covariance_type="diag", n_iter=200, random_state=self.seed)
        m.fit(z)
        self.model = m
        means = m.means_ * self.std_ + self.mean_
        vol_rank = np.argsort(np.argsort(means[:, 1]))
        for s in range(self.n_states):
            ret, adx = means[s, 0], means[s, 2]
            if vol_rank[s] == self.n_states - 1:
                label = Regime.HIGH_VOL
            elif vol_rank[s] == 0:
                label = Regime.LOW_VOL
            elif adx > 25 and ret > 0:
                label = Regime.TREND_UP
            elif adx > 25 and ret < 0:
                label = Regime.TREND_DOWN
            else:
                label = Regime.RANGE
            self.state_labels[s] = label.value
        return self

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if self.model is None or self.mean_ is None or self.std_ is None:
            raise RuntimeError("HMM is not fitted")
        out = pd.DataFrame({"regime": Regime.UNCERTAIN.value, "regime_confidence": 0.0}, index=features.index)
        valid = features[HMM_FEATURES].notna().all(axis=1)
        if valid.any():
            z = (features.loc[valid, HMM_FEATURES].to_numpy() - self.mean_) / self.std_
            # Forward filtering only; predict_proba would use the smoother and leak future rows.
            post = self._filtered(z)
            states = post.argmax(axis=1)
            out.loc[valid, "regime"] = [self.state_labels[int(s)] for s in states]
            out.loc[valid, "regime_confidence"] = post.max(axis=1)
        return out

    def _filtered(self, z: np.ndarray) -> np.ndarray:
        from scipy.stats import multivariate_normal

        m = self.model
        k = self.n_states
        trans, start = m.transmat_, m.startprob_  # type: ignore[attr-defined]
        covs = m.covars_  # type: ignore[attr-defined]
        dens = np.column_stack(
            [
                multivariate_normal(m.means_[s], covs[s], allow_singular=True).pdf(z)  # type: ignore[attr-defined]
                for s in range(k)
            ]
        )
        dens = np.atleast_2d(dens)
        alpha = np.zeros_like(dens)
        a = start * dens[0]
        alpha[0] = a / max(a.sum(), 1e-300)
        for t in range(1, len(z)):
            a = (alpha[t - 1] @ trans) * dens[t]
            alpha[t] = a / max(a.sum(), 1e-300)
        return alpha
