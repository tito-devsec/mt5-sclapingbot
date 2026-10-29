"""Causal GARCH(1,1) one-step volatility forecasts using only returns up to each row (optional arch)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def garch_forecast(close: pd.Series, window: int = 1000, refit_every: int = 250) -> pd.Series:
    from arch import arch_model

    r = 100 * np.log(close).diff()
    out = pd.Series(np.nan, index=close.index, dtype=float)
    params = None
    for t in range(window, len(close)):
        if params is None or (t - window) % refit_every == 0:
            hist = r.iloc[t - window + 1 : t + 1].dropna()
            res = arch_model(hist, vol="GARCH", p=1, q=1, mean="Zero", rescale=False).fit(disp="off")
            params = res.params
        # one-step variance recursion with the latest fitted parameters, using data up to t only
        hist = r.iloc[t - window + 1 : t + 1].dropna().to_numpy()
        omega, alpha, beta = params["omega"], params["alpha[1]"], params["beta[1]"]
        var = np.var(hist)
        for x in hist:
            var = omega + alpha * x * x + beta * var
        out.iloc[t] = np.sqrt(var) / 100
    return out
