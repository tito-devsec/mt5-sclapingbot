"""Deterministic feature pipeline; a bar's features are available at its close, higher-TF bars only once closed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from mt5bot.core.types import Timeframe
from mt5bot.features import indicators as ind

FEATURE_SET_VERSION = "1.0.0"

HIGHER_TF: dict[Timeframe, Timeframe | None] = {
    Timeframe.M1: Timeframe.M5,
    Timeframe.M5: Timeframe.M15,
    Timeframe.M15: Timeframe.H1,
    Timeframe.H1: None,
}


@dataclass(frozen=True)
class FeatureSpec:
    """``warmup_bars`` = number of leading rows that are NaN (for ``htf_*``: higher-timeframe bars)."""

    name: str
    family: str
    description: str
    warmup_bars: int
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class FeatureConfig:
    vol_percentile_window: int = 500
    sessions_utc: tuple[tuple[str, int, int], ...] = (("asia", 0, 8), ("london", 7, 16), ("new_york", 12, 21))

    @classmethod
    def from_settings(cls, fs: object) -> FeatureConfig:
        sessions = tuple((k, int(v[0]), int(v[1])) for k, v in fs.sessions_utc.items())  # type: ignore[attr-defined]
        return cls(vol_percentile_window=fs.vol_percentile_window, sessions_utc=sessions)  # type: ignore[attr-defined]


def _specs(cfg: FeatureConfig) -> list[FeatureSpec]:
    w = cfg.vol_percentile_window
    S = FeatureSpec
    specs = [
        S("ret_1", "price", "log return over 1 bar", 1, ("close",)),
        S("ret_5", "price", "log return over 5 bars", 5, ("close",)),
        S("ret_15", "price", "log return over 15 bars", 15, ("close",)),
        S("body_ratio", "price", "(close-open)/(high-low); 0 for zero-range bars", 0, ("open", "high", "low", "close")),
        S(
            "upper_wick_ratio",
            "price",
            "(high-max(open,close))/(high-low); 0 for zero-range bars",
            0,
            ("open", "high", "low", "close"),
        ),
        S(
            "lower_wick_ratio",
            "price",
            "(min(open,close)-low)/(high-low); 0 for zero-range bars",
            0,
            ("open", "high", "low", "close"),
        ),
        S("range_atr", "price", "(high-low)/ATR14", 14, ("high", "low", "close")),
        S("consec_up", "price", "consecutive bars with close>open", 0, ("open", "close")),
        S("consec_down", "price", "consecutive bars with close<open", 0, ("open", "close")),
        S("dist_high_20", "price", "(max high of last 20 bars incl. current - close)/ATR14", 19, ("high", "close")),
        S("dist_low_20", "price", "(close - min low of last 20 bars incl. current)/ATR14", 19, ("low", "close")),
        S("breakout_up_20", "price", "(close - highest high of previous 20 bars)/ATR14", 20, ("high", "close")),
        S("breakout_dn_20", "price", "(lowest low of previous 20 bars - close)/ATR14", 20, ("low", "close")),
        S("mom_10", "momentum", "(close - close 10 bars ago)/ATR14", 14, ("close",)),
        S("accel_10_5", "momentum", "mom_10 - mom_10 five bars earlier", 19, ("close",)),
        S("rsi_14", "momentum", "RSI(14), Wilder smoothing", 14, ("close",)),
        S("macd_hist_atr", "momentum", "MACD(12,26,9) histogram / ATR14", 33, ("close",)),
        S("macd_above_signal", "momentum", "1 if MACD line > signal else 0", 33, ("close",)),
        S("ema_20_50_atr", "trend", "(EMA20 - EMA50)/ATR14", 49, ("close",)),
        S("ema20_slope_atr", "trend", "(EMA20 - EMA20 five bars earlier)/ATR14", 24, ("close",)),
        S("close_ema20_atr", "trend", "(close - EMA20)/ATR14", 19, ("close",)),
        S("adx_14", "trend", "ADX(14)", 27, ("high", "low", "close")),
        S("plus_di_14", "trend", "+DI(14)", 14, ("high", "low", "close")),
        S("minus_di_14", "trend", "-DI(14)", 14, ("high", "low", "close")),
        S("atr_14", "volatility", "ATR(14) in price units", 14, ("high", "low", "close")),
        S("atr_pct", "volatility", "ATR14 / close", 14, ("high", "low", "close")),
        S("rv_30", "volatility", "std of 1-bar log returns over 30 bars", 30, ("close",)),
        S(
            "vol_pctile",
            "volatility",
            f"percentile of atr_pct within the trailing {w} bars",
            w + 13,
            ("high", "low", "close"),
        ),
        S("range_expansion", "volatility", "mean range of last 5 bars / mean range of last 50", 49, ("high", "low")),
        S("bb_width", "volatility", "Bollinger(20,2) width / middle band", 19, ("close",)),
        S("spread_points", "cost", "broker-reported spread in points (bar value)", 0, ("spread_points",)),
        S("spread_atr", "cost", "spread in price / ATR14", 14, ("spread_points", "high", "low", "close")),
        S(
            "tickvol_z",
            "cost",
            "z-score of tick count vs. previous 50 bars (tick count, not traded volume)",
            50,
            ("tick_volume",),
        ),
        S("hour_utc", "session", "UTC hour of the bar open", 0, ("time_utc",)),
        S("dow", "session", "day of week of the bar open (0=Mon)", 0, ("time_utc",)),
    ]
    for name, start, end in cfg.sessions_utc:
        specs.append(S(f"in_{name}", "session", f"1 if bar open hour in [{start},{end}) UTC", 0, ("time_utc",)))
    specs.append(
        S(
            "session_open_30m",
            "session",
            "1 if any configured session opened within the last 30 minutes",
            0,
            ("time_utc",),
        )
    )
    specs += [
        S(
            "htf_ema_20_50_atr",
            "cross_tf",
            "higher-TF (EMA20-EMA50)/ATR14 of the last CLOSED higher bar",
            49,
            ("close",),
        ),
        S("htf_rsi_14", "cross_tf", "higher-TF RSI14 of the last closed higher bar", 14, ("close",)),
        S("htf_adx_14", "cross_tf", "higher-TF ADX14 of the last closed higher bar", 27, ("close",)),
        S("htf_close_ema20_atr", "cross_tf", "(close - higher-TF EMA20)/higher-TF ATR14", 19, ("close",)),
        S(
            "htf_alignment",
            "cross_tf",
            "sign(ema_20_50_atr) * sign(htf_ema_20_50_atr): +1 aligned, -1 opposed",
            49,
            ("close",),
        ),
    ]
    return specs


def feature_specs(cfg: FeatureConfig | None = None) -> list[FeatureSpec]:
    return _specs(cfg or FeatureConfig())


def feature_names(cfg: FeatureConfig | None = None) -> list[str]:
    return [s.name for s in feature_specs(cfg)]


def feature_set_hash(cfg: FeatureConfig | None = None) -> str:
    cfg = cfg or FeatureConfig()
    payload = {"version": FEATURE_SET_VERSION, "config": asdict(cfg), "specs": [asdict(s) for s in _specs(cfg)]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _safe_ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    out = num / den.where(den > 0)
    return out.where(den != 0, 0.0)


def _single_tf(bars: pd.DataFrame, point: float, cfg: FeatureConfig) -> pd.DataFrame:
    o, h, lo, c = bars["open"], bars["high"], bars["low"], bars["close"]
    f = pd.DataFrame(index=bars.index)
    logc = np.log(c)
    for k in (1, 5, 15):
        f[f"ret_{k}"] = logc - logc.shift(k)
    rng = h - lo
    f["body_ratio"] = _safe_ratio(c - o, rng)
    f["upper_wick_ratio"] = _safe_ratio(h - pd.concat([o, c], axis=1).max(axis=1), rng)
    f["lower_wick_ratio"] = _safe_ratio(pd.concat([o, c], axis=1).min(axis=1) - lo, rng)
    a = ind.atr(h, lo, c, 14)
    f["range_atr"] = rng / a
    f["consec_up"] = ind.run_length(c > o)
    f["consec_down"] = ind.run_length(c < o)
    f["dist_high_20"] = (h.rolling(20, min_periods=20).max() - c) / a
    f["dist_low_20"] = (c - lo.rolling(20, min_periods=20).min()) / a
    don = ind.donchian_prev(h, lo, 20)
    f["breakout_up_20"] = (c - don["upper"]) / a
    f["breakout_dn_20"] = (don["lower"] - c) / a
    mom = (c - c.shift(10)) / a
    f["mom_10"] = mom
    f["accel_10_5"] = mom - mom.shift(5)
    f["rsi_14"] = ind.rsi(c, 14)
    m = ind.macd(c)
    f["macd_hist_atr"] = m["hist"] / a
    f["macd_above_signal"] = (m["macd"] > m["signal"]).astype(float).where(m["signal"].notna())
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    f["ema_20_50_atr"] = (e20 - e50) / a
    f["ema20_slope_atr"] = (e20 - e20.shift(5)) / a
    f["close_ema20_atr"] = (c - e20) / a
    d = ind.dmi_adx(h, lo, c, 14)
    f["adx_14"], f["plus_di_14"], f["minus_di_14"] = d["adx"], d["plus_di"], d["minus_di"]
    f["atr_14"] = a
    f["atr_pct"] = a / c
    f["rv_30"] = ind.realized_vol(c, 30)
    f["vol_pctile"] = ind.rolling_percentile(f["atr_pct"], cfg.vol_percentile_window)
    f["range_expansion"] = rng.rolling(5, min_periods=5).mean() / rng.rolling(50, min_periods=50).mean()
    f["bb_width"] = ind.bollinger_width(c)
    f["spread_points"] = bars["spread_points"].astype(float)
    f["spread_atr"] = bars["spread_points"] * point / a
    tv = bars["tick_volume"].astype(float)
    prev = tv.shift(1)
    f["tickvol_z"] = (tv - prev.rolling(50, min_periods=50).mean()) / prev.rolling(50, min_periods=50).std()
    t = bars["time_utc"]
    f["hour_utc"] = t.dt.hour.astype(float)
    f["dow"] = t.dt.day_of_week.astype(float)
    minute_of_day = t.dt.hour * 60 + t.dt.minute
    opened = pd.Series(False, index=bars.index)
    for name, start, end in cfg.sessions_utc:
        f[f"in_{name}"] = ((t.dt.hour >= start) & (t.dt.hour < end)).astype(float)
        since = minute_of_day - start * 60
        opened |= (since >= 0) & (since < 30)
    f["session_open_30m"] = opened.astype(float)
    return f


def _htf_frame(htf_bars: pd.DataFrame, htf: Timeframe) -> pd.DataFrame:
    h, lo, c = htf_bars["high"], htf_bars["low"], htf_bars["close"]
    a = ind.atr(h, lo, c, 14)
    e20 = ind.ema(c, 20)
    out = pd.DataFrame(
        {
            "available_at": htf_bars["time_utc"] + htf.delta,
            "htf_ema_20_50_atr": (e20 - ind.ema(c, 50)) / a,
            "htf_rsi_14": ind.rsi(c, 14),
            "htf_adx_14": ind.dmi_adx(h, lo, c, 14)["adx"],
            "_htf_ema20": e20,
            "_htf_atr": a,
        }
    )
    return out.sort_values("available_at")


def build_features(
    bars: pd.DataFrame,
    timeframe: Timeframe,
    point: float,
    higher_bars: pd.DataFrame | None = None,
    cfg: FeatureConfig | None = None,
) -> pd.DataFrame:
    """Return one row per input bar with ``time_utc``, ``available_at`` and all registered features."""
    cfg = cfg or FeatureConfig()
    if bars.empty:
        return pd.DataFrame(columns=["time_utc", "available_at", *feature_names(cfg)])
    bars = bars.sort_values("time_utc").reset_index(drop=True)
    f = _single_tf(bars, point, cfg)
    f.insert(0, "available_at", bars["time_utc"] + timeframe.delta)
    f.insert(0, "time_utc", bars["time_utc"])
    htf = HIGHER_TF[timeframe]
    if htf is not None and higher_bars is not None and not higher_bars.empty:
        hf = _htf_frame(higher_bars.sort_values("time_utc").reset_index(drop=True), htf)
        merged = pd.merge_asof(f[["available_at"]], hf, on="available_at", direction="backward")
        for col in ("htf_ema_20_50_atr", "htf_rsi_14", "htf_adx_14"):
            f[col] = merged[col].to_numpy()
        f["htf_close_ema20_atr"] = ((bars["close"] - merged["_htf_ema20"]) / merged["_htf_atr"]).to_numpy()
    else:
        for col in ("htf_ema_20_50_atr", "htf_rsi_14", "htf_adx_14", "htf_close_ema20_atr"):
            f[col] = np.nan
    f["htf_alignment"] = np.sign(f["ema_20_50_atr"]) * np.sign(f["htf_ema_20_50_atr"])
    f = f.replace([np.inf, -np.inf], np.nan)
    return f[["time_utc", "available_at", *feature_names(cfg)]]


def features_markdown(cfg: FeatureConfig | None = None) -> str:
    cfg = cfg or FeatureConfig()
    lines = [
        "# Feature reference",
        "",
        f"Generated from `mt5bot.features.pipeline` (feature-set version {FEATURE_SET_VERSION}, "
        f"hash `{feature_set_hash(cfg)}`). Regenerate with `mt5bot features docs`.",
        "",
        "Timing: a bar opening at *t* closes at *t + timeframe*; its features become available at that close. "
        "Higher-timeframe (`htf_*`) features come from the last higher-timeframe bar that had **closed** by then "
        "(M1→M5, M5→M15, M15→H1; H1 has none). Warm-up rows are NaN and are dropped before training.",
        "",
        "| Feature | Family | Calculation | Warm-up (bars) | Inputs |",
        "|---|---|---|---|---|",
    ]
    for s in _specs(cfg):
        lines.append(f"| `{s.name}` | {s.family} | {s.description} | {s.warmup_bars} | {', '.join(s.inputs)} |")
    return "\n".join(lines) + "\n"
