"""Vector memory with two backends: SQLite (exact cosine in numpy) and PostgreSQL with pgvector (HNSW)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import numpy as np
import pandas as pd

from mt5bot.core.errors import StorageError

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")


@dataclass(frozen=True)
class Hit:
    item_id: int
    similarity: float
    metadata: dict[str, Any]


@dataclass
class Item:
    item_id: int
    vector: np.ndarray
    metadata: dict[str, Any]
    available_at: datetime


class VectorStore(Protocol):
    def add_many(self, collection: str, items: list[Item]) -> int: ...
    def search(
        self,
        collection: str,
        vector: np.ndarray,
        k: int,
        available_before: datetime,
        where: dict[str, str] | None = None,
    ) -> list[Hit]: ...
    def count(self, collection: str) -> int: ...


def _check(collection: str) -> str:
    if not _NAME.match(collection):
        raise StorageError(f"invalid collection name {collection!r}")
    return collection


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


@dataclass
class _Cache:
    ids: list[int] = field(default_factory=list)
    vectors: list[np.ndarray] = field(default_factory=list)
    meta: list[dict[str, Any]] = field(default_factory=list)
    times: list[int] = field(default_factory=list)
    matrix: np.ndarray | None = None
    time_arr: np.ndarray | None = None
    cols: dict[str, np.ndarray] = field(default_factory=dict)


def _epoch(ts: datetime | str) -> int:
    return int(pd.Timestamp(ts).timestamp())


class SqliteVectorStore:
    """Vectors live in the main SQLite database; search is exact cosine similarity over an in-memory matrix."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._cache: dict[str, _Cache] = {}

    def _load(self, collection: str) -> _Cache:
        c = self._cache.get(_check(collection))
        if c is None:
            c = _Cache()
            rows = self.conn.execute(
                "SELECT item_id, vector, metadata_json, available_at FROM memory_vectors WHERE collection = ? "
                "ORDER BY item_id",
                (collection,),
            ).fetchall()
            for r in rows:
                c.ids.append(int(r["item_id"]))
                c.vectors.append(np.frombuffer(r["vector"], dtype=np.float32))
                c.meta.append(json.loads(r["metadata_json"]))
                c.times.append(_epoch(r["available_at"]))
            self._cache[collection] = c
        return c

    def add_many(self, collection: str, items: list[Item]) -> int:
        if not items:
            return 0
        c = self._load(collection)
        known = set(c.ids)
        fresh = [it for it in items if it.item_id not in known]
        rows = [
            (
                collection,
                it.item_id,
                len(it.vector),
                _unit(it.vector).tobytes(),
                json.dumps(it.metadata, sort_keys=True),
                pd.Timestamp(it.available_at).isoformat(),
            )
            for it in fresh
        ]
        self.conn.executemany(
            "INSERT INTO memory_vectors(collection, item_id, dim, vector, metadata_json, available_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(collection, item_id) DO NOTHING",
            rows,
        )
        for it in fresh:
            c.ids.append(it.item_id)
            c.vectors.append(_unit(it.vector))
            c.meta.append(it.metadata)
            c.times.append(_epoch(it.available_at))
        c.matrix, c.time_arr, c.cols = None, None, {}
        return len(fresh)

    def search(
        self,
        collection: str,
        vector: np.ndarray,
        k: int,
        available_before: datetime,
        where: dict[str, str] | None = None,
    ) -> list[Hit]:
        c = self._load(collection)
        if not c.ids:
            return []
        if c.matrix is None:
            c.matrix, c.time_arr = np.vstack(c.vectors), np.asarray(c.times, dtype=np.int64)
        assert c.time_arr is not None
        mask = c.time_arr <= _epoch(available_before)
        for key, value in (where or {}).items():
            if key not in c.cols:
                c.cols[key] = np.asarray([str(m.get(key, "")) for m in c.meta])
            mask &= c.cols[key] == value
        idx = np.flatnonzero(mask)
        if len(idx) == 0:
            return []
        sims = c.matrix[idx] @ _unit(vector)
        top = np.argsort(-sims, kind="stable")[:k]
        return [Hit(c.ids[idx[i]], float(sims[i]), c.meta[idx[i]]) for i in top]

    def count(self, collection: str) -> int:
        return len(self._load(collection).ids)


class PgVectorStore:
    """PostgreSQL + pgvector backend: one table per collection with an HNSW cosine index."""

    def __init__(self, dsn: str, dims: dict[str, int]) -> None:
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise StorageError("pgvector backend needs: pip install 'mt5-sclapingbot[pg]'") from exc
        try:
            self.conn = psycopg.connect(dsn, autocommit=True)
            self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(self.conn)
            self.conn.execute("SET hnsw.ef_search = 200")
        except psycopg.Error as exc:  # the message may hold connection details, so only the error class is kept
            raise StorageError(f"cannot use PostgreSQL/pgvector ({type(exc).__name__})") from None
        self.dims = dims
        for collection, dim in dims.items():
            t = self._table(collection)
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {t} (item_id bigint PRIMARY KEY, embedding vector({int(dim)}) NOT NULL, "
                "metadata jsonb NOT NULL, available_at timestamptz NOT NULL)"
            )
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {t}_hnsw ON {t} USING hnsw (embedding vector_cosine_ops)")
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {t}_time ON {t} (available_at)")

    @staticmethod
    def _table(collection: str) -> str:
        return f"mt5bot_mem_{_check(collection)}"

    def add_many(self, collection: str, items: list[Item]) -> int:
        from psycopg.types.json import Jsonb

        if not items:
            return 0
        t = self._table(collection)
        added = 0
        with self.conn.cursor() as cur:
            for it in items:
                cur.execute(
                    f"INSERT INTO {t} (item_id, embedding, metadata, available_at) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (item_id) DO NOTHING",
                    (it.item_id, _unit(it.vector), Jsonb(it.metadata), pd.Timestamp(it.available_at).to_pydatetime()),
                )
                added += cur.rowcount
        return added

    def search(
        self,
        collection: str,
        vector: np.ndarray,
        k: int,
        available_before: datetime,
        where: dict[str, str] | None = None,
    ) -> list[Hit]:
        from psycopg.types.json import Jsonb

        t = self._table(collection)
        q = _unit(vector)
        rows = self.conn.execute(
            f"SELECT item_id, 1 - (embedding <=> %s) AS similarity, metadata FROM {t} "
            "WHERE available_at <= %s AND metadata @> %s ORDER BY embedding <=> %s LIMIT %s",
            (q, pd.Timestamp(available_before).to_pydatetime(), Jsonb(where or {}), q, k),
        ).fetchall()
        return [Hit(int(r[0]), float(r[1]), dict(r[2])) for r in rows]

    def count(self, collection: str) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) FROM {self._table(collection)}").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self.conn.close()


def open_vector_store(conn: sqlite3.Connection, backend: str, dsn: str | None, dims: dict[str, int]) -> VectorStore:
    """Return the configured backend; pgvector failures raise instead of silently falling back."""
    if backend == "pgvector":
        if not dsn:
            raise StorageError("memory.backend is pgvector but memory.dsn is not set")
        return PgVectorStore(dsn, dims)
    return SqliteVectorStore(conn)
