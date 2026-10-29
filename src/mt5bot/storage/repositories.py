"""Repository classes. All SQL lives here and is parameterized."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from mt5bot.core.types import Timeframe
from mt5bot.storage.db import transaction

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _check_cols(cols: list[str]) -> list[str]:
    """Column names are interpolated into SQL, so restrict them to plain identifiers."""
    for c in cols:
        if not _IDENT.match(c):
            raise ValueError(f"invalid column name {c!r}")
    return cols


def iso(ts: datetime | pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("naive timestamp")
    return t.tz_convert(UTC).isoformat()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_ts(text: str | None) -> pd.Timestamp | None:
    return None if text is None else pd.Timestamp(text).tz_convert(UTC)


class BarRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        df: pd.DataFrame,
        *,
        source: str,
        broker_symbol: str,
        run_id: int | None,
    ) -> int:
        """Insert validated bars; existing (symbol, timeframe, time) rows are left untouched."""
        if df.empty:
            return 0
        collected = now_iso()
        rows = [
            (
                symbol,
                timeframe.value,
                iso(r.time_utc),
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                int(r.tick_volume),
                int(r.spread_points),
                int(r.real_volume),
                source,
                broker_symbol,
                run_id,
                collected,
            )
            for r in df.itertuples(index=False)
        ]
        with transaction(self.conn):
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT INTO bars(symbol, timeframe, time_utc, open, high, low, close, "
                "tick_volume, spread_points, real_volume, source, broker_symbol, ingestion_run, collected_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol, timeframe, time_utc) DO NOTHING",
                rows,
            )
            return self.conn.total_changes - before

    def latest_time(self, symbol: str, timeframe: Timeframe) -> pd.Timestamp | None:
        row = self.conn.execute(
            "SELECT MAX(time_utc) AS t FROM bars WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe.value),
        ).fetchone()
        return parse_ts(row["t"])

    def load(
        self, symbol: str, timeframe: Timeframe, start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        sql = (
            "SELECT time_utc, open, high, low, close, tick_volume, spread_points, real_volume "
            "FROM bars WHERE symbol = ? AND timeframe = ?"
        )
        params: list[Any] = [symbol, timeframe.value]
        if start is not None:
            sql += " AND time_utc >= ?"
            params.append(iso(start))
        if end is not None:
            sql += " AND time_utc < ?"
            params.append(iso(end))
        sql += " ORDER BY time_utc"
        df = pd.read_sql_query(sql, self.conn, params=params)
        df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True, format="ISO8601")  # typed even when empty
        return df

    def count(self, symbol: str | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT symbol, timeframe, COUNT(*) AS n, MIN(time_utc) AS first, MAX(time_utc) AS last, "
            "MAX(collected_at) AS collected FROM bars"
        )
        params: list[Any] = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol)
        sql += " GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
        return self.conn.execute(sql, params).fetchall()


class QuoteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, symbol: str, df: pd.DataFrame, source: str) -> int:
        if df.empty:
            return 0
        ts = now_iso()
        rows = [
            (symbol, iso(r.time_utc), float(r.bid), float(r.ask), source, ts)
            for r in df.itertuples(index=False)
            if r.bid > 0 and r.ask >= r.bid
        ]
        with transaction(self.conn):
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT INTO quotes(symbol, time_utc, bid, ask, source, collected_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(symbol, time_utc) DO NOTHING",
                rows,
            )
            return self.conn.total_changes - before


class IngestionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def start_run(
        self, *, source: str, symbol: str, broker_symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO ingestion_runs(started_at, source, symbol, broker_symbol, timeframe, requested_start, "
            "requested_end, status) VALUES (?,?,?,?,?,?,?, 'RUNNING')",
            (now_iso(), source, symbol, broker_symbol, timeframe.value, iso(start), iso(end)),
        )
        return int(cur.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        received: int,
        inserted: int,
        rejected: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE ingestion_runs SET finished_at = ?, status = ?, bars_received = ?, bars_inserted = ?, "
            "bars_rejected = ?, error_code = ?, error_message = ? WHERE id = ?",
            (now_iso(), status, received, inserted, rejected, error_code, error_message, run_id),
        )

    def set_checkpoint(self, symbol: str, timeframe: Timeframe, last_bar: pd.Timestamp) -> None:
        self.conn.execute(
            "INSERT INTO ingestion_checkpoints(symbol, timeframe, last_bar_utc, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(symbol, timeframe) DO UPDATE SET last_bar_utc = excluded.last_bar_utc, "
            "updated_at = excluded.updated_at WHERE excluded.last_bar_utc > ingestion_checkpoints.last_bar_utc",
            (symbol, timeframe.value, iso(last_bar), now_iso()),
        )

    def checkpoint(self, symbol: str, timeframe: Timeframe) -> pd.Timestamp | None:
        row = self.conn.execute(
            "SELECT last_bar_utc FROM ingestion_checkpoints WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe.value),
        ).fetchone()
        return parse_ts(row["last_bar_utc"]) if row else None

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def record_issues(self, issues: Iterable[dict[str, Any]], run_id: int | None) -> int:
        rows = [
            (
                now_iso(),
                i["symbol"],
                i.get("timeframe"),
                i.get("time_utc"),
                i["issue"],
                i["severity"],
                i.get("detail"),
                run_id,
            )
            for i in issues
        ]
        if not rows:
            return 0
        with transaction(self.conn):
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT INTO data_quality_issues(detected_at, symbol, timeframe, time_utc, issue, "
                "severity, detail, ingestion_run) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                rows,
            )
            return self.conn.total_changes - before

    def issue_summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT symbol, timeframe, issue, severity, COUNT(*) AS n FROM data_quality_issues "
            "GROUP BY symbol, timeframe, issue, severity ORDER BY symbol, timeframe, n DESC"
        ).fetchall()


class NewsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_items(self, items: Sequence[Any]) -> int:
        rows = [
            (
                i.fingerprint,
                i.provider_id,
                i.source_name,
                i.provider_item_id,
                i.headline,
                i.summary,
                i.url,
                iso(i.published_at) if i.published_at is not None else None,
                iso(i.retrieved_at),
                iso(i.available_at),
                i.category,
                json.dumps(sorted(i.currencies)),
                i.importance,
                i.sentiment,
                i.quality_status,
            )
            for i in items
        ]
        if not rows:
            return 0
        with transaction(self.conn):
            before = self.conn.total_changes
            # ON CONFLICT DO NOTHING keeps the FIRST retrieval time, which is what availability depends on.
            self.conn.executemany(
                "INSERT INTO news_items(fingerprint, provider_id, source_name, provider_item_id, headline, "
                "summary, url, published_at, retrieved_at, available_at, category, currencies, importance, sentiment, "
                "quality_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING",
                rows,
            )
            return self.conn.total_changes - before

    def available_at(
        self, decision_time: datetime, lookback_minutes: int, currencies: Iterable[str]
    ) -> list[sqlite3.Row]:
        """News usable at ``decision_time``: available no later than it, within the lookback."""
        start = pd.Timestamp(decision_time) - pd.Timedelta(minutes=lookback_minutes)
        rows = self.conn.execute(
            "SELECT * FROM news_items WHERE available_at <= ? AND available_at >= ? ORDER BY available_at",
            (iso(decision_time), iso(start)),
        ).fetchall()
        wanted = set(currencies)
        return [r for r in rows if wanted & set(json.loads(r["currencies"]))]

    def insert_events(self, events: Sequence[Any]) -> int:
        rows = [
            (
                e.fingerprint,
                e.source,
                iso(e.scheduled_at),
                e.currency,
                e.title,
                e.category,
                e.importance,
                e.actual,
                e.forecast,
                e.previous,
                iso(e.published_at) if e.published_at else None,
                iso(e.retrieved_at),
            )
            for e in events
        ]
        if not rows:
            return 0
        with transaction(self.conn):
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT INTO economic_events(fingerprint, source, scheduled_at, currency, title, category, "
                "importance, actual, forecast, previous, published_at, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(fingerprint) DO NOTHING",
                rows,
            )
            return self.conn.total_changes - before

    def events_between(self, start: datetime, end: datetime, known_at: datetime) -> list[sqlite3.Row]:
        """Scheduled events in [start, end] that were already known (retrieved) at ``known_at``."""
        return self.conn.execute(
            "SELECT * FROM economic_events WHERE scheduled_at >= ? AND scheduled_at <= ? AND retrieved_at <= ? "
            "ORDER BY scheduled_at",
            (iso(start), iso(end), iso(known_at)),
        ).fetchall()

    def record_provider_attempt(self, provider_id: str, ok: bool, items: int, error: str | None) -> None:
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO provider_status(provider_id, last_attempt_at, last_success_at, last_error, "
            "consecutive_failures, items_last_fetch) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
            "last_success_at = COALESCE(excluded.last_success_at, provider_status.last_success_at), "
            "last_error = excluded.last_error, "
            "consecutive_failures = CASE WHEN excluded.last_success_at IS NULL "
            "THEN provider_status.consecutive_failures + 1 ELSE 0 END, "
            "items_last_fetch = excluded.items_last_fetch",
            (provider_id, ts, ts if ok else None, error, 0 if ok else 1, items),
        )

    def provider_status(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM provider_status ORDER BY provider_id").fetchall()


class ModelRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, rec: dict[str, Any]) -> None:
        cols = list(rec)
        self.conn.execute(
            f"INSERT INTO model_versions({', '.join(_check_cols(cols))}) VALUES ({', '.join('?' for _ in cols)})",
            [rec[c] for c in cols],
        )

    def get(self, model_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM model_versions WHERE id = ?", (model_id,)).fetchone()

    def champion(self, kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM model_versions WHERE kind = ? AND status = 'CHAMPION'", (kind,)
        ).fetchone()

    def list_all(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM model_versions WHERE kind = ? ORDER BY created_at DESC", (kind,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM model_versions ORDER BY created_at DESC").fetchall()

    def set_status(self, model_id: str, status: str) -> None:
        self.conn.execute("UPDATE model_versions SET status = ? WHERE id = ?", (status, model_id))

    def record_promotion(
        self, *, kind: str, action: str, model_id: str, previous: str | None, reason: str, gates: dict[str, Any]
    ) -> None:
        self.conn.execute(
            "INSERT INTO promotions(created_at, kind, action, model_id, previous_champion, reason, gates_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (now_iso(), kind, action, model_id, previous, reason, json.dumps(gates, default=str)),
        )

    def promotions(self, kind: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM promotions WHERE kind = ? ORDER BY id DESC", (kind,)).fetchall()

    def add_evaluation(self, model_id: str | None, split: str, start: str, end: str, metrics: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO evaluation_runs(model_id, created_at, split, period_start, period_end, metrics_json) "
            "VALUES (?,?,?,?,?,?)",
            (model_id, now_iso(), split, start, end, json.dumps(metrics, default=float)),
        )


class ExperimentRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(
        self,
        *,
        kind: str,
        config_hash: str,
        params: dict[str, Any],
        n_trials: int = 1,
        code_version: str | None = None,
        data_fingerprint: str | None = None,
        notes: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO experiments(created_at, kind, config_hash, code_version, data_fingerprint, params_json, "
            "n_trials, notes) VALUES (?,?,?,?,?,?,?,?)",
            (
                now_iso(),
                kind,
                config_hash,
                code_version,
                data_fingerprint,
                json.dumps(params, default=str),
                n_trials,
                notes,
            ),
        )
        return int(cur.lastrowid or 0)

    def total_trials(self, kind: str | None = None) -> int:
        if kind:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(n_trials),0) AS n FROM experiments WHERE kind = ?", (kind,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COALESCE(SUM(n_trials),0) AS n FROM experiments").fetchone()
        return int(row["n"])


class BacktestRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        strategies: list[str],
        cost_multiplier: float,
        config: dict[str, Any],
        metrics: dict[str, Any],
        trades: pd.DataFrame,
        experiment_id: int | None = None,
    ) -> int:
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT INTO backtest_runs(created_at, experiment_id, symbol, timeframe, period_start, period_end, "
                "strategies, cost_multiplier, config_json, metrics_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    now_iso(),
                    experiment_id,
                    symbol,
                    timeframe,
                    start,
                    end,
                    json.dumps(strategies),
                    cost_multiplier,
                    json.dumps(config, default=str),
                    json.dumps(metrics, default=float),
                ),
            )
            run_id = int(cur.lastrowid or 0)
            if not trades.empty:
                self.conn.executemany(
                    "INSERT INTO simulated_trades(backtest_run, strategy, direction, signal_time, entry_time, "
                    "entry_price, exit_time, exit_price, exit_reason, lots, gross_pnl_usd, cost_usd, net_pnl_usd, "
                    "net_r, regime) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            run_id,
                            t.strategy,
                            t.direction,
                            iso(t.signal_time),
                            iso(t.entry_time),
                            float(t.entry_price),
                            iso(t.exit_time),
                            float(t.exit_price),
                            t.exit_reason,
                            float(t.lots),
                            float(t.gross_pnl_usd),
                            float(t.cost_usd),
                            float(t.net_pnl_usd),
                            float(t.net_r),
                            getattr(t, "regime", None),
                        )
                        for t in trades.itertuples(index=False)
                    ],
                )
        return run_id

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


class DecisionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(
        self,
        decision: dict[str, Any],
        candidates: list[dict[str, Any]],
        risk_checks: list[dict[str, Any]],
        regimes: list[dict[str, Any]],
    ) -> int | None:
        """Persist a decision with candidates and risk checks atomically; returns None if it already exists."""
        cols = list(decision)
        with transaction(self.conn):
            cur = self.conn.execute(
                f"INSERT INTO decisions({', '.join(_check_cols(cols))}) VALUES ({', '.join('?' for _ in cols)}) "
                "ON CONFLICT(symbol, timeframe, bar_time_utc, mode) DO NOTHING",
                [decision[c] for c in cols],
            )
            if cur.rowcount == 0:
                return None
            did = int(cur.lastrowid or 0)
            for c in candidates:
                self.conn.execute(
                    "INSERT INTO strategy_candidates(decision_id, strategy, strategy_version, direction, eligible, "
                    "eligibility_reason, probability, lcb_reward, score, sample_size, rule_eligible, stop_distance, "
                    "target_distance, max_hold_bars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        did,
                        c["strategy"],
                        c["strategy_version"],
                        c["direction"],
                        int(c["eligible"]),
                        c.get("eligibility_reason"),
                        c.get("probability"),
                        c.get("lcb_reward"),
                        c.get("score"),
                        c.get("sample_size"),
                        int(c.get("rule_eligible", 0)),
                        c.get("stop_distance"),
                        c.get("target_distance"),
                        c.get("max_hold_bars"),
                    ),
                )
            for r in risk_checks:
                self.conn.execute(
                    "INSERT INTO risk_evaluations(decision_id, evaluated_at, check_code, passed, detail) "
                    "VALUES (?,?,?,?,?)",
                    (did, now_iso(), r["code"], int(r["passed"]), r.get("detail")),
                )
            for g in regimes:
                self.conn.execute(
                    "INSERT INTO regime_predictions(decision_id, model, regime, confidence) VALUES (?,?,?,?)",
                    (did, g["model"], g["regime"], g.get("confidence")),
                )
        return did

    def get(self, decision_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()

    def pending(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decisions WHERE eval_status = 'PENDING' ORDER BY bar_time_utc LIMIT ?", (limit,)
        ).fetchall()

    def save_outcome(self, decision_id: int, outcome: dict[str, Any]) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT OR REPLACE INTO decision_outcomes(decision_id, evaluated_at, label_version, exit_reason, "
                "exit_time_utc, gross_r, net_r, mae_r, mfe_r, reward, attribution, evidence_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    now_iso(),
                    outcome["label_version"],
                    outcome.get("exit_reason"),
                    outcome.get("exit_time_utc"),
                    outcome.get("gross_r"),
                    outcome.get("net_r"),
                    outcome.get("mae_r"),
                    outcome.get("mfe_r"),
                    outcome.get("reward"),
                    outcome["attribution"],
                    json.dumps(outcome.get("evidence", {}), default=str),
                ),
            )
            self.conn.execute(
                "UPDATE decisions SET eval_status = ? WHERE id = ?", (outcome.get("eval_status", "DONE"), decision_id)
            )

    def save_candidate_outcome(self, candidate_id: int, outcome: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO candidate_outcomes(candidate_id, evaluated_at, label_version, provenance, exit_reason, "
            "exit_time_utc, net_r, mae_r, reward) VALUES (?,?,?,'SIMULATED',?,?,?,?,?) "
            "ON CONFLICT(candidate_id) DO NOTHING",
            (
                candidate_id,
                now_iso(),
                outcome["label_version"],
                outcome.get("exit_reason"),
                outcome.get("exit_time_utc"),
                outcome.get("net_r"),
                outcome.get("mae_r"),
                outcome.get("reward"),
            ),
        )

    def risk_checks(self, decision_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM risk_evaluations WHERE decision_id = ? ORDER BY id", (decision_id,)
        ).fetchall()

    def candidates(self, decision_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM strategy_candidates WHERE decision_id = ? ORDER BY id", (decision_id,)
        ).fetchall()

    def outcome(self, decision_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM decision_outcomes WHERE decision_id = ?", (decision_id,)).fetchone()

    def summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT symbol, timeframe, mode, status, COUNT(*) AS n FROM decisions "
            "GROUP BY symbol, timeframe, mode, status ORDER BY symbol, timeframe"
        ).fetchall()

    def rejection_summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT check_code, COUNT(*) AS n FROM risk_evaluations WHERE passed = 0 "
            "GROUP BY check_code ORDER BY n DESC"
        ).fetchall()

    def recent_outcomes(self, limit: int = 1000) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT d.id, d.symbol, d.timeframe, d.selected_strategy AS strategy, d.regime, d.bar_time_utc, "
            "o.net_r, o.reward, o.attribution FROM decision_outcomes o JOIN decisions d ON d.id = o.decision_id "
            "WHERE o.net_r IS NOT NULL ORDER BY d.bar_time_utc DESC LIMIT ?",
            self.conn,
            params=(limit,),
        )


class PerformanceRepository:
    """Welford running statistics per context bucket."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def update(self, bucket: dict[str, Any], reward: float) -> None:
        key = json.dumps(bucket, sort_keys=True)
        with transaction(self.conn):
            row = self.conn.execute(
                "SELECT n, mean_reward, m2_reward, wins FROM strategy_performance WHERE bucket_key = ?", (key,)
            ).fetchone()
            n, mean, m2, wins = (
                (row["n"], row["mean_reward"], row["m2_reward"], row["wins"]) if row else (0, 0.0, 0.0, 0)
            )
            n += 1
            delta = reward - mean
            mean += delta / n
            m2 += delta * (reward - mean)
            wins += int(reward > 0)
            self.conn.execute(
                "INSERT INTO strategy_performance(bucket_key, strategy, n, mean_reward, m2_reward, wins, updated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(bucket_key) DO UPDATE SET n = excluded.n, "
                "mean_reward = excluded.mean_reward, m2_reward = excluded.m2_reward, wins = excluded.wins, "
                "updated_at = excluded.updated_at",
                (key, bucket["strategy"], n, mean, m2, wins, now_iso()),
            )

    def get(self, bucket: dict[str, Any]) -> tuple[int, float, float] | None:
        """Return (n, mean, sample variance) for a bucket."""
        row = self.conn.execute(
            "SELECT n, mean_reward, m2_reward FROM strategy_performance WHERE bucket_key = ?",
            (json.dumps(bucket, sort_keys=True),),
        ).fetchone()
        if not row:
            return None
        n = int(row["n"])
        var = row["m2_reward"] / (n - 1) if n > 1 else float("inf")
        return n, float(row["mean_reward"]), float(var)

    def all(self) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT * FROM strategy_performance ORDER BY strategy, n DESC", self.conn)
        if not df.empty:
            df["std"] = (df["m2_reward"] / (df["n"] - 1).clip(lower=1)) ** 0.5
        return df


class DriftRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, alert: Any) -> int:
        cur = self.conn.execute(
            "INSERT INTO drift_alerts(detected_at, kind, severity, scope, metric, value, threshold, evidence_json, "
            "response) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                iso(alert.detected_at),
                alert.kind,
                alert.severity,
                alert.scope,
                alert.metric,
                alert.value,
                alert.threshold,
                json.dumps(alert.evidence, default=str),
                alert.response,
            ),
        )
        return int(cur.lastrowid or 0)

    def open_alerts(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM drift_alerts WHERE resolved_at IS NULL ORDER BY detected_at DESC"
        ).fetchall()

    def resolve(self, alert_id: int) -> None:
        self.conn.execute("UPDATE drift_alerts SET resolved_at = ? WHERE id = ?", (now_iso(), alert_id))


class StrategyStatusRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def set_status(self, strategy: str, symbol: str, timeframe: str, status: str, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO strategy_status(strategy, symbol, timeframe, status, reason, changed_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(strategy, symbol, timeframe) DO UPDATE SET status = excluded.status, "
            "reason = excluded.reason, changed_at = excluded.changed_at",
            (strategy, symbol, timeframe, status, reason, now_iso()),
        )

    def suspended(self, symbol: str, timeframe: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT strategy FROM strategy_status WHERE symbol = ? AND timeframe = ? AND status = 'SUSPENDED'",
            (symbol, timeframe),
        ).fetchall()
        return {r["strategy"] for r in rows}


class LLMRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def log(self, **rec: Any) -> None:
        cols = list(rec)
        self.conn.execute(
            f"INSERT INTO llm_requests({', '.join(_check_cols(cols))}) VALUES ({', '.join('?' for _ in cols)})",
            [rec[c] for c in cols],
        )

    def cached(self, cache_key: str, not_before: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM llm_requests WHERE cache_key = ? AND status = 'OK' AND requested_at >= ? "
            "ORDER BY id DESC LIMIT 1",
            (cache_key, not_before),
        ).fetchone()

    def spend_since(self, since: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_requests WHERE requested_at >= ?", (since,)
        ).fetchone()
        return float(row["c"])


class InstrumentRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, spec: Any, broker_symbol: str, source: str) -> None:
        self.conn.execute(
            "INSERT INTO instrument_specs(symbol, broker_symbol, base, quote, point, digits, contract_size, "
            "volume_min, volume_step, volume_max, source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET broker_symbol=excluded.broker_symbol, base=excluded.base, "
            "quote=excluded.quote, point=excluded.point, digits=excluded.digits, contract_size=excluded.contract_size, "
            "volume_min=excluded.volume_min, volume_step=excluded.volume_step, volume_max=excluded.volume_max, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (
                spec.symbol,
                broker_symbol,
                spec.base,
                spec.quote,
                spec.point,
                spec.digits,
                spec.contract_size,
                spec.volume_min,
                spec.volume_step,
                spec.volume_max,
                source,
                now_iso(),
            ),
        )

    def get(self, symbol: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM instrument_specs WHERE symbol = ?", (symbol,)).fetchone()


class SimStateRepository:
    """Simulated account state derived from recorded RECOMMEND decisions (never from a broker)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def open_positions(self, mode: str, at: datetime) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT d.symbol, d.direction, d.bar_time_utc, d.timeframe, d.max_hold_bars, o.exit_time_utc "
            "FROM decisions d LEFT JOIN decision_outcomes o ON o.decision_id = d.id "
            "WHERE d.mode = ? AND d.status = 'RECOMMEND' AND d.bar_time_utc <= ?",
            (mode, iso(at)),
        ).fetchall()
        out = []
        t = pd.Timestamp(at)
        for r in rows:
            if r["exit_time_utc"] is not None:
                still_open = pd.Timestamp(r["exit_time_utc"]) >= t
            else:
                tf = Timeframe(r["timeframe"])
                still_open = pd.Timestamp(r["bar_time_utc"]) + tf.delta * (int(r["max_hold_bars"]) + 1) > t
            if still_open:
                out.append((r["symbol"], r["direction"]))
        return out

    def realized(self, mode: str, at: datetime, limit: int = 200) -> pd.DataFrame:
        """Outcomes of RECOMMEND decisions whose exit was known by ``at`` (newest first)."""
        return pd.read_sql_query(
            "SELECT o.exit_time_utc, o.net_r FROM decision_outcomes o JOIN decisions d ON d.id = o.decision_id "
            "WHERE d.mode = ? AND d.status = 'RECOMMEND' AND o.net_r IS NOT NULL AND o.exit_time_utc <= ? "
            "ORDER BY o.exit_time_utc DESC LIMIT ?",
            self.conn,
            params=(mode, iso(at), limit),
        )
