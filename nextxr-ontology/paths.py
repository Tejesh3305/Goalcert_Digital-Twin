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

WHAT IS STILL HERE — AND WHAT MOVED
-----------------------------------
The five SQLite stores that used to live here (`twins.db`, `changelog.db`,
`bundles.db`, `agent_checkpoints.db`, `track3_gate.db`) now live in the shared
relational store, `db/` — RDS PostgreSQL 16 in production. That is what lifted the
"desired count = 1" constraint: SQLite over NFS/EFS is safe for a single writer, so
the API could never be scaled out or rolling-deployed. See AWS_DEPLOYMENT.md §7.

In local dev with no NXR_DATABASE_URL set, `db/` still writes SQLite files under
this same directory, so the paths below remain the on-disk truth offline.

What genuinely remains file state:

    data/scenes/    reconstructed 3-D models (mirrored into the DB scene cache,
                    which is what makes them correct across tasks)

Blobs — the generated GLBs under DATA_DIR (the 3-D platform's own variable) — are
still on the volume. Moving those to S3 is the remaining step for a fully
stateless task; the relational move does not depend on it.
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
