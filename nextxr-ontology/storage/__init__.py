"""storage — the twin's blob store.

S3 in production, local filesystem in dev. One import surface:

    import storage

    storage.get_store().put("threed/jobs/abc/model.glb", data)
    storage.get_store().get("threed/jobs/abc/model.glb")

See `storage/core.py` for the key rules and why blobs had to leave the task's
local disk. Companion to `db/` (relational state) — together they are what makes
an ECS task stateless.
"""
from __future__ import annotations

from .core import (  # noqa: F401
    LOCAL,
    S3,
    BlobNotFound,
    LocalObjectStore,
    ObjectStore,
    S3ObjectStore,
    backend,
    bucket,
    content_type_for,
    get_store,
    info,
    is_s3,
    key_prefix,
    local_root,
    log_posture,
    ping,
    reset_store,
)

__all__ = [
    "LOCAL", "S3", "BlobNotFound", "LocalObjectStore", "ObjectStore",
    "S3ObjectStore", "backend", "bucket", "content_type_for", "get_store",
    "info", "is_s3", "key_prefix", "local_root", "log_posture", "ping",
    "reset_store",
]
