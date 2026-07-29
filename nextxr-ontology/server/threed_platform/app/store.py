"""store.py — job records + artifact store for the 3-D pipeline.

SPLIT IN TWO, DELIBERATELY
--------------------------
Every job used to be a folder under DATA_DIR/jobs/<job_id>/ — job.json, the
uploaded input, and each stage's artifacts. Transparent and easy to inspect, and
correct for exactly one process. Behind a load balancer it breaks in a way the
user sees immediately: task A runs the reconstruction, the browser polls
GET /api/jobs/<id>, the ALB routes it to task B, and the job "does not exist".
Same for the finished GLB.

So the job now lives in two shared places:

    the RECORD    -> Postgres, table `threed_jobs`  (db/)
    the ARTIFACTS -> the blob store, keys under threed/jobs/<id>/  (storage/)

WHY STAGES STILL WRITE LOCAL FILES
----------------------------------
`job_dir()` and `artifact_dir()` still hand out real local paths, and the stages
still `trimesh.export(...)` straight to them. Mid-pipeline output is genuine
scratch — rewriting eight stages to stream every intermediate mesh through S3
would be slower, much more code, and would break `trimesh`/`PIL` APIs that want a
filename. Instead each artifact is PUBLISHED to the blob store as its stage
finishes, and reads resolve blob-first with the local file as a cache. Local disk
stops being the record and becomes a working directory that any task may discard.

Local dev with nothing configured behaves exactly as before: the blob store's
filesystem backend writes under the same data directory.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

import db
import storage

from .config import settings

_LOCK = threading.RLock()

_STORE = "threed"

#: Blob keys for a job's artifacts all live under this prefix.
KEY_ROOT = "threed/jobs"

#: Columns on threed_jobs, in the order the row dict is built.
_COLS = ("job_id", "status", "stage", "filename", "fields", "stages", "state",
         "error", "created", "updated")


def _now() -> float:
    return round(time.time(), 3)


def blob_key(job_id: str, rel: str) -> str:
    """The blob key for one artifact, e.g. threed/jobs/<id>/artifacts/x/model.glb"""
    return f"{KEY_ROOT}/{job_id}/{str(rel).replace(chr(92), '/').lstrip('/')}"


class JobStore:
    def __init__(self, root: Path | None = None):
        # Local WORKING directory. Not the record any more — see the module
        # docstring. Kept under the same data dir so existing jobs still resolve.
        self.root = (root or settings.data_dir) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        db.schema.ensure(_STORE)

    # ── paths (local scratch) ────────────────────────────────────────────────
    def job_dir(self, job_id: str) -> Path:
        d = self.root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artifact_dir(self, job_id: str, stage: str) -> Path:
        d = self.job_dir(job_id) / "artifacts" / stage
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── blob publish / fetch ─────────────────────────────────────────────────
    def publish(self, job_id: str, rel: str) -> str | None:
        """Copy one local artifact into the blob store. Best-effort: a blob
        failure must not fail a reconstruction that already succeeded — the file
        is still on this task's disk, so the job completes and only cross-task
        fetches degrade."""
        local = self.job_dir(job_id) / rel
        if not local.is_file():
            return None
        try:
            return storage.get_store().put_file(blob_key(job_id, rel), local)
        except Exception:
            return None

    def publish_all(self, job_id: str, rels: list[str]) -> None:
        for rel in rels or []:
            self.publish(job_id, rel)

    def read_artifact(self, job_id: str, rel: str) -> bytes | None:
        """Artifact bytes, wherever they are.

        Blob store first: it is the shared copy, and on any task other than the
        one that ran the job it is the ONLY copy. Falls back to the local file so
        jobs produced before this change, and single-task local runs, still work.
        """
        try:
            return storage.get_store().get(blob_key(job_id, rel))
        except Exception:
            pass
        local = (self.job_dir(job_id) / rel)
        try:
            resolved, base = local.resolve(), self.job_dir(job_id).resolve()
            if resolved.is_file() and str(resolved).startswith(str(base)):
                return resolved.read_bytes()
        except Exception:
            pass
        return None

    def artifact_url(self, job_id: str, rel: str) -> str | None:
        """A presigned URL for the artifact, when the backend offers one — lets
        a browser pull a large GLB straight from S3 instead of through the API."""
        try:
            s = storage.get_store()
            if s.exists(blob_key(job_id, rel)):
                return s.url(blob_key(job_id, rel))
        except Exception:
            pass
        return None

    def save_input(self, job_id: str, filename: str, data: bytes) -> Path:
        """Persist the uploaded file: local (the pipeline reads it by path) and
        blob (so it survives this task)."""
        p = self.job_dir(job_id) / "input" / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.publish(job_id, f"input/{filename}")
        return p

    # ── record (Postgres / SQLite) ───────────────────────────────────────────
    def _row_to_job(self, row) -> dict:
        return {
            "id": row["job_id"],
            "status": row["status"],
            "stage": row["stage"],
            "created": row["created"],
            "updated": row["updated"],
            "filename": row["filename"],
            "fields": db.json_load(row["fields"]) or {},
            "stages": db.json_load(row["stages"]) or [],
            "state": db.json_load(row["state"]) or {},
            "error": row["error"],
        }

    def create(self, filename: str, fields: dict[str, Any]) -> dict:
        job_id = uuid.uuid4().hex[:12]
        (self.job_dir(job_id) / "input").mkdir(parents=True, exist_ok=True)
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

    def load(self, job_id: str) -> dict | None:
        try:
            with db.connect(_STORE) as conn:
                row = conn.execute(
                    "SELECT * FROM threed_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            return self._row_to_job(row) if row else None
        except Exception:
            return None

    def _write(self, job: dict) -> None:
        job["updated"] = _now()
        with db.connect(_STORE) as conn:
            conn.execute(
                "INSERT INTO threed_jobs (job_id, status, stage, filename, "
                "fields, stages, state, error, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (job_id) DO UPDATE SET "
                "status=excluded.status, stage=excluded.stage, "
                "filename=excluded.filename, fields=excluded.fields, "
                "stages=excluded.stages, state=excluded.state, "
                "error=excluded.error, updated=excluded.updated",
                (job["id"], job.get("status", "queued"), job.get("stage"),
                 job.get("filename"), db.Json(job.get("fields") or {}),
                 db.Json(job.get("stages") or []), db.Json(job.get("state") or {}),
                 job.get("error"), job.get("created") or _now(), job["updated"]),
            )

    def _mutate(self, job_id: str, fn) -> dict | None:
        """Read-modify-write one job under a lock.

        A job's stages run on a single task, so contention is mostly the
        pipeline thread versus an API read. The advisory lock covers the rest —
        the upload handler writes state right before handing off to the worker.
        """
        with _LOCK, db.connect(_STORE) as conn:
            conn.lock(f"threed:{job_id}")
            row = conn.execute(
                "SELECT * FROM threed_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = self._row_to_job(row)
            fn(job)
            job["updated"] = _now()
            conn.execute(
                "UPDATE threed_jobs SET status=?, stage=?, filename=?, fields=?, "
                "stages=?, state=?, error=?, updated=? WHERE job_id=?",
                (job.get("status", "queued"), job.get("stage"),
                 job.get("filename"), db.Json(job.get("fields") or {}),
                 db.Json(job.get("stages") or []), db.Json(job.get("state") or {}),
                 job.get("error"), job["updated"], job_id),
            )
            return job

    def update(self, job_id: str, **changes) -> dict:
        job = self._mutate(job_id, lambda j: j.update(changes))
        return job if job is not None else {"id": job_id, **changes}

    def set_state(self, job_id: str, key: str, value: Any) -> None:
        self._mutate(job_id, lambda j: j.setdefault("state", {}).__setitem__(key, value))

    # ── stage records ────────────────────────────────────────────────────────
    def stage_start(self, job_id: str, name: str) -> None:
        def _f(job):
            job["status"] = "running"
            job["stage"] = name
            job.setdefault("stages", []).append(
                {"name": name, "status": "running", "started": _now(),
                 "ms": None, "note": None, "artifacts": []})
        self._mutate(job_id, _f)

    def stage_finish(self, job_id: str, name: str, *, note: str | None = None,
                     artifacts: list[str] | None = None, status: str = "done") -> None:
        # Publish BEFORE recording the stage as finished: a client that sees
        # "done" and immediately fetches an artifact must not race the upload.
        self.publish_all(job_id, artifacts or [])

        def _f(job):
            for rec in reversed(job.get("stages", [])):
                if rec["name"] == name and rec["status"] == "running":
                    rec["status"] = status
                    rec["ms"] = int((_now() - rec["started"]) * 1000)
                    rec["note"] = note
                    rec["artifacts"] = artifacts or []
                    break
        self._mutate(job_id, _f)

    def list_jobs(self, limit: int = 50) -> list[dict]:
        try:
            with db.connect(_STORE) as conn:
                rows = conn.execute(
                    "SELECT job_id, status, stage, filename, created, updated "
                    "FROM threed_jobs ORDER BY updated DESC LIMIT ?", (limit,)
                ).fetchall()
        except Exception:
            return []
        return [{"id": r["job_id"], "status": r["status"], "stage": r["stage"],
                 "filename": r["filename"], "created": r["created"],
                 "updated": r["updated"]} for r in rows]


store = JobStore()
