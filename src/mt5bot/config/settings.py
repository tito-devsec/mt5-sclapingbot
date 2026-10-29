"""Typed, validated configuration from env vars, .env and TOML; unknown keys are rejected."""

from __future__ import annotations

import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from mt5bot.core.types import Timeframe

_SYMBOL_RE = re.compile(r"^[A-Z]{6}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MT5Settings(_Strict):
    terminal_path: str | None = None
    login: SecretStr | None = None
    password: SecretStr | None = None
    server: str | None = None
    # Demo-only is not configurable: the type only admits True.
    require_demo: Literal[True] = True
    expected_currency: str = "USD"
    symbol_map: dict[str, str] = Field(default_factory=dict)
    # Server-time mapping: "NY+7", an IANA zone, or None to use server_utc_offset_hours (see `mt5bot check`).
    server_timezone: str | None = None
    server_utc_offset_hours: float | None = Field(default=None, ge=-14, le=14)
    timeout_ms: int = Field(default=60_000, ge=1_000, le=600_000)
    connect_retries: int = Field(default=3, ge=0, le=10)


class InstrumentSettings(_Strict):
    symbols: list[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])
    timeframes: list[Timeframe] = Field(
        default_factory=lambda: [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1]
    )

    @field_validator("symbols")
    @classmethod
    def _check_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one symbol is required")
        for s in v:
            if not _SYMBOL_RE.match(s):
                raise ValueError(f"symbol {s!r} must be a 6-letter base name such as EURUSD")
        if len(set(v)) != len(v):
            raise ValueError("duplicate symbols")
        return v


class CollectionSettings(_Strict):
    max_bars_per_request: int = Field(default=20_000, ge=100, le=100_000)
    request_pause_seconds: float = Field(default=0.2, ge=0, le=10)
    default_history_days: int = Field(default=730, ge=1, le=3650)
    tick_sample_minutes: int = Field(default=0, ge=0, le=1440)


class StorageSettings(_Strict):
    db_path: Path = Path("data/mt5bot.db")
    model_dir: Path = Path("models")
    report_dir: Path = Path("reports")
    log_dir: Path = Path("logs")


class FeatureSettings(_Strict):
    sessions_utc: dict[str, tuple[int, int]] = Field(
        default_factory=lambda: {"asia": (0, 8), "london": (7, 16), "new_york": (12, 21)}
    )
    vol_percentile_window: int = Field(default=500, ge=50, le=20_000)

    @field_validator("sessions_utc")
    @classmethod
    def _check_sessions(cls, v: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
        for name, (start, end) in v.items():
            if not (0 <= start <= 23 and 1 <= end <= 24 and start < end):
                raise ValueError(f"session {name!r} must satisfy 0 <= start < end <= 24")
        return v


class CostSettings(_Strict):
    commission_per_lot_per_side_usd: float = Field(default=0.0, ge=0, le=100)
    slippage_points: dict[str, float] = Field(default_factory=lambda: {"*": 0.0})
    swap_long_points_per_night: dict[str, float] = Field(default_factory=lambda: {"*": 0.0})
    swap_short_points_per_night: dict[str, float] = Field(default_factory=lambda: {"*": 0.0})

    def for_symbol(self, table: dict[str, float], symbol: str) -> float:
        return table.get(symbol, table.get("*", 0.0))


StrategyName = Literal["trend", "breakout", "mean_reversion"]


def _default_strategies() -> list[StrategyName]:
    return ["trend", "breakout"]


class StrategySettings(_Strict):
    enabled: list[StrategyName] = Field(default_factory=_default_strategies)
    max_hold_minutes: int = Field(default=60, ge=1, le=1440)


class MLSettings(_Strict):
    random_seed: int = 42
    model_type: Literal["logreg", "random_forest", "lightgbm", "xgboost", "catboost"] = "lightgbm"
    min_train_samples: int = Field(default=200, ge=20)
    probability_threshold: float = Field(default=0.55, gt=0.5, lt=1.0)
    conformal_confidence: float = Field(default=0.8, gt=0.5, lt=1.0)


class EvaluationSettings(_Strict):
    n_folds: int = Field(default=5, ge=2, le=50)
    embargo_bars: int = Field(default=60, ge=0)
    test_fraction: float = Field(default=0.2, gt=0, lt=0.5)
    validation_fraction: float = Field(default=0.2, gt=0, lt=0.5)
    min_trades_for_promotion: int = Field(default=100, ge=1)
    max_drawdown_r_for_promotion: float = Field(default=20.0, gt=0)
    max_brier_for_promotion: float = Field(default=0.25, gt=0, le=1)
    cost_multipliers: list[float] = Field(default_factory=lambda: [0.5, 1.0, 1.5, 2.0, 3.0])
    promotion_cost_multiplier: float = Field(default=1.5, ge=1.0)
    min_positive_fold_share: float = Field(default=0.6, ge=0, le=1)
    min_deflated_sharpe_prob: float = Field(default=0.5, ge=0, lt=1)


class SelectionSettings(_Strict):
    min_samples: int = Field(default=30, ge=1)
    lcb_z: float = Field(default=1.0, ge=0, le=5)
    reward_clip_low: float = -1.5
    reward_clip_high: float = 3.0
    mae_penalty: float = Field(default=0.25, ge=0)


class RiskSettings(_Strict):
    reference_balance_usd: float = Field(default=1_000.0, gt=0)
    risk_per_trade_pct: float = Field(default=0.5, gt=0, le=5)
    max_risk_per_trade_usd: float = Field(default=10.0, gt=0)
    max_daily_loss_pct: float = Field(default=2.0, gt=0, le=50)
    max_drawdown_pct: float = Field(default=10.0, gt=0, le=90)
    max_consecutive_losses: int = Field(default=5, ge=1)
    max_open_positions: int = Field(default=3, ge=1)
    max_usd_leg_exposure: int = Field(default=2, ge=1)
    max_spread_atr_ratio: float = Field(default=0.35, gt=0)
    max_spread_points: dict[str, float] = Field(default_factory=lambda: {"*": 50.0, "XAUUSD": 60.0})
    min_reward_risk: float = Field(default=1.0, gt=0)
    max_data_age_seconds: int = Field(default=180, ge=1)
    max_model_uncertainty: float = Field(default=0.5, gt=0, le=1)
    vol_percentile_min: float = Field(default=0.05, ge=0, lt=1)
    vol_percentile_max: float = Field(default=0.98, gt=0, le=1)
    event_window_before_minutes: int = Field(default=30, ge=0)
    event_window_after_minutes: int = Field(default=15, ge=0)
    # Conservative default: unknown news state (stale/failed feeds) blocks new recommendations.
    block_on_stale_news: bool = True
    emergency_disable: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> RiskSettings:
        if self.vol_percentile_min >= self.vol_percentile_max:
            raise ValueError("vol_percentile_min must be below vol_percentile_max")
        return self


class ProviderConfig(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    type: Literal["rss"] = "rss"
    url: str = Field(pattern=r"^https://")
    currencies: list[str] = Field(default_factory=list)
    category: Literal["central_bank", "statistics", "news"] = "news"
    enabled: bool = True
    min_interval_seconds: int = Field(default=300, ge=30)
    store_summary: bool = False


class NewsSettings(_Strict):
    enabled: bool = True
    providers: list[ProviderConfig] = Field(default_factory=list)
    stale_after_minutes: int = Field(default=180, ge=1)
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=5)
    calendar_csv: Path | None = None
    finnhub_api_key: SecretStr | None = None
    newsapi_api_key: SecretStr | None = None


class LLMSettings(_Strict):
    enabled: bool = False
    provider: Literal["none", "anthropic"] = "none"
    model: str = "claude-opus-5-5"
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    daily_cost_limit_usd: float = Field(default=1.0, ge=0)
    cache_ttl_hours: int = Field(default=24, ge=0)
    # USD per million tokens for the cost cap; update if you change `model` and check current pricing.
    input_usd_per_mtok: float = Field(default=4.0, ge=0)
    output_usd_per_mtok: float = Field(default=20.0, ge=0)
    max_output_tokens: int = Field(default=4000, ge=256, le=16000)


class LoggingSettings(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_format: bool = True


class MonitorSettings(_Strict):
    interval_seconds: int = Field(default=60, ge=5, le=3600)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MT5BOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        env_ignore_empty=True,
    )

    mt5: MT5Settings = Field(default_factory=MT5Settings)
    instruments: InstrumentSettings = Field(default_factory=InstrumentSettings)
    collection: CollectionSettings = Field(default_factory=CollectionSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    costs: CostSettings = Field(default_factory=CostSettings)
    strategy: StrategySettings = Field(default_factory=StrategySettings)
    ml: MLSettings = Field(default_factory=MLSettings)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    selection: SelectionSettings = Field(default_factory=SelectionSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_path = _resolve_toml_path()
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=toml_path),
        )


def _resolve_toml_path() -> Path:
    override = os.environ.get("MT5BOT_CONFIG_FILE")
    if override:
        return Path(override)
    local = Path("config/local.toml")
    if local.exists():
        return local
    with resources.as_file(resources.files("mt5bot.config") / "default.toml") as p:
        return Path(p)


def load_settings(**overrides: Any) -> Settings:
    """Load and validate settings. Raises ``ConfigError`` with a redacted message."""
    from pydantic import ValidationError

    from mt5bot.core.errors import ConfigError

    try:
        return Settings(**overrides)
    except ValidationError as exc:
        # Only field locations and messages; never echo input values (they may be secrets).
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"invalid configuration: {problems}") from None
