"""Status and performance reports: CLI text, static HTML and CSV. No frontend framework."""

from __future__ import annotations

import csv
import html
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mt5bot.storage.repositories import (
    BacktestRepository,
    BarRepository,
    DecisionRepository,
    DriftRepository,
    IngestionRepository,
    ModelRepository,
    NewsRepository,
    PerformanceRepository,
)


def gather(conn: sqlite3.Connection) -> dict[str, Any]:
    models = ModelRepository(conn).list_all()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "data": [dict(r) for r in BarRepository(conn).count()],
        "ingestion": [dict(r) for r in IngestionRepository(conn).recent_runs(10)],
        "quality": [dict(r) for r in IngestionRepository(conn).issue_summary()],
        "news": [dict(r) for r in NewsRepository(conn).provider_status()],
        "models": [
            {k: r[k] for k in ("id", "kind", "model_type", "status", "train_start", "train_end", "created_at")}
            for r in models
        ],
        "drift": [dict(r) for r in DriftRepository(conn).open_alerts()],
        "decisions": [dict(r) for r in DecisionRepository(conn).summary()],
        "rejections": [dict(r) for r in DecisionRepository(conn).rejection_summary()],
        "backtests": [
            {
                **{k: r[k] for k in ("id", "symbol", "timeframe", "period_start", "period_end", "cost_multiplier")},
                **{
                    k: v
                    for k, v in json.loads(r["metrics_json"]).items()
                    if k in ("trades", "net_r", "expectancy_r", "win_rate", "max_drawdown_r", "profit_factor")
                },
            }
            for r in BacktestRepository(conn).recent(20)
        ],
        "performance": PerformanceRepository(conn)
        .all()
        .drop(columns=["m2_reward"], errors="ignore")
        .to_dict("records"),
    }


def _table(rows: list[dict[str, Any]], title: str, note: str = "") -> str:
    if not rows:
        return f"<h2>{html.escape(title)}</h2><p class='empty'>No records.</p>"
    cols = list(rows[0])
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(_fmt(r.get(c)))}</td>" for c in cols) + "</tr>" for r in rows)
    n = f"<p class='note'>{html.escape(note)}</p>" if note else ""
    return f"<h2>{html.escape(title)}</h2>{n}<div class='scroll'><table><tr>{head}</tr>{body}</table></div>"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return "" if v is None else str(v)


def write_html(data: dict[str, Any], path: Path) -> Path:
    css = (
        "body{font-family:system-ui,sans-serif;margin:16px;background:#fff;color:#111}"
        "table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #ccc;padding:4px 6px;text-align:left}"
        "th{background:#f3f3f3}.scroll{overflow-x:auto}.note{color:#555}.warn{background:#fff4e5;padding:8px;"
        "border:1px solid #f0b46c}@media(prefers-color-scheme:dark){body{background:#111;color:#eee}"
        "th{background:#222}td,th{border-color:#444}.warn{background:#3a2a10;border-color:#8a6420}}"
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>mt5bot report</title>"
        f"<style>{css}</style></head><body>",
        "<h1>mt5-sclapingbot research report</h1>",
        f"<p>Generated {html.escape(data['generated_at'])}</p>",
        "<p class='warn'>Research output only. Backtest and decision figures are SIMULATED or HISTORICAL unless "
        "labelled OBSERVED_DEMO. Nothing here is evidence of future profitability, and no orders are ever placed "
        "by this software.</p>",
        _table(data["data"], "Collected data"),
        _table(data["ingestion"], "Recent ingestion runs"),
        _table(data["quality"], "Data-quality issues"),
        _table(data["news"], "News provider status"),
        _table(data["models"], "Models"),
        _table(data["drift"], "Open drift alerts"),
        _table(data["decisions"], "Decisions by status", "mode column shows HISTORICAL / SIMULATED / OBSERVED_DEMO"),
        _table(data["rejections"], "Risk rejections by reason code"),
        _table(data["backtests"], "Recent backtests (SIMULATED)"),
        _table(data["performance"], "Strategy-performance memory", "n = sample size; std = reward standard deviation"),
        "</body></html>",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def write_csvs(data: dict[str, Any], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for key in ("data", "decisions", "rejections", "backtests", "performance", "drift", "models"):
        rows = data[key]
        p = directory / f"{key}.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        out.append(p)
    return out


def text_status(data: dict[str, Any]) -> str:
    lines = ["== data =="]
    for r in data["data"]:
        lines.append(
            f"{r['symbol']:8} {r['timeframe']:4} bars={r['n']:>8}  last={r['last']}  collected={r['collected']}"
        )
    if not data["data"]:
        lines.append("no bars collected yet (run `mt5bot collect`)")
    lines.append("== news providers ==")
    for r in data["news"]:
        lines.append(f"{r['provider_id']:12} last_success={r['last_success_at']} failures={r['consecutive_failures']}")
    if not data["news"]:
        lines.append("no provider has been polled (news state is UNKNOWN)")
    lines.append("== models ==")
    for r in data["models"][:10]:
        lines.append(f"{r['status']:10} {r['id']}")
    if not data["models"]:
        lines.append("no models trained")
    lines.append(f"== open drift alerts: {len(data['drift'])} ==")
    for r in data["drift"][:10]:
        lines.append(f"{r['severity']:8} {r['kind']:20} {r['scope']} {r['metric']}={r['value']}")
    lines.append("== decisions ==")
    for r in data["decisions"]:
        lines.append(f"{r['symbol']:8} {r['timeframe']:4} {r['mode']:13} {r['status']:10} {r['n']}")
    lines.append("== top risk rejections ==")
    for r in data["rejections"][:8]:
        lines.append(f"{r['check_code']:32} {r['n']}")
    return "\n".join(lines)


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)
