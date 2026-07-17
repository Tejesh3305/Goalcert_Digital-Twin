"""Where this service keeps its state on disk. ONE definition, so it can be moved.

WHY THIS EXISTS
---------------
The twin keeps real, user-created state in a `data/` directory next to the code:

    data/twins.db              the TWIN REGISTRY — every twin a user has created
    data/changelog.db          the governance/change log
    data/bundles.db            published agent bundles
    data/agent_checkpoints.db  langgraph checkpoints
    data/track3_gate.db        gate events
    data/scenes/               reconstructed 3-D models from Build-a-Twin

Each of those used to compute its own path from `__file__`, which silently means
"inside the container image". On a Fargate task that directory is ephemeral: it is gone
the moment the task is replaced. Deploying that as-is would delete every twin, every
reconstructed model and the entire change log on each redeploy — and it would look like
the product simply forgot, with no error anywhere.

Set NXR_DATA_DIR to a mounted volume (EFS access point on ECS) and all of it persists.
Unset, it resolves to the same path as before, so local dev and the existing checked-in
data are unchanged.

NOTE: these are SQLite files. SQLite over NFS/EFS is safe for ONE writer; it is not safe
to run several twin tasks against the same EFS mount. Keep the twin service at desired
count 1 until this state moves to RDS. See AWS_DEPLOYMENT.md §7.
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
