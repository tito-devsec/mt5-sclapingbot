"""Daily lessons written from recorded outcomes, and the cached playbook of proven skills (CAG)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

import pandas as pd

from mt5bot.agent.recall import LESSONS
from mt5bot.agent.skills import SkillBook, SkillKey, bounds
from mt5bot.memory.embed import HashingEmbedder
from mt5bot.memory.vector import Item, VectorStore
from mt5bot.storage.repositories import now_iso
from mt5bot.strategies.techniques import entry_of

MIN_GROUP = 5


def write_lessons(conn: sqlite3.Connection, store: VectorStore, embedder: HashingEmbedder, now: pd.Timestamp) -> int:
    """Summarise each completed UTC day that has outcomes but no lessons yet; returns the number of lessons added."""
    today = now.strftime("%Y-%m-%d")
    days = [
        r["day"]
        for r in conn.execute(
            "SELECT DISTINCT substr(available_at, 1, 10) AS day FROM episodes WHERE substr(available_at, 1, 10) < ? "
            "AND substr(available_at, 1, 10) NOT IN (SELECT day FROM lessons) ORDER BY day",
            (today,),
        ).fetchall()
    ]
    added = 0
    for day in days:
        raw = pd.read_sql_query(
            "SELECT symbol, timeframe, technique, regime, session, source, direction, decided_at, reward FROM episodes "
            "WHERE substr(available_at, 1, 10) = ?",
            conn,
            params=(day,),
        )
        raw["entry"] = raw["technique"].map(entry_of)
        # One signal fires several exit profiles; average them so each signal counts once.
        keys = ["symbol", "timeframe", "entry", "regime", "session", "source", "direction", "decided_at"]
        df = raw.groupby(keys, as_index=False)["reward"].mean()
        lessons: list[tuple[str, str, dict[str, Any]]] = []
        win = float((df["reward"] > 0).mean())
        lessons.append(
            (
                "summary",
                f"{day}: {len(df)} signals studied ({len(raw)} outcomes), mean {df['reward'].mean():+.2f}R, "
                f"{win:.0%} positive.",
                {"signals": len(df), "outcomes": len(raw), "status": "observation"},
            )
        )
        g = df.groupby(["symbol", "timeframe", "entry", "regime", "session"])["reward"].agg(["count", "mean"])
        g = g[g["count"] >= MIN_GROUP].sort_values("mean")
        for kind, part in (("worked", g[g["mean"] > 0].tail(3)), ("failed", g[g["mean"] < 0].head(3))):
            for (sym, tf, entry, regime, session), row in part.iterrows():
                verb = "worked" if kind == "worked" else "failed"
                n = int(row["count"])
                lessons.append(
                    (
                        kind,
                        f"{day} {sym} {tf}: {entry} {verb} in {regime} during {session} session, "
                        f"mean {row['mean']:+.2f}R over {n} signals (one day; an observation, not a rule).",
                        {
                            "signals": n,
                            "mean": float(row["mean"]),
                            "symbol": sym,
                            "timeframe": tf,
                            "regime": regime,
                            "session": session,
                            "date": day,
                            "confidence": "low" if n < 30 else "medium",
                            "status": "observation",
                        },
                    )
                )
        user = df[df["source"] == "USER"]
        if len(user):
            lessons.append(
                (
                    "user",
                    f"{day}: {len(user)} of your own trades were studied, mean {user['reward'].mean():+.2f} ATR.",
                    {"n": len(user)},
                )
            )
        events = conn.execute(
            "SELECT e.to_state, e.reason, s.technique, s.symbol, s.timeframe, s.regime, s.session FROM skill_events e "
            "JOIN skills s ON s.id = e.skill_id WHERE substr(e.at, 1, 10) = ? "
            "AND e.to_state IN ('VALIDATED','RETIRED')",
            (day,),
        ).fetchall()
        for e in events:
            what = "became a proven skill" if e["to_state"] == "VALIDATED" else "was retired"
            lessons.append(
                (
                    "skill",
                    f"{day} {e['symbol']} {e['timeframe']}: {e['technique']} in {e['regime']} during "
                    f"{e['session']} session {what} ({e['reason']}).",
                    {},
                )
            )
        items = []
        for kind, text, evidence in lessons:
            cur = conn.execute(
                "INSERT INTO lessons(day, created_at, kind, text, evidence_json) VALUES (?,?,?,?,?) "
                "ON CONFLICT(day, kind, text) DO NOTHING",
                (day, now_iso(), kind, text, json.dumps(evidence)),
            )
            if cur.rowcount:
                vec = embedder.embed([text])[0]
                items.append(
                    Item(
                        int(cur.lastrowid or 0),
                        vec,
                        {"day": day, "kind": kind, "text": text},
                        pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1),
                    )
                )
        added += store.add_many(LESSONS, items)
    return added


def build_playbook(conn: sqlite3.Connection, book: SkillBook) -> dict[str, Any]:
    """Compact, precomputed knowledge the policy loads once: proven skills by context, counts and recent lessons."""
    cfg = book.cfg
    skills = {}
    for r in book.validated():
        key = SkillKey(r["technique"], r["symbol"], r["timeframe"], r["regime"], r["session"])
        n, mean, m2 = (
            (r["stage_n"], r["stage_mean"], r["stage_m2"])
            if r["stage_n"] >= 2
            else (r["n"], r["mean_reward"], r["m2_reward"])
        )
        lo, _ = bounds(n, mean, m2, cfg.confirm_z)
        skills[key.text] = {"n": int(r["n"]), "mean": round(float(r["mean_reward"]), 4), "lcb": round(float(lo), 4)}
    lessons = [r["text"] for r in conn.execute("SELECT text FROM lessons ORDER BY id DESC LIMIT 20").fetchall()]
    return {"skills": skills, "counts": book.counts(), "lessons": lessons}


def save_playbook(conn: sqlite3.Connection, playbook: dict[str, Any]) -> bool:
    """Store the playbook if its content changed; returns True when a new version was written."""
    content = json.dumps(playbook, sort_keys=True)
    digest = hashlib.sha256(content.encode()).hexdigest()[:16]
    last = conn.execute("SELECT content_hash FROM playbook_cache ORDER BY id DESC LIMIT 1").fetchone()
    if last is not None and last["content_hash"] == digest:
        return False
    conn.execute(
        "INSERT INTO playbook_cache(built_at, content_hash, content_json) VALUES (?,?,?)", (now_iso(), digest, content)
    )
    return True


def load_playbook(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT content_json FROM playbook_cache ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return {"skills": {}, "counts": {}, "lessons": []}
    data: dict[str, Any] = json.loads(row["content_json"])
    return data
