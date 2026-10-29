"""Export research state to the MQL5 components and import their tick-level results."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from mt5bot.bridge.files import (
    bridge_dir,
    decision_lines,
    import_resolutions,
    import_tick_file,
    load_decisions,
    status_lines,
    watch_lines,
    write_atomic,
)
from mt5bot.config.settings import Settings
from mt5bot.gateway.clock import ServerClock
from mt5bot.pipeline.context import SpecProvider
from mt5bot.storage.repositories import DriftRepository, InstrumentRepository, ModelRepository, NewsRepository


@dataclass
class BridgeReport:
    directory: Path | None
    files_written: list[str] = field(default_factory=list)
    resolutions_imported: int = 0
    ticks_imported: int = 0
    heartbeats: dict[str, str] = field(default_factory=dict)
    note: str = ""


def broker_name(conn: sqlite3.Connection, symbol: str) -> str:
    row = InstrumentRepository(conn).get(symbol)
    return row["broker_symbol"] if row else symbol


def export_state(
    conn: sqlite3.Connection, settings: Settings, clock: ServerClock, now: pd.Timestamp, mode: str = "OBSERVED_DEMO"
) -> BridgeReport:
    d = bridge_dir(settings.bridge)
    rep = BridgeReport(d)
    if not settings.bridge.enabled or d is None:
        rep.note = "bridge disabled or exchange folder unknown (set bridge.directory)"
        return rep
    specs = SpecProvider(conn)
    for sym in settings.instruments.symbols:
        broker = broker_name(conn, sym)
        spec = specs(sym)
        slip = settings.costs.for_symbol(settings.costs.slippage_points, sym)
        watch: list[str] = []
        for tf in settings.instruments.timeframes:
            rows = load_decisions(conn, sym, tf, settings.bridge.max_decisions)
            path = d / f"decisions_{broker}_{tf.value}.csv"
            write_atomic(path, decision_lines(rows, tf, clock, spec.digits))
            rep.files_written.append(path.name)
            lines = watch_lines(rows, tf, clock, now, spec.point, slip, spec.digits)
            watch += lines[1:] if watch else lines
        path = d / f"watch_{broker}.csv"
        write_atomic(path, watch or ["id,direction,start_server,stop_dist,target_dist,expiry_server,slip_points"])
        rep.files_written.append(path.name)
    champ = ModelRepository(conn).champion("meta_label")
    providers = NewsRepository(conn).provider_status()
    news_state = "UNKNOWN" if not providers or any(p["consecutive_failures"] for p in providers) else "OK"
    status = {
        "updated_utc": now.isoformat(),
        "updated_server": str(clock.utc_to_server_epoch(now.to_pydatetime())),
        "mode": mode,
        "champion": champ["id"] if champ else "none (evidence-only baseline)",
        "news": news_state,
        "open_alerts": str(len(DriftRepository(conn).open_alerts())),
        "execution": "DISABLED (research and observation only)",
    }
    write_atomic(d / "status.txt", status_lines(status))
    rep.files_written.append("status.txt")
    return rep


def import_results(
    conn: sqlite3.Connection, settings: Settings, clock: ServerClock, max_tick_file_age_hours: float = 48.0
) -> BridgeReport:
    d = bridge_dir(settings.bridge)
    rep = BridgeReport(d)
    if not settings.bridge.enabled or d is None or not d.exists():
        rep.note = "exchange folder not found; is the observer EA running?"
        return rep
    for sym in settings.instruments.symbols:
        broker = broker_name(conn, sym)
        rep.resolutions_imported += import_resolutions(conn, d / f"resolved_{broker}.csv", clock)
        hb = d / f"heartbeat_{broker}.txt"
        if hb.exists():
            rep.heartbeats[broker] = hb.read_text(encoding="ascii", errors="replace").strip().replace("\r\n", "; ")
        if settings.bridge.import_ticks:
            cutoff = time.time() - max_tick_file_age_hours * 3600
            for f in sorted(d.glob(f"ticks_{broker}_*.csv")):
                if f.stat().st_mtime >= cutoff:
                    rep.ticks_imported += import_tick_file(conn, sym, f, clock)
    return rep


def sync(
    conn: sqlite3.Connection, settings: Settings, clock: ServerClock, now: pd.Timestamp, mode: str = "OBSERVED_DEMO"
) -> BridgeReport:
    imported = import_results(conn, settings, clock)
    exported = export_state(conn, settings, clock, now, mode)
    exported.resolutions_imported, exported.ticks_imported = imported.resolutions_imported, imported.ticks_imported
    exported.heartbeats = imported.heartbeats
    return exported
