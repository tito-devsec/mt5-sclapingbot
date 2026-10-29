"""Observe-only demo monitoring loop; it never submits, modifies, closes or cancels orders."""

from __future__ import annotations

import logging
import signal
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from mt5bot.bridge.service import sync
from mt5bot.config.settings import Settings
from mt5bot.core.errors import AccountValidationError, Mt5BotError
from mt5bot.core.logging import log_event
from mt5bot.core.types import Timeframe, utc_now
from mt5bot.drift.monitor import check_feed, persist, resolve
from mt5bot.gateway.account import require_valid_account
from mt5bot.gateway.base import MarketGateway
from mt5bot.gateway.clock import ServerClock
from mt5bot.ingest.collector import Collector
from mt5bot.ingest.quality import is_weekend_gap
from mt5bot.memory.outcomes import evaluate_pending
from mt5bot.news.service import build_registry, refresh
from mt5bot.pipeline.context import cost_model, sizing
from mt5bot.pipeline.inference import ResearchPipeline
from mt5bot.storage.repositories import BarRepository

log = logging.getLogger(__name__)


@dataclass
class MonitorStats:
    cycles: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    stopped_reason: str = ""
    bridge_files: int = 0
    tick_resolutions: int = 0


class DemoMonitor:
    def __init__(
        self,
        gateway: MarketGateway,
        conn: sqlite3.Connection,
        settings: Settings,
        sleep: Callable[[float], None] | None = None,
        clock: ServerClock | None = None,
    ) -> None:
        self.gw = gateway
        self.clock = clock
        self.conn = conn
        self.s = settings
        self.pipeline = ResearchPipeline(conn, settings)
        self.collector = Collector(gateway, conn, settings)
        self.news_registry = build_registry(settings.news)
        self._stop = threading.Event()
        self._sleep = sleep or (lambda secs: self._stop.wait(secs))

    def stop(self, *_: object) -> None:
        self._stop.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self.stop)

    def run(self, max_cycles: int | None = None) -> MonitorStats:
        stats = MonitorStats()
        symbols, tfs = list(self.s.instruments.symbols), list(self.s.instruments.timeframes)
        while not self._stop.is_set():
            try:
                account = require_valid_account(self.gw.account(), self.s.mt5.expected_currency)
            except (AccountValidationError, Mt5BotError) as exc:
                stats.stopped_reason = f"{exc.code}: account validation failed - monitoring stopped"
                log_event(log, logging.ERROR, "monitor stopped", code=exc.code)
                break
            now = utc_now()
            try:
                self.collector.collect(symbols, tfs, end=now)
                if self.news_registry.ids:
                    refresh(self.conn, self.news_registry, now)
                for sym in symbols:
                    for tf in tfs:
                        self._feed_check(sym, tf, pd.Timestamp(now))
                        rec = self.pipeline.recommend(sym, tf, mode="OBSERVED_DEMO", now=now, account=account)
                        if rec is not None and rec.id is not None:
                            stats.decisions[rec.status.value] = stats.decisions.get(rec.status.value, 0) + 1
                    evaluate_pending(
                        self.conn,
                        spec_for=self.pipeline.specs,
                        costs_for=lambda x: cost_model(self.s, x),
                        sizing=sizing(self.s),
                        selection=self.s.selection,
                        now=pd.Timestamp(now),
                        feature_cfg=self.pipeline.fcfg,
                    )
            except Mt5BotError as exc:
                stats.errors += 1
                log_event(log, logging.ERROR, "monitor cycle failed", code=exc.code)
            if self.clock is not None and self.s.bridge.enabled:
                try:
                    rep = sync(self.conn, self.s, self.clock, pd.Timestamp(now))
                    stats.bridge_files = len(rep.files_written)
                    stats.tick_resolutions += rep.resolutions_imported
                except OSError as exc:  # a locked or missing exchange folder must not stop monitoring
                    stats.errors += 1
                    log_event(log, logging.WARNING, "bridge sync failed", error=type(exc).__name__)
            stats.cycles += 1
            if max_cycles is not None and stats.cycles >= max_cycles:
                stats.stopped_reason = "max cycles reached"
                break
            self._sleep(self.s.monitor.interval_seconds)
        if not stats.stopped_reason:
            stats.stopped_reason = "stop requested"
        return stats

    def _feed_check(self, symbol: str, tf: Timeframe, now: pd.Timestamp) -> None:
        last = BarRepository(self.conn).latest_time(symbol, tf)
        market_open = last is None or not is_weekend_gap(last, now)
        scope = f"{symbol}:{tf.value}"
        alerts = check_feed(last, now, tf.minutes, scope, market_open=market_open)
        if alerts:
            persist(self.conn, alerts)
        else:
            resolve(self.conn, "FEED_FAILURE", scope)


def sleep_seconds(s: float) -> None:  # pragma: no cover - trivial
    time.sleep(s)
