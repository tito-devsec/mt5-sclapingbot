"""File exchange with the MQL5 indicator and observer EA through MT5's Common\\Files folder."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from mt5bot.config.settings import BridgeSettings
from mt5bot.core.types import Timeframe
from mt5bot.gateway.clock import ServerClock
from mt5bot.storage.db import transaction
from mt5bot.storage.repositories import QuoteRepository, iso, now_iso

FORMAT_VERSION = "1"
_UNSAFE = re.compile(r"[^A-Za-z0-9 _.:;/()+\-=<>%|]")
DECISION_HEADER = (
    "id,bar_server,bar_utc,status,regime,strategy,direction,entry_ref,stop,target,hold_bars,tf_minutes,rejections,"
    "rationale"
)
WATCH_HEADER = "id,direction,start_server,stop_dist,target_dist,expiry_server,slip_points"
RESOLVED_HEADER = "id,entry_msc,entry_price,first_hit,hit_msc,hit_price,ticks"


def bridge_dir(cfg: BridgeSettings) -> Path | None:
    """The exchange folder, or None when it cannot be determined (non-Windows without an explicit directory)."""
    if cfg.directory is not None:
        return Path(cfg.directory)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "mt5bot"
    return None


def safe_text(text: str | None, limit: int = 200) -> str:
    """ASCII-only, comma-free text so MQL5 can split lines on commas."""
    return _UNSAFE.sub(" ", (text or "").replace(",", ";"))[:limit].strip()


def write_atomic(path: Path, lines: Iterable[str], attempts: int = 5) -> None:
    """Write via a temp file and rename, retrying while MT5 briefly holds the target open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", errors="replace")
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


@dataclass
class DecisionRow:
    id: int
    bar_utc: pd.Timestamp
    status: str
    regime: str
    strategy: str | None
    direction: str | None
    entry_ref: float
    stop_distance: float | None
    target_distance: float | None
    hold_bars: int | None
    rejections: list[str]
    rationale: str


def _levels(r: DecisionRow) -> tuple[float | None, float | None]:
    if r.direction is None or r.stop_distance is None or r.target_distance is None:
        return None, None
    sgn = 1 if r.direction == "LONG" else -1
    return r.entry_ref - sgn * r.stop_distance, r.entry_ref + sgn * r.target_distance


def decision_lines(rows: list[DecisionRow], tf: Timeframe, clock: ServerClock, digits: int) -> list[str]:
    out = [DECISION_HEADER]
    for r in rows:
        stop, target = _levels(r)
        out.append(
            ",".join(
                [
                    str(r.id),
                    str(clock.utc_to_server_epoch(r.bar_utc.to_pydatetime())),
                    str(int(r.bar_utc.timestamp())),
                    r.status,
                    r.regime,
                    r.strategy or "",
                    r.direction or "",
                    f"{r.entry_ref:.{digits}f}",
                    "" if stop is None else f"{stop:.{digits}f}",
                    "" if target is None else f"{target:.{digits}f}",
                    "" if r.hold_bars is None else str(r.hold_bars),
                    str(tf.minutes),
                    safe_text("|".join(r.rejections), 300),
                    safe_text(r.rationale),
                ]
            )
        )
    return out


def load_decisions(conn: sqlite3.Connection, symbol: str, tf: Timeframe, limit: int) -> list[DecisionRow]:
    rows = conn.execute(
        "SELECT * FROM decisions WHERE symbol = ? AND timeframe = ? ORDER BY bar_time_utc DESC LIMIT ?",
        (symbol, tf.value, limit),
    ).fetchall()
    out = []
    for d in reversed(rows):
        rej = [
            r["check_code"]
            for r in conn.execute(
                "SELECT check_code FROM risk_evaluations WHERE decision_id = ? AND passed = 0", (d["id"],)
            ).fetchall()
        ]
        out.append(
            DecisionRow(
                int(d["id"]),
                pd.Timestamp(d["bar_time_utc"]),
                d["status"],
                d["regime"],
                d["selected_strategy"],
                d["direction"],
                float(d["entry_ref"] or 0.0),
                d["stop_distance"],
                d["target_distance"],
                d["max_hold_bars"],
                rej,
                d["rationale"] or "",
            )
        )
    return out


def watch_lines(
    rows: list[DecisionRow],
    tf: Timeframe,
    clock: ServerClock,
    now: pd.Timestamp,
    point: float,
    slippage_points: float,
    digits: int,
) -> list[str]:
    """Selected candidates whose holding window has not ended, for live tick-level stop/target tracking."""
    out = [WATCH_HEADER]
    for r in rows:
        if r.direction is None or r.stop_distance is None or r.target_distance is None or r.hold_bars is None:
            continue
        start = r.bar_utc + tf.delta  # entry happens at the next bar's open
        expiry = start + tf.delta * int(r.hold_bars)
        if expiry <= now:
            continue
        out.append(
            ",".join(
                [
                    str(r.id),
                    r.direction,
                    str(clock.utc_to_server_epoch(start.to_pydatetime())),
                    f"{r.stop_distance:.{digits + 1}f}",
                    f"{r.target_distance:.{digits + 1}f}",
                    str(clock.utc_to_server_epoch(expiry.to_pydatetime())),
                    f"{slippage_points:g}",
                ]
            )
        )
    return out


def status_lines(values: dict[str, str]) -> list[str]:
    return [f"format={FORMAT_VERSION}"] + [f"{k}={safe_text(str(v), 300)}" for k, v in values.items()]


def _server_msc_to_utc(clock: ServerClock, msc: int) -> pd.Timestamp:
    secs, ms = divmod(int(msc), 1000)
    return pd.Timestamp(clock.server_epoch_to_utc(pd.Series([secs]))[0]) + pd.Timedelta(milliseconds=ms)


def import_resolutions(conn: sqlite3.Connection, path: Path, clock: ServerClock) -> int:
    """Import the observer EA's resolved_<SYMBOL>.csv (id,entry_msc,entry_price,first_hit,hit_msc,hit_price,ticks)."""
    if not path.exists():
        return 0
    known = {r[0] for r in conn.execute("SELECT id FROM decisions").fetchall()}
    rows = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) != 7 or not parts[0].isdigit() or int(parts[0]) not in known:
            continue
        did, entry_msc, entry_px, first_hit, hit_msc, hit_px, ticks = parts
        if first_hit not in ("TP", "SL", "NONE", "LATE"):
            continue
        entry_t = iso(_server_msc_to_utc(clock, int(entry_msc))) if entry_msc.isdigit() and int(entry_msc) > 0 else None
        hit_t = iso(_server_msc_to_utc(clock, int(hit_msc))) if hit_msc.isdigit() and int(hit_msc) > 0 else None
        rows.append(
            (
                int(did),
                entry_t,
                float(entry_px or 0) or None,
                first_hit,
                hit_t,
                float(hit_px or 0) or None,
                int(ticks) if ticks.isdigit() else 0,
                now_iso(),
            )
        )
    if not rows:
        return 0
    with transaction(conn):
        before = conn.total_changes
        conn.executemany(
            "INSERT INTO tick_resolutions(decision_id, source, entry_time_utc, entry_price, first_hit, hit_time_utc, "
            "hit_price, ticks_seen, imported_at) VALUES (?, 'MT5_EA', ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(decision_id) DO NOTHING",
            rows,
        )
        return conn.total_changes - before


def import_tick_file(conn: sqlite3.Connection, symbol: str, path: Path, clock: ServerClock) -> int:
    """Import an observer EA tick file (time_msc,bid,ask in server time) into the quotes table."""
    import io

    text = path.read_text(encoding="ascii", errors="replace")
    if text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]  # the EA may be mid-write: drop a partial last line
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    if df.empty or not {"time_msc", "bid", "ask"} <= set(df.columns):
        return 0
    df = df[pd.to_numeric(df["time_msc"], errors="coerce").notna()].astype({"time_msc": "int64"})
    secs, ms = df["time_msc"] // 1000, df["time_msc"] % 1000
    utc = clock.server_epoch_to_utc(secs.to_numpy()) + pd.to_timedelta(ms.to_numpy(), unit="ms")
    out = pd.DataFrame({"time_utc": utc, "bid": df["bid"].astype(float), "ask": df["ask"].astype(float)}).dropna()
    return QuoteRepository(conn).insert(symbol, out, source="mt5_ea")
