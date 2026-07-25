"""Where this service keeps its FILE state on disk. ONE definition, so it can be moved.

WHY THIS EXISTS
---------------
The twin keeps real, user-created state in a `data/` directory next to the code.
Each store used to compute its own path from `__file__`, which silently means
"inside the container image". On a Fargate task that directory is ephemeral: it is
gone the moment the task is replaced. Deploying that as-is would delete the data on
each redeploy — and it would look like the product simply forgot, with no error
anywhere.

Set NXR_DATA_DIR to a mounted volume (EFS access point on ECS) and it persists.
Unset, it resolves to the same path as before, so local dev is unchanged.

WHAT MOVED — AND WHAT THIS DIRECTORY IS NOW
-------------------------------------------
Everything durable has left it:

    records  ->  db/       RDS PostgreSQL 16  (twin registry, change log, agent
                           bundles, checkpoints, scene cache, 3-D job records)
    blobs    ->  storage/  S3                 (generated GLBs, 3-D artifacts)

That is what lifted the "desired count = 1" constraint. SQLite over NFS/EFS is
safe for a single writer and local files are per-task, so the API could never be
scaled out or rolling-deployed. See AWS_DEPLOYMENT.md §7.

This directory is now (a) the local WORKING directory — 3-D pipeline scratch
while a stage runs — and (b) the FALLBACK location both layers use when
NXR_DATABASE_URL / NXR_S3_BUCKET are unset, which is the zero-dependency local
dev default. So the paths below are still the on-disk truth offline, and mean
nothing durable on a correctly-configured deploy.

Because the fallback is silent, a deploy should set NXR_REQUIRE_DB=1 and
NXR_REQUIRE_S3=1; the app then refuses to start rather than quietly writing a
tenant's twins to one task's disk.
"""
from __future__ import annotations

import os
from pathlib import Path

# The repo-local default — identical to what every module computed on its own before.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"

DATA_DIR = Path(os.environ.get("NXR_DATA_DIR") or _DEFAULT_DATA_DIR)


def data_path(*parts: str) -> Path:
    """A path inside the data root, with parent directories created.

    Creating the parent here (rather than at each call site) is deliberate: on a fresh EFS
    mount the directory does not exist yet, and every one of these stores would otherwise
    fail on first write.
    """
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def data_dir(*parts: str) -> Path:
    """A DIRECTORY inside the data root, created if missing."""
    p = DATA_DIR.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
