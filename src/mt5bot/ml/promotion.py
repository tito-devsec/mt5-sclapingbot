"""Promotion gates: a challenger replaces the champion only if every gate passes."""

from __future__ import annotations

from typing import Any

from mt5bot.config.settings import EvaluationSettings
from mt5bot.ml.registry import GateResult


def evaluate_gates(
    summary: dict[str, Any], champion_on_test: dict[str, Any] | None, cfg: EvaluationSettings
) -> GateResult:
    """Apply promotion gates to a report summary, comparing with the champion on the same test window."""
    test = summary["test"]
    sel, stressed = test["selected"], test["selected_stressed"]
    base = summary["baselines"]["test"]["rules_all_eligible"]
    g: dict[str, dict[str, Any]] = {}

    def gate(name: str, ok: bool, value: Any, threshold: Any) -> None:
        g[name] = {"passed": bool(ok), "value": value, "threshold": threshold}

    gate("min_trades", sel["trades"] >= cfg.min_trades_for_promotion, sel["trades"], cfg.min_trades_for_promotion)
    gate("positive_expectancy_after_costs", sel["expectancy_r"] > 0, sel["expectancy_r"], "> 0")
    gate(
        "beats_rule_baseline",
        sel["expectancy_r"] > base.get("expectancy_r", 0.0),
        sel["expectancy_r"],
        base.get("expectancy_r", 0.0),
    )
    if champion_on_test is not None:
        champ = champion_on_test["selected"]["expectancy_r"]
        gate("beats_champion", sel["expectancy_r"] > champ, sel["expectancy_r"], champ)
    gate(
        "survives_cost_stress",
        stressed["expectancy_r"] > 0,
        stressed["expectancy_r"],
        f"> 0 at x{cfg.promotion_cost_multiplier} costs",
    )
    gate(
        "max_drawdown_r",
        sel.get("max_drawdown_r", 0.0) <= cfg.max_drawdown_r_for_promotion,
        sel.get("max_drawdown_r", 0.0),
        cfg.max_drawdown_r_for_promotion,
    )
    gate(
        "calibration_brier",
        test["classification"]["brier"] <= cfg.max_brier_for_promotion,
        test["classification"]["brier"],
        cfg.max_brier_for_promotion,
    )
    gate(
        "walk_forward_consistency",
        summary["walk_forward_positive_share"] >= cfg.min_positive_fold_share,
        summary["walk_forward_positive_share"],
        cfg.min_positive_fold_share,
    )
    dsr = sel.get("deflated_sharpe_prob")
    gate(
        "deflated_sharpe",
        dsr is not None and dsr == dsr and dsr >= cfg.min_deflated_sharpe_prob,
        dsr,
        cfg.min_deflated_sharpe_prob,
    )
    return GateResult(all(v["passed"] for v in g.values()), g)
