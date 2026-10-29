"""Generate the configuration reference from the settings models (the validation code is the source)."""

from __future__ import annotations

from typing import Any, get_args, get_origin

from pydantic import BaseModel, SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from mt5bot.config.settings import Settings

PURPOSE: dict[str, str] = {
    "mt5.terminal_path": "Path to terminal64.exe; empty attaches to the running terminal",
    "mt5.login": "Demo account login (numeric). Optional if the terminal is already logged in",
    "mt5.password": "Demo account password",
    "mt5.server": "Broker server name for the demo account",
    "mt5.require_demo": "Demo-only guard. Only `true` is accepted",
    "mt5.expected_currency": "Account currency the account guard requires",
    "mt5.symbol_map": "Explicit canonical -> broker symbol names, used when resolution is ambiguous",
    "mt5.server_timezone": "How broker server time maps to UTC: 'NY+7', an IANA zone, or empty",
    "mt5.server_utc_offset_hours": "Fixed server UTC offset (no DST) if server_timezone is empty",
    "mt5.timeout_ms": "Terminal connection timeout",
    "mt5.connect_retries": "Extra connection attempts with backoff",
    "instruments.symbols": "Canonical instruments to collect and research",
    "instruments.timeframes": "Timeframes to collect and research",
    "collection.max_bars_per_request": "Bars requested per copy_rates_range call",
    "collection.request_pause_seconds": "Minimum pause between terminal history requests",
    "collection.default_history_days": "History depth for a first collection without --from",
    "collection.tick_sample_minutes": "Reserved for tick sampling (0 = off)",
    "storage.db_path": "SQLite database file",
    "storage.model_dir": "Model artifact directory",
    "storage.report_dir": "Report output directory",
    "storage.log_dir": "Log directory",
    "features.sessions_utc": "Trading sessions as [start_hour, end_hour) in UTC",
    "features.vol_percentile_window": "Trailing window for the volatility percentile feature",
    "costs.commission_per_lot_per_side_usd": "Commission per lot per side (depends on account type)",
    "costs.slippage_points": "Assumed adverse slippage per fill, by symbol ('*' = default)",
    "costs.swap_long_points_per_night": "Swap for longs in points per rollover (positive = credit)",
    "costs.swap_short_points_per_night": "Swap for shorts in points per rollover (positive = credit)",
    "strategy.enabled": "Strategy specialists that may produce candidates",
    "strategy.max_hold_minutes": "Maximum holding time for every candidate",
    "ml.random_seed": "Seed for every model and search",
    "ml.model_type": "Default challenger model family",
    "ml.min_train_samples": "Minimum labelled candidates required to train",
    "ml.probability_threshold": "Minimum calibrated success probability to select a candidate",
    "ml.conformal_confidence": "Confidence level of the conformal abstention sets",
    "evaluation.n_folds": "Walk-forward folds",
    "evaluation.embargo_bars": "Extra embargo bars between training and evaluation windows",
    "evaluation.test_fraction": "Share of data held out as the untouched test window",
    "evaluation.validation_fraction": "Share of data used for validation",
    "evaluation.min_trades_for_promotion": "Promotion gate: minimum selected trades on test",
    "evaluation.max_drawdown_r_for_promotion": "Promotion gate: maximum drawdown in R on test",
    "evaluation.max_brier_for_promotion": "Promotion gate: maximum Brier score on test",
    "evaluation.cost_multipliers": "Cost multipliers for backtest sensitivity runs",
    "evaluation.promotion_cost_multiplier": "Promotion gate: expectancy must stay positive at this cost multiple",
    "evaluation.min_positive_fold_share": "Promotion gate: share of walk-forward folds with positive expectancy",
    "evaluation.min_deflated_sharpe_prob": "Promotion gate: minimum deflated-Sharpe probability",
    "selection.mode": "rules (default): rule-eligible candidates go to the risk engine; evidence; or ml (champion)",
    "selection.rules_memory_veto": "In rules mode, let performance memory veto a strategy proven negative after costs",
    "selection.min_samples": "Evidence needed before performance memory can qualify or veto a candidate",
    "selection.lcb_z": "z-score of the lower confidence bound on mean reward",
    "selection.reward_clip_low": "Lower clip of net R in the reward",
    "selection.reward_clip_high": "Upper clip of net R in the reward",
    "selection.mae_penalty": "Penalty per R of adverse excursion beyond 1R",
    "risk.reference_balance_usd": "Configured reference balance for sizing - NOT a verified account balance",
    "risk.risk_per_trade_pct": "Risk budget per candidate as % of the reference balance",
    "risk.max_risk_per_trade_usd": "Absolute cap on risk per candidate",
    "risk.max_daily_loss_pct": "Simulated daily loss limit (% of reference balance)",
    "risk.max_drawdown_pct": "Simulated drawdown limit (% of reference balance)",
    "risk.max_consecutive_losses": "Consecutive simulated losses before rejection",
    "risk.max_open_positions": "Maximum concurrent simulated positions",
    "risk.max_usd_leg_exposure": "Maximum open positions with the same USD direction",
    "risk.max_spread_atr_ratio": "Maximum spread / ATR",
    "risk.max_spread_points": "Maximum spread in points by symbol ('*' = default)",
    "risk.min_reward_risk": "Minimum reward/risk after estimated costs",
    "risk.max_data_age_seconds": "Maximum age of the last closed bar in observe mode",
    "risk.max_model_uncertainty": "Maximum model uncertainty (1 - probability)",
    "risk.vol_percentile_min": "Lower volatility-percentile bound",
    "risk.vol_percentile_max": "Upper volatility-percentile bound",
    "risk.event_window_before_minutes": "Block window before a high-impact event",
    "risk.event_window_after_minutes": "Block window after a high-impact event / breaking news",
    "risk.block_on_stale_news": "Reject when news state is unknown (stale/failed/disabled feeds)",
    "risk.emergency_disable": "Global emergency disable: rejects every candidate",
    "news.enabled": "Master switch for news collection",
    "news.providers": "RSS providers: id, url, currencies, category, enabled, min_interval_seconds, store_summary",
    "news.stale_after_minutes": "A provider without success for this long makes news state unknown",
    "news.request_timeout_seconds": "HTTP timeout per request",
    "news.max_retries": "Retries for network errors and HTTP 5xx",
    "news.calendar_csv": "Optional local economic-calendar CSV you are permitted to use",
    "news.finnhub_api_key": "Reserved for an optional licensed provider (not implemented yet)",
    "news.newsapi_api_key": "Reserved for an optional licensed provider (not implemented yet)",
    "llm.enabled": "Enable the optional research adapter",
    "llm.provider": "'none' or 'anthropic'",
    "llm.model": "Model identifier used by the adapter",
    "llm.api_key": "API key (or use ANTHROPIC_API_KEY)",
    "llm.timeout_seconds": "Request timeout",
    "llm.max_retries": "SDK retries",
    "llm.daily_cost_limit_usd": "Daily spending cap enforced before each request",
    "llm.cache_ttl_hours": "Reuse identical responses for this long",
    "llm.input_usd_per_mtok": "Input price used for the cost cap",
    "llm.output_usd_per_mtok": "Output price used for the cost cap",
    "llm.max_output_tokens": "max_tokens per request",
    "logging.level": "Log level",
    "logging.json_format": "JSON log lines (true) or plain text",
    "monitor.interval_seconds": "Seconds between observe-only monitoring cycles",
    "bridge.enabled": "Exchange decisions, levels and tick results with the MQL5 indicator and observer EA",
    "bridge.directory": "Exchange folder; empty uses MT5's Common\\Files\\mt5bot folder",
    "bridge.max_decisions": "Most recent decisions exported per symbol and timeframe for the chart",
    "bridge.import_ticks": "Import tick files recorded by the observer EA into the quotes table",
}


def _type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation)).replace("NoneType", "None")
    args = ", ".join(_type_name(a) for a in get_args(annotation))
    name = getattr(origin, "__name__", str(origin)).replace("UnionType", "Union")
    if name in ("Union", "Optional"):
        return " | ".join(_type_name(a) for a in get_args(annotation))
    if name == "Literal":
        return " | ".join(repr(a) for a in get_args(annotation))
    return f"{name}[{args}]"


def _rules(info: FieldInfo) -> str:
    out = []
    for m in info.metadata:
        for attr, sym in (("ge", ">="), ("gt", ">"), ("le", "<="), ("lt", "<"), ("pattern", "matches")):
            v = getattr(m, attr, None)
            if v is not None:
                out.append(f"{sym} {v}")
    return ", ".join(out)


def _plain(v: Any) -> Any:
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return getattr(v, "value", v)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _default(info: FieldInfo) -> str:
    if info.default_factory is not None:
        try:
            return _cell(f"`{_plain(info.default_factory())}`")  # type: ignore[call-arg]
        except TypeError:
            return "(computed)"
    if info.default is PydanticUndefined:
        return "required"
    return _cell(f"`{_plain(info.default)}`")


def config_markdown() -> str:
    lines = [
        "# Configuration reference",
        "",
        "Generated from `mt5bot.config.settings` by `mt5bot config docs`; the validation code is the source of truth.",
        "",
        "Sources, highest priority first: environment variables `MT5BOT_<SECTION>__<KEY>`, then `.env`, then "
        "`config/local.toml` (or the file in `MT5BOT_CONFIG_FILE`), then the packaged defaults. Unknown keys are "
        "rejected. All settings are read at start-up, so **every change requires restarting the command**.",
        "",
        "| Key | Purpose | Type | Default | Validation | Secret |",
        "|---|---|---|---|---|---|",
    ]
    for section, sinfo in Settings.model_fields.items():
        model = sinfo.annotation
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        for name, info in model.model_fields.items():
            key = f"{section}.{name}"
            secret = "SecretStr" in _type_name(info.annotation) or info.annotation is SecretStr
            lines.append(
                f"| `{key}` | {PURPOSE.get(key, '-')} | {_cell('`' + _type_name(info.annotation) + '`')} | "
                f"{'(none)' if secret else _default(info)} | {_rules(info) or '-'} | {'**yes**' if secret else 'no'} |"
            )
    return "\n".join(lines) + "\n"
