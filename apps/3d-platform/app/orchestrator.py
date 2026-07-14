"""orchestrator.py — runs a job's pipeline, stage by stage.

For the monolith this is a thread-pool worker; the message-queue + per-stage
microservices arrive later by swapping this loop for a queue consumer. The stage
contract and job record stay identical, so nothing else changes.
"""
from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .pipeline import COMMON, for_route
from .stages.base import Ctx, StageSkipped
from .store import store

_POOL = ThreadPoolExecutor(max_workers=2)


def submit(job_id: str) -> None:
    _POOL.submit(_run, job_id)


def _run(job_id: str) -> None:
    job = store.load(job_id)
    if job is None:
        return
    ctx = Ctx(job_id=job_id, store=store, job_dir=Path(store.job_dir(job_id)),
              state=dict(job.get("state", {})))
    ctx.state.setdefault("fields", job.get("fields", {}))
    ctx.state.setdefault("filename", job.get("filename"))

    try:
        # COMMON sets the route; then continue with the route-specific stages.
        for stage in COMMON:
            _run_stage(ctx, stage)
        for stage in for_route(ctx.get("route", "object")):
            _run_stage(ctx, stage)
        store.update(job_id, status="done", stage=None)
    except Exception as e:
        store.update(job_id, status="error", error=str(e))
        (Path(store.job_dir(job_id)) / "error.log").write_text(
            traceback.format_exc(), encoding="utf-8")


def _run_stage(ctx: Ctx, stage) -> None:
    store.stage_start(ctx.job_id, stage.name)
    try:
        note = stage.run(ctx)
        artifacts = _artifact_list(ctx, stage.name)
        store.stage_finish(ctx.job_id, stage.name, note=note, artifacts=artifacts, status="done")
    except StageSkipped as s:
        store.stage_finish(ctx.job_id, stage.name, note=f"skipped: {s}", status="skipped")
    except Exception as e:
        if getattr(stage, "optional", False):
            store.stage_finish(ctx.job_id, stage.name, note=f"optional failed: {e}", status="skipped")
            return
        store.stage_finish(ctx.job_id, stage.name, note=f"error: {e}", status="error")
        raise


def _artifact_list(ctx: Ctx, stage: str) -> list[str]:
    d = ctx.store.artifact_dir(ctx.job_id, stage)
    out = []
    for p in sorted(d.glob("**/*")):
        if p.is_file():
            out.append(ctx.rel(p))
    return out
