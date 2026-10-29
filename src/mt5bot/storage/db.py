"""SQLite connection management and versioned, forward-only migrations."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from mt5bot.core.errors import StorageError

_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database with safe defaults (WAL, foreign keys, busy timeout)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All-or-nothing unit of work."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _available_migrations() -> list[tuple[int, str, str]]:
    pkg = resources.files("mt5bot.storage") / "migrations"
    found = []
    for entry in pkg.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            found.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise StorageError(f"migration numbering must be contiguous from 0001, got {versions}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)


def backup(conn: sqlite3.Connection, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest


def migrate(conn: sqlite3.Connection, db_path: Path | str | None = None) -> list[str]:
    """Apply pending migrations; back up a non-empty database first. Returns applied names."""
    have = current_version(conn)
    pending = [m for m in _available_migrations() if m[0] > have]
    if not pending:
        return []
    if have > 0 and db_path and str(db_path) != ":memory:":
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup(conn, Path(db_path).with_suffix(f".pre-v{pending[0][0]:04d}-{stamp}.bak"))
    applied = []
    for version, name, sql in pending:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _split_sql(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(UTC).isoformat()),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise StorageError(f"migration {name} failed: {exc}") from exc
        applied.append(name)
    return applied


def _split_sql(sql: str) -> list[str]:
    """Split a migration file into statements (no semicolons inside literals in our files)."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    parts = "\n".join(lines).split(";")
    return [p.strip() for p in parts if p.strip()]


def open_database(path: Path | str) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn, path)
    return conn
