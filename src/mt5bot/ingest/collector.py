"""Incremental, checkpointed, idempotent historical collection."""

from __future__ import annotations

import functools
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from mt5bot.config.settings import Settings
from mt5bot.core.errors import GatewayError, Mt5BotError
from mt5bot.core.logging import log_event
from mt5bot.core.types import Timeframe, ensure_utc, utc_now
from mt5bot.gateway.base import MarketGateway, SymbolMeta
from mt5bot.gateway.retry import RateLimiter, retry
from mt5bot.gateway.symbols import resolve_symbol
from mt5bot.ingest.quality import validate_bars
from mt5bot.storage.repositories import BarRepository, IngestionRepository, InstrumentRepository

log = logging.getLogger(__name__)


@dataclass
class CollectionReport:
    symbol: str
    timeframe: Timeframe
    broker_symbol: str | None
    status: str
    received: int = 0
    inserted: int = 0
    rejected: int = 0
    forming_dropped: int = 0
    issues: int = 0
    error: str | None = None
    runs: list[int] = field(default_factory=list)


class Collector:
    def __init__(
        self,
        gateway: MarketGateway,
        conn: sqlite3.Connection,
        settings: Settings,
        *,
        source: str = "mt5",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gw = gateway
        self.bars = BarRepository(conn)
        self.runs = IngestionRepository(conn)
        self.instruments = InstrumentRepository(conn)
        self.settings = settings
        self.source = source
        self._limiter = RateLimiter(settings.collection.request_pause_seconds, sleep=sleep)
        self._sleep = sleep

    def resolve(self, symbols: list[str]) -> dict[str, SymbolMeta]:
        available = self.gw.symbols()
        resolved = {s: resolve_symbol(s, available, self.settings.mt5.symbol_map) for s in symbols}
        for canonical, meta in resolved.items():
            self.instruments.upsert(meta.to_spec(canonical), meta.name, self.source)
        return resolved

    def collect(
        self,
        symbols: list[str],
        timeframes: list[Timeframe],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CollectionReport]:
        end_utc = ensure_utc(end) if end else utc_now()
        reports: list[CollectionReport] = []
        try:
            resolved = self.resolve(symbols)
        except Mt5BotError as exc:
            return [
                CollectionReport(s, tf, None, "FAILED", error=f"{exc.code}: {exc}")
                for s in symbols
                for tf in timeframes
            ]
        for sym in symbols:
            for tf in timeframes:
                reports.append(self._collect_one(sym, resolved[sym].name, tf, start, end_utc))
        return reports

    def _collect_one(
        self, symbol: str, broker: str, tf: Timeframe, start: datetime | None, end_utc: datetime
    ) -> CollectionReport:
        rep = CollectionReport(symbol, tf, broker, "OK")
        last = self.bars.latest_time(symbol, tf)
        if last is not None:
            from_ts = last + tf.delta  # resume after the last stored bar
        elif start is not None:
            from_ts = pd.Timestamp(ensure_utc(start))
        else:
            from_ts = pd.Timestamp(end_utc - timedelta(days=self.settings.collection.default_history_days))
        chunk = tf.delta * self.settings.collection.max_bars_per_request
        cursor = from_ts
        now = pd.Timestamp(end_utc)
        prev_last = last
        while cursor < now:
            chunk_end = min(cursor + chunk, now)
            run_id = self.runs.start_run(
                source=self.source,
                symbol=symbol,
                broker_symbol=broker,
                timeframe=tf,
                start=cursor.to_pydatetime(),
                end=chunk_end.to_pydatetime(),
            )
            rep.runs.append(run_id)
            try:
                self._limiter.wait()
                raw = retry(
                    functools.partial(self.gw.bars, broker, tf, cursor.to_pydatetime(), chunk_end.to_pydatetime()),
                    attempts=3,
                    retry_on=(GatewayError,),
                    sleep=self._sleep,
                )
                q = validate_bars(raw, symbol, tf, now, previous_last=prev_last)
                inserted = self.bars.insert_bars(
                    symbol, tf, q.clean, source=self.source, broker_symbol=broker, run_id=run_id
                )
                self.runs.record_issues(q.issues, run_id)
                if not q.clean.empty:
                    last_bar = q.clean["time_utc"].iloc[-1]
                    self.runs.set_checkpoint(symbol, tf, last_bar)
                    prev_last = last_bar
                status = "PARTIAL" if q.rejected else "OK"
                self.runs.finish_run(run_id, status=status, received=len(raw), inserted=inserted, rejected=q.rejected)
                rep.received += len(raw)
                rep.inserted += inserted
                rep.rejected += q.rejected
                rep.forming_dropped += q.forming_dropped
                rep.issues += len(q.issues)
                if status == "PARTIAL":
                    rep.status = "PARTIAL"
            except Mt5BotError as exc:
                self.runs.finish_run(
                    run_id,
                    status="FAILED",
                    received=0,
                    inserted=0,
                    rejected=0,
                    error_code=exc.code,
                    error_message=str(exc)[:500],
                )
                rep.status, rep.error = "FAILED", f"{exc.code}: {exc}"
                log_event(
                    log, logging.ERROR, "collection chunk failed", symbol=symbol, timeframe=tf.value, code=exc.code
                )
                break  # stop this series; the checkpoint lets the next run resume here
            cursor = chunk_end
        log_event(
            log,
            logging.INFO,
            "collection finished",
            symbol=symbol,
            timeframe=tf.value,
            status=rep.status,
            inserted=rep.inserted,
            rejected=rep.rejected,
        )
        return rep
