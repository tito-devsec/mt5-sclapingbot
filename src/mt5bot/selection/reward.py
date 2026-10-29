"""Selection reward: clip(net_R, low, high) - mae_penalty * max(0, MAE_R - 1); see docs/ML_GUIDE.md."""

from __future__ import annotations

import math

from mt5bot.config.settings import SelectionSettings


def reward(net_r: float, mae_r: float, cfg: SelectionSettings) -> float:
    if net_r is None or math.isnan(net_r):
        raise ValueError("reward needs an observed net_r")
    clipped = min(max(net_r, cfg.reward_clip_low), cfg.reward_clip_high)
    mae = 0.0 if mae_r is None or math.isnan(mae_r) else mae_r
    return clipped - cfg.mae_penalty * max(0.0, mae - 1.0)
