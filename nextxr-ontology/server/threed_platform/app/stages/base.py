"""base.py — the Stage contract + per-job context.

A Stage is a small, independently-testable unit. It reads/writes the shared
`ctx.state` dict and drops files into its own artifact folder. The orchestrator
records timing, status and artifacts for each one. Keeping every stage behind
this identical interface is what lets us later lift any stage into its own
horizontally-scaled microservice with no rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..store import JobStore


@dataclass
class Ctx:
    job_id: str
    store: JobStore
    job_dir: Path
    state: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def artifacts(self, stage: str) -> Path:
        return self.store.artifact_dir(self.job_id, stage)

    def set(self, key: str, value: Any) -> None:
        self.state[key] = value
        self.store.set_state(self.job_id, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def rel(self, path: Path | str) -> str:
        """Path relative to the job dir — what the API serves to clients."""
        try:
            return str(Path(path).resolve().relative_to(self.job_dir.resolve())).replace("\\", "/")
        except Exception:
            return str(path)


class Stage:
    name: str = "stage"
    optional: bool = False   # if True, failures are warnings, not fatal

    def run(self, ctx: Ctx) -> str | None:
        """Do the work. Return an optional human note. Raise to fail the job."""
        raise NotImplementedError


class StageSkipped(Exception):
    """Raise inside a stage to record a clean skip (e.g. dependency missing)."""
