"""test_trellis_reconstruct.py — the TRELLIS stage's contract with RunPod.

WHAT THIS FILE EXISTS TO PREVENT
--------------------------------
This is the one stage whose work happens on someone else's machine, on a clock we
do not control, and it was written as if it were a function call. A serverless
endpoint with no always-on worker cold-starts before it can run anything, so a job
routinely sits IN_QUEUE for longer than the client waits. The old code then raised
"RunPod job timed out" and dropped the job id — while RunPod went on to finish the
job and bill for it, leaving a GLB nothing could ever fetch. Retrying paid for the
same GPU minutes twice.

The properties below are what make that impossible, each named after the failure
it prevents:

  id recorded first     the id reaches the job store BEFORE any waiting, so a
                        crash or a timeout still leaves something to resume from
  resume before submit  a re-run collects the finished result instead of starting
                        a second generation
  a blip is not a fail  one dropped TLS connection must not abandon a live job
  terminal is terminal  a FAILED job is not resumed forever

Every test drives the real stage against a fake RunPod. Nothing here touches the
network — the point is the state machine, and a test that needed a live GPU would
never run in CI.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THREED = ROOT / "nextxr-ontology" / "server" / "threed_platform"
if str(THREED) not in sys.path:
    sys.path.insert(0, str(THREED))

# A plausible glTF binary header. `_save_glb_from_output` only has to recognise
# the base64 and write the bytes back out, so this never needs to be a real mesh.
FAKE_GLB = bytes([103, 108, 84, 70, 2, 0, 0, 0]) + bytes(512)
FAKE_GLB_B64 = base64.b64encode(FAKE_GLB).decode()

COMPLETED = {"status": "COMPLETED", "output": {"glb": FAKE_GLB_B64},
             "delayTime": 7498, "executionTime": 126109}


class FakeCtx:
    """The Ctx contract the stage uses. `set` writes through like the real one,
    and records the ORDER, because "before waiting" is the property under test."""

    def __init__(self, tmp_path):
        self.state = {}
        self.written = []
        self._dir = tmp_path

    def artifacts(self, stage):
        d = self._dir / stage
        d.mkdir(parents=True, exist_ok=True)
        return d

    def set(self, key, value):
        self.state[key] = value
        self.written.append(key)

    def get(self, key, default=None):
        return self.state.get(key, default)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeRunPod:
    """A scripted RunPod. `statuses` is what successive /status calls return."""

    def __init__(self, statuses, *, submit_id="job-1", fail_status_calls=0):
        self.statuses = list(statuses)
        self.submit_id = submit_id
        self.fail_status_calls = fail_status_calls
        self.submissions = 0
        self.status_calls = 0
        self.last_payload = None

    def post(self, url, **kw):
        assert url.endswith("/run"), f"expected an async submit, got {url}"
        self.submissions += 1
        self.last_payload = kw.get("json")
        return _Resp({"id": self.submit_id, "status": "IN_QUEUE"})

    def get(self, url, **kw):
        self.status_calls += 1
        if self.status_calls <= self.fail_status_calls:
            raise OSError("TLS connection dropped")
        if self.statuses:
            return _Resp(self.statuses.pop(0))
        return _Resp({"status": "IN_QUEUE"})


def configure(monkeypatch, **overrides):
    """Swap `reconstruct.settings` for a modified copy.

    `Settings` is a FROZEN dataclass — the right call for configuration read all
    over the process, and it means a test replaces the object rather than
    assigning to a field.
    """
    import dataclasses

    from app.stages import reconstruct

    monkeypatch.setattr(reconstruct, "settings",
                        dataclasses.replace(reconstruct.settings, **overrides))


@pytest.fixture
def stage(monkeypatch):
    """The real ReconstructStage, with sleeping and credentials faked."""
    from app.stages import reconstruct

    monkeypatch.setattr(reconstruct.time, "sleep", lambda *_: None)
    monkeypatch.setattr(reconstruct, "POLL_SECONDS", 0)
    # A short wait budget by default: a stage bug that never terminates should
    # fail this suite in seconds rather than spin for the real 900.
    configure(monkeypatch, runpod_api_key="test-key",
              runpod_endpoint_id="test-endpoint", trellis_wait_seconds=3)
    return reconstruct.ReconstructStage()


def wire(monkeypatch, fake):
    from app.stages import reconstruct

    monkeypatch.setattr(reconstruct.requests, "post", fake.post)
    monkeypatch.setattr(reconstruct.requests, "get", fake.get)


def png(tmp_path) -> Path:
    """A real file on disk — the stage base64-encodes whatever path it is given,
    so it has to exist. The contents are never decoded."""
    p = tmp_path / "in.png"
    p.write_bytes(bytes([137, 80, 78, 71, 13, 10, 26, 10]) + bytes(64))
    return p


# ── The happy path ──────────────────────────────────────────────────────


def test_a_completed_job_writes_the_glb(stage, tmp_path, monkeypatch):
    fake = FakeRunPod([{"status": "IN_PROGRESS"}, COMPLETED])
    wire(monkeypatch, fake)
    ctx, out = FakeCtx(tmp_path), tmp_path / "model.glb"

    note = stage._runpod(ctx, png(tmp_path), out, None)

    assert out.read_bytes() == FAKE_GLB
    assert ctx.get("reconstruction") == "trellis@runpod"
    assert "126s" in note, f"the note should carry the real timings: {note}"


def test_the_job_id_is_recorded_before_any_waiting(stage, tmp_path, monkeypatch):
    """THE PROPERTY THE WHOLE FIX RESTS ON. Written after the wait, a timeout
    would still lose it — which is exactly what used to happen."""
    fake = FakeRunPod([COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert "runpod_job_id" in ctx.written
    assert ctx.written.index("runpod_job_id") < ctx.written.index("reconstruction")


# ── Losing patience must not lose the job ───────────────────────────────


def test_a_job_still_queued_leaves_its_id_behind(stage, tmp_path, monkeypatch):
    """Giving up on the WAIT must not give up on the JOB."""
    fake = FakeRunPod([{"status": "IN_QUEUE"}] * 50)
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    with pytest.raises(RuntimeError) as excinfo:
        stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert ctx.get("runpod_job_id") == "job-1", "the id must survive the timeout"
    message = str(excinfo.value)
    assert "job-1" in message
    assert "re-run" in message.lower()
    # It has to name the CAUSE. "Timed out" alone sends an operator to restart a
    # pipeline that will queue behind the very same missing worker.
    assert "IN_QUEUE" in message and "worker" in message


def test_a_re_run_collects_the_earlier_result_instead_of_generating_again(
        stage, tmp_path, monkeypatch):
    """THE REGRESSION. The first attempt ran out of patience; RunPod finished
    anyway. The second must fetch that result, not pay for a second generation."""
    fake = FakeRunPod([COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)
    ctx.set("runpod_job_id", "job-from-the-timed-out-attempt")
    out = tmp_path / "model.glb"

    note = stage._runpod(ctx, png(tmp_path), out, None)

    assert fake.submissions == 0, "it submitted a NEW job instead of resuming"
    assert out.read_bytes() == FAKE_GLB
    assert "resumed" in note


def test_resuming_a_finished_job_does_not_poll_again(stage, tmp_path, monkeypatch):
    """The resume read IS the result. Discarding it and polling afresh would sit
    through another wait cycle holding the answer."""
    fake = FakeRunPod([COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)
    ctx.set("runpod_job_id", "already-finished")

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert fake.status_calls == 1, "it re-polled a job it was already holding"


def test_a_consumed_job_id_is_cleared(stage, tmp_path, monkeypatch):
    """Otherwise the next photo through this job returns the previous one's mesh."""
    fake = FakeRunPod([COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert not ctx.get("runpod_job_id")


# ── A failed status READ is not a failed JOB ────────────────────────────


def test_a_transient_network_error_does_not_abandon_a_running_job(
        stage, tmp_path, monkeypatch):
    """Observed against the live endpoint: one dropped TLS connection mid-poll
    killed a job that was running perfectly well."""
    fake = FakeRunPod([COMPLETED], fail_status_calls=3)
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert (tmp_path / "model.glb").read_bytes() == FAKE_GLB


def test_a_permanently_unreachable_api_still_stops(stage, tmp_path, monkeypatch):
    """Tolerating blips must not become polling a dead endpoint forever."""
    from app.stages import reconstruct

    monkeypatch.setattr(reconstruct, "MAX_POLL_ERRORS", 3)
    fake = FakeRunPod([], fail_status_calls=999)
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    with pytest.raises(RuntimeError, match="Lost contact"):
        stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert ctx.get("runpod_job_id") == "job-1", "still resumable"


# ── Terminal is terminal ────────────────────────────────────────────────


def test_a_failed_job_is_not_resumable(stage, tmp_path, monkeypatch):
    """A FAILED job must not be retried forever — the id is cleared so the next
    attempt generates afresh."""
    fake = FakeRunPod([{"status": "FAILED", "error": "CUDA OOM"}])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    with pytest.raises(RuntimeError, match="FAILED"):
        stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert not ctx.get("runpod_job_id")


def test_a_dead_recorded_job_is_replaced_by_a_fresh_submission(
        stage, tmp_path, monkeypatch):
    """Resuming is for jobs that might still deliver. An id RunPod reports as
    CANCELLED must not block a new attempt."""
    fake = FakeRunPod([{"status": "CANCELLED"}, COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)
    ctx.set("runpod_job_id", "a-dead-job")

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert fake.submissions == 1, "it should have submitted a replacement"
    assert (tmp_path / "model.glb").read_bytes() == FAKE_GLB


# ── The payload ─────────────────────────────────────────────────────────


def test_texture_size_comes_from_configuration(stage, tmp_path, monkeypatch):
    """It was hardcoded at 2048, which quadruples the texture data in a result the
    worker hands back as base64 INSIDE the job payload."""
    configure(monkeypatch, runpod_api_key="k", runpod_endpoint_id="e",
              trellis_texture_size=1024)

    payload = stage._payload(png(tmp_path), None)

    assert payload["input"]["texture_size"] == 1024


def test_the_image_is_sent_under_every_alias_the_worker_might_read(stage, tmp_path):
    payload = stage._payload(png(tmp_path), None)["input"]

    assert payload["image"] == payload["image_base64"] == payload["images"][0]
    assert not payload["image"].startswith("data:"), "raw base64, no data: prefix"


def test_the_job_record_shows_what_runpod_is_doing(stage, tmp_path, monkeypatch):
    """"Queued behind a cold start" and "stuck" look identical without this."""
    fake = FakeRunPod([{"status": "IN_QUEUE"}, {"status": "IN_PROGRESS"}, COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    assert "runpod_status" in ctx.written


# ── Collecting a resumable job over HTTP ────────────────────────────────
#
# Recording the RunPod job id only pays off if something can act on it. Until the
# retry route existed, the stage's own error message ("re-run this job to collect
# the result") was true only for someone with a Python shell on the box.


@pytest.fixture
def threed_client():
    from app.main import app as threed_app
    from fastapi.testclient import TestClient

    with TestClient(threed_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def queued_job(tmp_path):
    """A job record shaped like one whose TRELLIS attempt ran out of patience."""
    from app.store import store

    job = store.create("photo.png", {"route": "object"})
    store.set_state(job["id"], "input_path", str(png(tmp_path)))
    store.set_state(job["id"], "runpod_job_id", "runpod-job-still-running")
    store.update(job["id"], status="error", error="RunPod job ... still IN_QUEUE")
    return job["id"]


def test_retry_names_the_runpod_job_it_will_resume(threed_client, queued_job,
                                                   monkeypatch):
    from app import main as threed_main

    submitted = []
    monkeypatch.setattr(threed_main, "submit", submitted.append)

    resp = threed_client.post(f"/api/jobs/{queued_job}/retry")

    assert resp.status_code == 200, resp.text
    assert resp.json()["resuming_runpod_job"] == "runpod-job-still-running"
    assert submitted == [queued_job], "it did not actually re-queue the job"


def test_retry_refuses_an_unknown_job(threed_client):
    assert threed_client.post("/api/jobs/no-such-job/retry").status_code == 404


def test_retry_refuses_a_job_that_is_still_running(threed_client, queued_job):
    from app.store import store

    store.update(queued_job, status="running")

    resp = threed_client.post(f"/api/jobs/{queued_job}/retry")

    assert resp.status_code == 409


def test_the_output_is_recorded_for_diagnosis(stage, tmp_path, monkeypatch):
    """`runpod_output.json` is what an operator reads when the worker returns
    something unexpected, so the SHAPE of the response is kept."""
    fake = FakeRunPod([COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    recorded = json.loads(
        (tmp_path / "reconstruct" / "runpod_output.json").read_text())
    assert recorded["status"] == "COMPLETED"
    assert recorded["executionTime"] == 126109
    assert "glb" in recorded["output"], "which key carried the model still matters"


def test_the_diagnostic_file_does_not_duplicate_the_mesh(stage, tmp_path,
                                                         monkeypatch):
    """The response was written verbatim, so a 1.34 MB GLB was stored a second
    time as base64 — a 1.8 MB JSON file beside the model.glb that already has it,
    on every single job."""
    fake = FakeRunPod([COMPLETED])
    wire(monkeypatch, fake)
    ctx = FakeCtx(tmp_path)

    stage._runpod(ctx, png(tmp_path), tmp_path / "model.glb", None)

    text = (tmp_path / "reconstruct" / "runpod_output.json").read_text()
    assert FAKE_GLB_B64 not in text
    assert "elided" in text, "it should say the payload was dropped, not just drop it"
    # The mesh itself is still written, in the one place it belongs.
    assert (tmp_path / "model.glb").read_bytes() == FAKE_GLB
