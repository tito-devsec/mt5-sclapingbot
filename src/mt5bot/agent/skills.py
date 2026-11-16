"""Skill book: a technique in a market context is VALIDATED only after discovery and fresh-sample confirmation."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

import pandas as pd

from mt5bot.config.settings import AgentSettings

STATES = ("EXPERIMENTAL", "PROBATION", "VALIDATED", "RETIRED")


@dataclass(frozen=True)
class SkillKey:
    technique: str
    symbol: str
    timeframe: str
    regime: str
    session: str

    @property
    def text(self) -> str:
        return "::".join((self.technique, self.symbol, self.timeframe, self.regime, self.session))


def bounds(n: int, mean: float, m2: float, z: float) -> tuple[float, float]:
    """Lower and upper confidence bounds of the mean reward; infinite until two samples exist."""
    if n < 2:
        return -math.inf, math.inf
    se = math.sqrt(max(m2 / (n - 1), 0.0) / n)
    return mean - z * se, mean + z * se


class SkillBook:
    def __init__(self, conn: sqlite3.Connection, cfg: AgentSettings) -> None:
        self.conn = conn
        self.cfg = cfg

    def observe(self, key: SkillKey, reward: float, at: pd.Timestamp) -> str:
        """Record one outcome for a skill, apply lifecycle rules, and return the resulting state."""
        ts = at.isoformat()
        row = self.conn.execute(
            "SELECT * FROM skills WHERE technique = ? AND symbol = ? AND timeframe = ? AND regime = ? AND session = ?",
            (key.technique, key.symbol, key.timeframe, key.regime, key.session),
        ).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO skills(technique, symbol, timeframe, regime, session, state, created_at, updated_at, "
                "state_changed_at) VALUES (?,?,?,?,?,'EXPERIMENTAL',?,?,?)",
                (key.technique, key.symbol, key.timeframe, key.regime, key.session, ts, ts, ts),
            )
            sid, state, changed = int(cur.lastrowid or 0), "EXPERIMENTAL", ts
            n, mean, m2, wins, sn, smean, sm2 = 0, 0.0, 0.0, 0, 0, 0.0, 0.0
        else:
            sid, state, changed = int(row["id"]), row["state"], row["state_changed_at"]
            n, mean, m2, wins = row["n"], row["mean_reward"], row["m2_reward"], row["wins"]
            sn, smean, sm2 = row["stage_n"], row["stage_mean"], row["stage_m2"]
        if state == "RETIRED" and at - pd.Timestamp(changed) >= pd.Timedelta(days=self.cfg.retired_cooldown_days):
            state, changed, sn, smean, sm2 = self._move(sid, state, "EXPERIMENTAL", ts, "cooldown over; re-evaluating")
        n, mean, m2 = _welford(n, mean, m2, reward)
        sn, smean, sm2 = _welford(sn, smean, sm2, reward)
        wins += int(reward > 0)
        new_state, reason = self._transition(state, sn, smean, sm2)
        if new_state != state:
            state, changed, sn, smean, sm2 = self._move(sid, state, new_state, ts, reason)
        self.conn.execute(
            "UPDATE skills SET state = ?, n = ?, mean_reward = ?, m2_reward = ?, wins = ?, stage_n = ?, "
            "stage_mean = ?, stage_m2 = ?, updated_at = ?, state_changed_at = ? WHERE id = ?",
            (state, n, mean, m2, wins, sn, smean, sm2, ts, changed, sid),
        )
        return state

    def _transition(self, state: str, n: int, mean: float, m2: float) -> tuple[str, str]:
        c = self.cfg
        if state == "EXPERIMENTAL" and n >= c.discovery_min_samples:
            lo, _ = bounds(n, mean, m2, c.discovery_z)
            if lo > 0:
                return "PROBATION", f"discovered: lower bound {lo:+.3f}R over {n} outcomes; needs confirmation"
        elif state == "PROBATION" and n >= c.confirm_min_samples:
            lo, hi = bounds(n, mean, m2, c.confirm_z)
            if lo > 0:
                return "VALIDATED", f"confirmed on {n} fresh outcomes: lower bound {lo:+.3f}R"
            if hi < 0 or n >= 3 * c.confirm_min_samples:
                return "EXPERIMENTAL", f"confirmation failed on {n} fresh outcomes (mean {mean:+.3f}R)"
        elif state == "VALIDATED" and n >= c.retire_min_samples:
            _, hi = bounds(n, mean, m2, c.retire_z)
            if hi < 0 or (n >= 3 * c.retire_min_samples and mean <= 0):
                return "RETIRED", f"stopped working: mean {mean:+.3f}R over {n} outcomes since activation"
        return state, ""

    def _move(self, sid: int, old: str, new: str, ts: str, reason: str) -> tuple[str, str, int, float, float]:
        self.conn.execute(
            "INSERT INTO skill_events(skill_id, at, from_state, to_state, reason) VALUES (?,?,?,?,?)",
            (sid, ts, old, new, reason),
        )
        return new, ts, 0, 0.0, 0.0

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) AS n FROM skills GROUP BY state").fetchall()
        return {s: next((r["n"] for r in rows if r["state"] == s), 0) for s in STATES}

    def validated(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM skills WHERE state = 'VALIDATED' ORDER BY id").fetchall()

    def top(self, limit: int = 20, state: str | None = None) -> list[sqlite3.Row]:
        if state:
            return self.conn.execute(
                "SELECT * FROM skills WHERE state = ? ORDER BY mean_reward DESC, n DESC LIMIT ?", (state, limit)
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM skills ORDER BY CASE state WHEN 'VALIDATED' THEN 0 WHEN 'PROBATION' THEN 1 "
            "WHEN 'EXPERIMENTAL' THEN 2 ELSE 3 END, mean_reward DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def events(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT e.at, e.from_state, e.to_state, e.reason, s.technique, s.symbol, s.timeframe, s.regime, s.session "
            "FROM skill_events e JOIN skills s ON s.id = e.skill_id ORDER BY e.id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def _welford(n: int, mean: float, m2: float, x: float) -> tuple[int, float, float]:
    n += 1
    delta = x - mean
    mean += delta / n
    return n, mean, m2 + delta * (x - mean)
