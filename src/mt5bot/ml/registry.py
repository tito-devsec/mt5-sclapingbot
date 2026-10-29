"""Versioned skops model registry with SHA-256 and type allow-list checks, promotion and rollback."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from mt5bot.core.errors import ModelIntegrityError, ModelUnavailableError
from mt5bot.ml.models import library_versions
from mt5bot.ml.training import ConformalAbstainer, FittedMetaModel
from mt5bot.storage.db import transaction
from mt5bot.storage.repositories import ModelRepository, iso, now_iso

TRUSTED_PREFIXES = (
    "sklearn.",
    "numpy.",
    "lightgbm.",
    "xgboost.",
    "catboost.",
    "scipy.",
    "collections.OrderedDict",
    "builtins.",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class GateResult:
    passed: bool
    gates: dict[str, dict[str, Any]]

    def reason(self) -> str:
        failed = [k for k, v in self.gates.items() if not v["passed"]]
        return "all gates passed" if not failed else "failed gates: " + ", ".join(failed)


class ModelRegistry:
    def __init__(self, conn: sqlite3.Connection, model_dir: Path) -> None:
        self.conn = conn
        self.repo = ModelRepository(conn)
        self.dir = Path(model_dir)

    # -- persistence ------------------------------------------------------
    def register(
        self,
        model: FittedMetaModel,
        *,
        kind: str,
        feature_set: str,
        train_start: pd.Timestamp,
        train_end: pd.Timestamp,
        dataset_fingerprint: str,
        metrics: dict[str, Any],
        experiment_id: int | None = None,
    ) -> str:
        import skops.io as sio

        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%S%fZ")
        model_id = f"{kind}-{model.model_type}-{stamp}-{dataset_fingerprint[:6]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        final = self.dir / f"{model_id}.skops"
        payload = {
            "estimator": model.estimator,
            "conformal_q": model.conformal.q_,
            "conformal_confidence": model.conformal.confidence,
        }
        # Write to a temp file, then rename atomically: a failed run never leaves a partial artifact.
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        os.close(fd)
        try:
            sio.dump(payload, tmp)
            digest = _sha256(Path(tmp))
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.repo.add(
            {
                "id": model_id,
                "kind": kind,
                "model_type": model.model_type,
                "artifact_path": str(final),
                "artifact_sha256": digest,
                "feature_set": feature_set,
                "feature_names": json.dumps(model.feature_names),
                "train_start": iso(train_start),
                "train_end": iso(train_end),
                "dataset_fingerprint": dataset_fingerprint,
                "params_json": json.dumps({"params": model.params, "threshold": model.threshold}),
                "metrics_json": json.dumps(metrics, default=_json_default),
                "seed": model.seed,
                "library_versions": json.dumps(library_versions()),
                "status": "CHALLENGER",
                "created_at": now_iso(),
                "experiment_id": experiment_id,
            }
        )
        return model_id

    def load(self, model_id: str) -> FittedMetaModel:
        import skops.io as sio

        row = self.repo.get(model_id)
        if row is None:
            raise ModelUnavailableError(f"model {model_id} is not registered")
        path = Path(row["artifact_path"])
        if not path.exists():
            raise ModelUnavailableError(f"artifact for {model_id} is missing")
        if _sha256(path) != row["artifact_sha256"]:
            raise ModelIntegrityError(f"artifact for {model_id} does not match its recorded SHA-256")
        untrusted = sio.get_untrusted_types(file=path)
        bad = [t for t in untrusted if not t.startswith(TRUSTED_PREFIXES)]
        if bad:
            raise ModelIntegrityError(f"artifact for {model_id} contains non-allow-listed types: {bad}")
        payload = sio.load(path, trusted=untrusted)
        conf = ConformalAbstainer(payload["conformal_confidence"])
        conf.q_ = payload["conformal_q"]
        params = json.loads(row["params_json"])
        return FittedMetaModel(
            row["model_type"],
            payload["estimator"],
            conf,
            json.loads(row["feature_names"]),
            params["threshold"],
            params["params"],
            int(row["seed"]),
        )

    def champion(self, kind: str) -> tuple[str, FittedMetaModel] | None:
        row = self.repo.champion(kind)
        if row is None:
            return None
        return row["id"], self.load(row["id"])

    # -- promotion --------------------------------------------------------
    def decide(self, challenger_id: str, kind: str, gates: GateResult) -> str:
        """Promote if every gate passed, otherwise reject. Returns the action taken."""
        current = self.repo.champion(kind)
        with transaction(self.conn):
            if gates.passed:
                if current is not None:
                    self.repo.set_status(current["id"], "RETIRED")
                self.repo.set_status(challenger_id, "CHAMPION")
                action = "PROMOTE"
            else:
                self.repo.set_status(challenger_id, "REJECTED")
                action = "REJECT"
            self.repo.record_promotion(
                kind=kind,
                action=action,
                model_id=challenger_id,
                previous=current["id"] if current else None,
                reason=gates.reason(),
                gates=gates.gates,
            )
        return action

    def rollback(self, kind: str) -> str:
        """Restore the champion that preceded the most recent promotion."""
        promos = [p for p in self.repo.promotions(kind) if p["action"] == "PROMOTE"]
        if not promos:
            raise ModelUnavailableError("no promotion to roll back")
        last = promos[0]
        prev = last["previous_champion"]
        if prev is None:
            raise ModelUnavailableError("the current champion has no predecessor; nothing to roll back to")
        self.load(prev)  # verify integrity before switching
        with transaction(self.conn):
            self.repo.set_status(last["model_id"], "RETIRED")
            self.repo.set_status(prev, "CHAMPION")
            self.repo.record_promotion(
                kind=kind,
                action="ROLLBACK",
                model_id=prev,
                previous=last["model_id"],
                reason=f"rollback from {last['model_id']}",
                gates={},
            )
        return prev


def _json_default(o: Any) -> Any:
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)
