"""The 3-D generation app.

This package runs in two ways, and both must keep working:

  * mounted inside the main API (`server.main` -> /api/v1/threed), where the
    ontology root is already on sys.path, and
  * standalone from the `threed_platform/` directory — `selftest.py`,
    `selftest_prior.py`, `selftest_drawing.py` and `ingest_priors.py` all do
    `from app.store import store`, where it is NOT.

`store.py` now depends on the platform-level `db` and `storage` packages: job
records live in Postgres and artifacts in the blob store, so the 3-D pipeline is
no longer pinned to one task's local disk. Under the standalone entrypoints those
imports would fail with a bare ModuleNotFoundError, so put the ontology root on
sys.path here, once, for every submodule.
"""
from __future__ import annotations

import sys
from pathlib import Path

# .../nextxr-ontology/server/threed_platform/app/__init__.py -> nextxr-ontology
_ONTOLOGY_ROOT = Path(__file__).resolve().parents[3]
if str(_ONTOLOGY_ROOT) not in sys.path:
    sys.path.insert(0, str(_ONTOLOGY_ROOT))
