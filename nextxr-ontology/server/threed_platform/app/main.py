"""main.py — FastAPI gateway for the 3D platform.

Endpoints:
  GET  /api/health
  POST /api/jobs                 multipart: file=<image/pdf> [+ route, facility, ...]
  GET  /api/jobs                 recent jobs
  GET  /api/jobs/{id}            full job record (status, per-stage, state)
  GET  /api/jobs/{id}/file/{path}  fetch any artifact (e.g. artifacts/export/model.glb)
  GET  /api/jobs/{id}/result     redirect to the primary GLB (object route)

Static web UI is served at /.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import storage

from .config import settings
from .orchestrator import submit
from .priors.library import library
from .store import store

app = FastAPI(title="NextXR · 3D Generation Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

WEB = Path(__file__).resolve().parent.parent / "web"


@app.get("/api/health")
def health():
    lib = library.list()
    return {"ok": True, "trellis": settings.has_trellis,
            "provider": settings.provider(),
            "drawing_backend": settings.two_d_to_3d_url or "in-process import",
            "prior": {"enabled": settings.prior_enabled, "method": lib["method"],
                      "count": lib["count"],
                      "retrieve_threshold": settings.prior_retrieve_threshold,
                      "hint_threshold": settings.prior_hint_threshold},
            "data_dir": str(settings.data_dir)}


@app.get("/api/priors")
def list_priors():
    return library.list()


@app.post("/api/priors")
async def add_prior(
    file: UploadFile = File(...),
    asset_type: str = Form(...),
    mesh: UploadFile | None = File(None),
    meta: str = Form(""),
):
    tmp = settings.data_dir / "_ingest"
    tmp.mkdir(parents=True, exist_ok=True)
    ref_path = tmp / (file.filename or "ref.png")
    ref_path.write_bytes(await file.read())
    mesh_path = None
    if mesh is not None and mesh.filename:
        mesh_path = tmp / mesh.filename
        mesh_path.write_bytes(await mesh.read())
    meta_obj = {}
    if meta:
        try:
            import json as _json
            meta_obj = _json.loads(meta)
        except Exception:
            meta_obj = {"note": meta}
    item = library.add(ref_path, asset_type, mesh_path, meta_obj)
    return {"added": item, "count": library.list()["count"]}


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    route: str = Form(""),
    facility: str = Form(""),
    floors: int = Form(1),
    object_type: str = Form(""),
    asset_id: str = Form(""),
    material: str = Form(""),
):
    if not file.filename:
        raise HTTPException(400, "no file")
    fields = {k: v for k, v in {
        "route": route, "facility": facility, "floors": floors,
        "object_type": object_type, "asset_id": asset_id, "material": material,
    }.items() if v not in ("", None)}

    job = store.create(file.filename, fields)
    in_path = store.save_input(job["id"], file.filename, await file.read())
    store.set_state(job["id"], "input_path", str(in_path))
    store.set_state(job["id"], "filename", file.filename)
    store.set_state(job["id"], "fields", fields)
    submit(job["id"])
    return {"job_id": job["id"], "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": store.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.load(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job


@app.get("/api/jobs/{job_id}/file/{path:path}")
def get_file(job_id: str, path: str):
    """Serve an artifact from the blob store, falling back to local disk.

    Was a FileResponse off this task's disk, which 404s on every task except the
    one that ran the job. `read_artifact` resolves blob-first; traversal outside
    the job's prefix is rejected by the blob key validator and by the local path
    check behind it.
    """
    try:
        data = store.read_artifact(job_id, path)
    except ValueError:
        raise HTTPException(400, "invalid path")
    if data is None:
        raise HTTPException(404, "no such file")
    return Response(content=data,
                    media_type=storage.content_type_for(path),
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/jobs/{job_id}/result")
def get_result(job_id: str, request: Request):
    job = store.load(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    glb = (job.get("state", {}) or {}).get("result_glb")
    if not glb:
        raise HTTPException(404, "no GLB result (drawing route renders in the 2d-to-3d viewer)")
    # Prefer a presigned S3 URL: a GLB is tens of megabytes, and streaming it
    # through the API task wastes a worker for the whole download. Falls back to
    # the artifact route when the backend has no URLs (local filesystem).
    direct = store.artifact_url(job_id, glb)
    if direct:
        return RedirectResponse(direct)
    # Honour the mount prefix (root_path) so the redirect works whether this app
    # is standalone ("") or mounted in the main server ("/api/v1/threed").
    root = request.scope.get("root_path", "")
    return RedirectResponse(f"{root}/api/jobs/{job_id}/file/{glb}")


if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
