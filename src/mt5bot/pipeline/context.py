"""Shared helpers for the orchestration layer."""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from mt5bot.backtest.simulator import CostModel, Sizing
from mt5bot.config.settings import Settings
from mt5bot.core.logging import log_event
from mt5bot.core.types import InstrumentSpec, default_spec
from mt5bot.features.pipeline import FeatureConfig
from mt5bot.storage.repositories import InstrumentRepository

log = logging.getLogger(__name__)


def config_hash(settings: Settings) -> str:
    # SecretStr values serialize as '**********', so secrets never enter the hash input.
    return hashlib.sha256(settings.model_dump_json().encode()).hexdigest()[:16]


def feature_config(settings: Settings) -> FeatureConfig:
    return FeatureConfig.from_settings(settings.features)


def cost_model(settings: Settings, symbol: str, multiplier: float = 1.0) -> CostModel:
    c = settings.costs
    return CostModel(
        c.commission_per_lot_per_side_usd,
        c.for_symbol(c.slippage_points, symbol),
        c.for_symbol(c.swap_long_points_per_night, symbol),
        c.for_symbol(c.swap_short_points_per_night, symbol),
        multiplier,
    )


def sizing(settings: Settings) -> Sizing:
    r = settings.risk
    return Sizing(r.reference_balance_usd, r.risk_per_trade_pct, r.max_risk_per_trade_usd)


class SpecProvider:
    """Instrument specs from broker metadata stored in the DB; typical defaults only as a flagged fallback."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.repo = InstrumentRepository(conn)
        self.unverified: set[str] = set()

    def __call__(self, symbol: str) -> InstrumentSpec:
        row = self.repo.get(symbol)
        if row is not None:
            return InstrumentSpec(
                symbol,
                row["base"],
                row["quote"],
                row["point"],
                row["digits"],
                row["contract_size"],
                row["volume_min"],
                row["volume_step"],
                row["volume_max"],
            )
        if symbol not in self.unverified:
            log_event(log, logging.WARNING, "using unverified default contract spec", symbol=symbol)
            self.unverified.add(symbol)
        return default_spec(symbol)
