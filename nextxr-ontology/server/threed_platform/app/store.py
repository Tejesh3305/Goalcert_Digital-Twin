"""store.py — filesystem job + artifact store.

Every job is a folder under DATA_DIR/jobs/<job_id>/ so the whole pipeline is
transparent and inspectable on disk:

    jobs/<id>/
        job.json              # status, per-stage records, shared state
        input/<original>      # the uploaded file
        artifacts/<stage>/... # whatever each stage emits

job.json is the single source of truth the API serves. Writes are guarded by a
per-process lock; that's enough for the single-node monolith and is the seam to
swap for a real DB/queue when we split into services.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import settings

_LOCK = threading.RLock()


def _now() -> float:
    return round(time.time(), 3)


class JobStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = (root or settings.data_dir) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    # ── paths ────────────────────────────────────────────────────────────────
    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _job_file(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def artifact_dir(self, job_id: str, stage: str) -> Path:
        d = self.job_dir(job_id) / "artifacts" / stage
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── lifecycle ────────────────────────────────────────────────────────────
    def create(self, filename: str, fields: dict[str, Any]) -> dict:
        job_id = uuid.uuid4().hex[:12]
        jdir = self.job_dir(job_id)
        (jdir / "input").mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "status": "queued",      # queued | running | done | error
            "stage": None,
            "created": _now(),
            "updated": _now(),
            "filename": filename,
            "fields": fields,
            "stages": [],            # [{name, status, ms, note, artifacts:[...]}]
            "state": {},             # shared key/value passed between stages
            "error": None,
        }
        self._write(job)
        return job

    def load(self, job_id: str) -> Optional[dict]:
        f = self._job_file(job_id)
        if not f.exists():
            return None
        with _LOCK:
            return json.loads(f.read_text(encoding="utf-8"))

    def _write(self, job: dict) -> None:
        job["updated"] = _now()
        f = self._job_file(job["id"])
        f.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            f.write_text(json.dumps(job, indent=2), encoding="utf-8")

    def update(self, job_id: str, **changes) -> dict:
        with _LOCK:
            job = self.load(job_id) or {"id": job_id}
            job.update(changes)
            self._write(job)
            return job

    def set_state(self, job_id: str, key: str, value: Any) -> None:
        with _LOCK:
            job = self.load(job_id)
            if job is None:
                return
            job.setdefault("state", {})[key] = value
            self._write(job)

    # ── stage records ────────────────────────────────────────────────────────
    def stage_start(self, job_id: str, name: str) -> None:
        with _LOCK:
            job = self.load(job_id)
            if job is None:
                return
            job["status"] = "running"
            job["stage"] = name
            job.setdefault("stages", []).append(
                {"name": name, "status": "running", "started": _now(),
                 "ms": None, "note": None, "artifacts": []})
            self._write(job)

    def stage_finish(self, job_id: str, name: str, *, note: str | None = None,
                     artifacts: list[str] | None = None, status: str = "done") -> None:
        with _LOCK:
            job = self.load(job_id)
            if job is None:
                return
            for rec in reversed(job.get("stages", [])):
                if rec["name"] == name and rec["status"] == "running":
                    rec["status"] = status
                    rec["ms"] = int((_now() - rec["started"]) * 1000)
                    rec["note"] = note
                    rec["artifacts"] = artifacts or []
                    break
            self._write(job)

    def list_jobs(self, limit: int = 50) -> list[dict]:
        jobs = []
        for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            j = self.load(d.name)
            if j:
                jobs.append({k: j.get(k) for k in ("id", "status", "stage", "filename", "created", "updated")})
            if len(jobs) >= limit:
                break
        return jobs


store = JobStore()
