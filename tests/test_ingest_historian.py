"""
test_ingest_historian.py — the real-data path, end to end.

This is the capability the platform did not have: an endpoint that accepts a
measurement, a store to put it in, and a way to read it back. Everything the UI
rendered before came from `dynamics/` — a simulator, not a twin.

The tests below are ordered as the pipeline runs: device identity, then ingest,
then idempotency, then read-back, then the isolation properties. Behaviour
evaluation is disabled for most of them (`NXR_INGEST_BEHAVIOURS=0`) because it
needs Neo4j; the one test that asserts on it checks that its ABSENCE is reported
rather than silently swallowed, which is the property that matters when a rule
stops firing in production.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import KEY_ACME, KEY_ADMIN, KEY_READER, hdr

TENANT = "acme"
OTHER = "globex"
ASSET = "ahu-01"
SIGNAL = "hvac:AirTemperature"


@pytest.fixture(scope="module", autouse=True)
def _no_behaviours(monkeypatch_session=None):
    """Behaviour evaluation needs Neo4j. Off for this module so these tests
    exercise ingest + historian in isolation; `test_behaviour_failure_is_reported`
    turns it back on deliberately."""
    import os
    previous = os.environ.get("NXR_INGEST_BEHAVIOURS")
    os.environ["NXR_INGEST_BEHAVIOURS"] = "0"
    yield
    if previous is None:
        os.environ.pop("NXR_INGEST_BEHAVIOURS", None)
    else:
        os.environ["NXR_INGEST_BEHAVIOURS"] = previous


@pytest.fixture(scope="module")
def historian_ready(api):
    import historian
    report = historian.ensure()
    assert not report["failures"], report["failures"]
    return report


def _hour_aligned(hours_ago: int) -> datetime:
    """A timestamp on an exact hour boundary, `hours_ago` in the past.

    Tests that assert on 1h ROLLUP CONTENTS must anchor here rather than to a bare
    `now`. Anchoring to now makes the result depend on where in the hour the suite
    happens to run: at 07:59 a five-sample 30-second series straddles two buckets and
    the assertion fails, and at 07:05 it does not. That is a flaky test, and one whose
    failure looks exactly like a real aggregation bug.
    """
    now = datetime.now(UTC)
    return (now.replace(minute=0, second=0, microsecond=0)
            - timedelta(hours=hours_ago))


def _samples(n: int, *, start: datetime, step_s: int = 30,
             asset: str = ASSET, signal: str = SIGNAL,
             quality: int = 192) -> list[dict]:
    return [{
        "asset_id": asset, "signal": signal,
        "value": 20.0 + (i % 40) * 0.25,
        "ts": (start + timedelta(seconds=i * step_s)).isoformat(),
        "unit": "DEG_C", "quality": quality,
    } for i in range(n)]


@pytest.fixture(scope="module")
def device(api, historian_ready):
    """A registered device plus its one-time token."""
    resp = api.post("/api/v1/ingest/devices",
                    json={"tenant": TENANT, "name": "Plant Gateway",
                          "device_id": "gw-test-1"},
                    headers=hdr(KEY_ADMIN))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token"].startswith("nxrd_"), body["token"][:12]
    return body["device"], body["token"]


# ── Device identity ────────────────────────────────────────────────────────


def test_device_token_is_returned_once_and_never_again(api, device):
    """The plaintext is unrecoverable by design. A registry an operator can read
    back is a registry an attacker can read once."""
    dev, token = device
    listing = api.get("/api/v1/ingest/devices", headers=hdr(KEY_ADMIN)).json()
    row = next(d for d in listing["devices"] if d["device_id"] == dev["device_id"])
    serialised = json.dumps(listing)
    assert "token" not in row
    assert "token_hash" not in row
    assert token not in serialised, "the plaintext token leaked into the listing"


def test_ingest_requires_a_credential(api, historian_ready):
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"tenant": TENANT, "samples": _samples(
                        1, start=datetime.now(UTC))})
    assert resp.status_code == 401


def test_bad_device_token_is_rejected_indistinguishably(api, historian_ready):
    """Unknown, disabled, expired and malformed tokens must all produce the SAME
    message. A distinguishing error confirms to whoever holds stolen hardware that
    a token is real, which is the one fact worth protecting here.

    Includes the empty-string case: a client that sends the header is attempting
    device auth, so it must fail as a device token rather than as a missing
    API key.
    """
    api.post("/api/v1/ingest/devices",
             json={"tenant": TENANT, "device_id": "gw-indist-disabled"},
             headers=hdr(KEY_ADMIN))
    disabled = api.post("/api/v1/ingest/devices",
                        json={"tenant": TENANT, "device_id": "gw-indist-2"},
                        headers=hdr(KEY_ADMIN)).json()["token"]
    api.post("/api/v1/ingest/devices/gw-indist-2/enabled",
             json={"enabled": False}, headers=hdr(KEY_ADMIN))

    expired = api.post("/api/v1/ingest/devices",
                       json={"tenant": TENANT, "device_id": "gw-indist-exp",
                             "ttl_days": -1},
                       headers=hdr(KEY_ADMIN)).json()["token"]

    statuses, messages = set(), set()
    for token in ("nxrd_totally-made-up", "", "not-even-prefixed",
                  "nxrd_" + "a" * 43, disabled, expired):
        resp = api.post("/api/v1/ingest/telemetry",
                        json={"samples": _samples(
                            1, start=datetime.now(UTC))},
                        headers={"X-Device-Token": token})
        statuses.add(resp.status_code)
        messages.add(resp.json().get("detail"))

    assert statuses == {401}, f"status codes distinguish token states: {statuses}"
    assert len(messages) == 1, f"messages distinguish token states: {messages}"


def test_read_only_key_cannot_ingest(api, historian_ready):
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"tenant": TENANT,
                          "samples": _samples(1, start=datetime.now(UTC))},
                    headers=hdr(KEY_READER))
    assert resp.status_code == 403, resp.text


# ── Ingest ─────────────────────────────────────────────────────────────────


def test_device_ingest_stores_measurements(api, device):
    _, token = device
    start = datetime.now(UTC) - timedelta(hours=2)
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"samples": _samples(120, start=start)},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant"] == TENANT, "device token must establish its own tenant"
    assert body["accepted"] == 120
    assert body["rejected"] == 0


def test_device_cannot_write_into_another_tenant(api, device):
    """A device's tenant comes from its credential, never from the payload.

    Naming a foreign tenant is refused with a 403 rather than silently ignored:
    ignoring the field would leave a misconfigured gateway convinced it was
    writing to one twin while its data landed in another, which is far harder to
    diagnose than an immediate rejection.
    """
    _, token = device
    start = datetime.now(UTC) - timedelta(hours=5)
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"tenant": OTHER, "samples": _samples(3, start=start)},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 403, resp.text

    leaked = api.get(f"/api/v1/twins/{OTHER}/history/stats",
                     headers=hdr(KEY_ADMIN)).json()
    assert leaked.get("samples", 0) == 0, \
        "a device wrote into a tenant it does not belong to"


def test_device_may_name_its_own_tenant(api, device):
    """Naming the device's OWN tenant is harmless and must still work — a
    gateway config that sets it explicitly should not break."""
    _, token = device
    start = datetime.now(UTC) - timedelta(hours=7)
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"tenant": TENANT,
                          "samples": _samples(3, start=start,
                                              signal="hvac:OwnTenant")},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"] == TENANT


def test_replay_is_idempotent(api, device):
    """An edge agent that reconnects resends its buffer. That must not
    double-count, and the guarantee must not depend on the client remembering an
    Idempotency-Key header."""
    _, token = device
    start = datetime.now(UTC) - timedelta(hours=8)
    batch = {"samples": _samples(50, start=start)}

    first = api.post("/api/v1/ingest/telemetry", json=batch,
                     headers={"X-Device-Token": token}).json()
    second = api.post("/api/v1/ingest/telemetry", json=batch,
                      headers={"X-Device-Token": token}).json()

    assert first["accepted"] == 50 and first["duplicates"] == 0
    assert second["accepted"] == 0 and second["duplicates"] == 50


def test_partial_batch_returns_207_and_keeps_good_samples(api, device):
    """One bad sample must not discard the rest, and the response must say which
    one failed. A 400 for the whole batch loses 499 good readings and teaches the
    operator nothing."""
    _, token = device
    start = datetime.now(UTC) - timedelta(hours=12)
    good = _samples(9, start=start)
    bad = [{"asset_id": ASSET, "signal": SIGNAL, "value": 1.0,
            "ts": (datetime.now(UTC) + timedelta(days=3)).isoformat()}]

    resp = api.post("/api/v1/ingest/telemetry", json={"samples": good + bad},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["accepted"] == 9
    assert body["rejected"] == 1
    assert "future" in body["rejections"][0]["reason"]


def test_asset_prefix_scoped_device_cannot_write_outside_it(api, historian_ready):
    """One gateway per plant: a compromised device in one building must not be
    able to fabricate readings for another."""
    created = api.post("/api/v1/ingest/devices",
                       json={"tenant": TENANT, "device_id": "gw-line-a",
                             "asset_prefix": "line-a-"},
                       headers=hdr(KEY_ADMIN)).json()
    token = created["token"]
    start = datetime.now(UTC) - timedelta(hours=3)

    allowed = _samples(2, start=start, asset="line-a-pump-1")
    forbidden = _samples(2, start=start, asset="line-b-pump-9")
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"samples": allowed + forbidden},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 2
    assert all("line-a-" in r["reason"] for r in body["rejections"])


def test_rotate_invalidates_the_previous_token(api, historian_ready):
    created = api.post("/api/v1/ingest/devices",
                       json={"tenant": TENANT, "device_id": "gw-rotate"},
                       headers=hdr(KEY_ADMIN)).json()
    old = created["token"]
    new = api.post("/api/v1/ingest/devices/gw-rotate/rotate",
                   headers=hdr(KEY_ADMIN)).json()["token"]
    assert new != old

    start = datetime.now(UTC) - timedelta(hours=4)
    assert api.post("/api/v1/ingest/telemetry",
                    json={"samples": _samples(1, start=start)},
                    headers={"X-Device-Token": old}).status_code == 401
    assert api.post("/api/v1/ingest/telemetry",
                    json={"samples": _samples(1, start=start)},
                    headers={"X-Device-Token": new}).status_code == 200


def test_disabled_device_cannot_ingest(api, historian_ready):
    created = api.post("/api/v1/ingest/devices",
                       json={"tenant": TENANT, "device_id": "gw-disable"},
                       headers=hdr(KEY_ADMIN)).json()
    token = created["token"]
    api.post("/api/v1/ingest/devices/gw-disable/enabled",
             json={"enabled": False}, headers=hdr(KEY_ADMIN))
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"samples": _samples(
                        1, start=datetime.now(UTC) - timedelta(hours=1))},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 401


def test_bulk_ndjson_gzipped(api, device):
    """Backfill: NDJSON so it streams and survives truncation at a known
    boundary, gzip because telemetry compresses ~10x."""
    _, token = device
    start = datetime.now(UTC) - timedelta(days=2)
    lines = "\n".join(json.dumps(s) for s in
                      _samples(200, start=start, signal="hvac:ReturnTemp"))
    resp = api.post("/api/v1/ingest/telemetry/bulk",
                    content=gzip.compress(lines.encode()),
                    headers={"X-Device-Token": token,
                             "Content-Encoding": "gzip",
                             "Content-Type": "application/x-ndjson"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 200


def test_bulk_reports_malformed_lines_by_line_number(api, device):
    _, token = device
    start = datetime.now(UTC) - timedelta(days=3)
    good = [json.dumps(s) for s in _samples(2, start=start, signal="hvac:Bulk2")]
    payload = "\n".join([good[0], "{not json", good[1], "[]"])
    resp = api.post("/api/v1/ingest/telemetry/bulk", content=payload.encode(),
                    headers={"X-Device-Token": token,
                             "Content-Type": "application/x-ndjson"})
    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 2
    assert any("line 2" in r["reason"] for r in body["rejections"])


def test_oversized_batch_is_refused_with_guidance(api, device):
    _, token = device
    start = datetime.now(UTC) - timedelta(days=1)
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"samples": _samples(10_001, start=start, step_s=1)},
                    headers={"X-Device-Token": token})
    assert resp.status_code == 413
    assert "bulk" in resp.json()["detail"]


# ── Read back ──────────────────────────────────────────────────────────────


def test_history_returns_what_was_ingested(api, device):
    _, token = device
    start = datetime.now(UTC) - timedelta(minutes=40)
    api.post("/api/v1/ingest/telemetry",
             json={"samples": _samples(40, start=start, signal="hvac:ReadBack")},
             headers={"X-Device-Token": token})

    resp = api.get(f"/api/v1/entities/{ASSET}/history",
                   params={"tenant": TENANT, "signal": "hvac:ReadBack",
                           "from": "-1h", "agg": "raw"},
                   headers=hdr(KEY_ACME))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["source"] == "measured", \
        "history must be distinguishable from the simulated telemetry endpoint"
    assert body["count"] == 40
    assert body["unit"] == "DEG_C"
    assert body["points"][0]["quality"] == 192


def test_relative_time_shorthand(api, device):
    for window in ("-30m", "-1h", "-7d"):
        resp = api.get(f"/api/v1/entities/{ASSET}/history",
                       params={"tenant": TENANT, "signal": SIGNAL,
                               "from": window, "to": "now"},
                       headers=hdr(KEY_ACME))
        assert resp.status_code == 200, f"{window}: {resp.text}"


def test_unparseable_time_is_a_400_with_examples(api, device):
    resp = api.get(f"/api/v1/entities/{ASSET}/history",
                   params={"tenant": TENANT, "signal": SIGNAL,
                           "from": "last tuesday"},
                   headers=hdr(KEY_ACME))
    assert resp.status_code == 400
    assert "-24h" in resp.json()["detail"]


def test_auto_agg_reports_the_tier_it_chose(api, device):
    """Silent downsampling is a trap — an operator comparing two windows would
    see different numbers with nothing to explain why."""
    resp = api.get(f"/api/v1/entities/{ASSET}/history",
                   params={"tenant": TENANT, "signal": SIGNAL,
                           "from": "-30d", "agg": "auto"},
                   headers=hdr(KEY_ACME)).json()
    assert resp["agg"] in ("1m", "1h", "1d")
    assert any("agg=auto resolved" in n for n in resp["notes"])


def test_rollup_average_is_sum_over_count(api, device):
    """`avg(avg(x))` weights buckets equally regardless of sample count, which
    skews every chart drawn from a coarser tier. The rollup therefore stores
    count and sum, and the mean is derived."""
    _, token = device
    start = _hour_aligned(3)
    # 10 samples of 10.0 in minute 0; 1 sample of 100.0 in minute 1.
    rows = [{"asset_id": "avg-test", "signal": "t", "value": 10.0, "unit": "x",
             "ts": (start + timedelta(seconds=i)).isoformat()} for i in range(10)]
    rows.append({"asset_id": "avg-test", "signal": "t", "value": 100.0,
                 "unit": "x", "ts": (start + timedelta(minutes=1)).isoformat()})
    api.post("/api/v1/ingest/telemetry", json={"samples": rows},
             headers={"X-Device-Token": token})

    hourly = api.get("/api/v1/entities/avg-test/history",
                     params={"tenant": TENANT, "signal": "t",
                             "from": start.isoformat(),
                             "to": (start + timedelta(minutes=5)).isoformat(),
                             "agg": "1h"},
                     headers=hdr(KEY_ACME)).json()
    bucket = hourly["points"][0]
    assert bucket["count"] == 11
    # Correct weighted mean: (10*10 + 100) / 11 = 18.18…
    # A naive avg-of-minute-averages would give (10 + 100) / 2 = 55.
    assert abs(bucket["value"] - 200 / 11) < 1e-6, bucket
    assert bucket["max"] == 100.0 and bucket["min"] == 10.0


def test_bad_quality_is_excluded_from_statistics_but_counted(api, device):
    """A sensor reading 0 with a broken wire is not the same fact as a sensor
    reading 0. Averaging in a dead sensor's zeros is how a dashboard lies."""
    _, token = device
    start = _hour_aligned(6)
    rows = _samples(5, start=start, asset="q-test", signal="t")
    rows += [{"asset_id": "q-test", "signal": "t", "value": 0.0, "unit": "DEG_C",
              "quality": 0,
              "ts": (start + timedelta(seconds=300 + i)).isoformat()}
             for i in range(20)]
    api.post("/api/v1/ingest/telemetry", json={"samples": rows},
             headers={"X-Device-Token": token})

    hourly = api.get("/api/v1/entities/q-test/history",
                     params={"tenant": TENANT, "signal": "t",
                             "from": start.isoformat(),
                             "to": (start + timedelta(minutes=30)).isoformat(),
                             "agg": "1h"},
                     headers=hdr(KEY_ACME)).json()
    bucket = hourly["points"][0]
    assert bucket["count"] == 5, bucket
    assert bucket["bad_count"] == 20, bucket
    assert bucket["min"] > 0.0, "a BAD-quality zero polluted the statistics"


def test_latest_reports_age_and_staleness(api, device):
    _, token = device
    old = datetime.now(UTC) - timedelta(hours=6)
    api.post("/api/v1/ingest/telemetry",
             json={"samples": [{"asset_id": "stale-test", "signal": "t",
                                "value": 22.4, "unit": "DEG_C",
                                "ts": old.isoformat()}]},
             headers={"X-Device-Token": token})

    body = api.get("/api/v1/entities/stale-test/latest",
                   params={"tenant": TENANT, "stale_after_s": 300},
                   headers=hdr(KEY_ACME)).json()
    row = body["signals"][0]
    assert row["value"] == 22.4
    assert row["age_s"] > 300
    assert row["stale"] is True, \
        "a six-hour-old reading must not be presented as current"


def test_signal_inventory_lists_what_actually_arrived(api, device):
    body = api.get(f"/api/v1/twins/{TENANT}/signals",
                   headers=hdr(KEY_ACME)).json()
    assert body["available"] is True
    names = {s["signal"] for s in body["signals"]}
    assert SIGNAL in names and "hvac:ReadBack" in names
    for s in body["signals"]:
        assert s["samples"] > 0
        assert s["first_ts"] and s["last_ts"]


def test_trends_resolve_every_series_at_one_tier(api, device):
    """Mixed resolutions on one chart make two lines look comparable when their
    points cover different spans."""
    body = api.get(f"/api/v1/twins/{TENANT}/trends",
                   params={"signals": f"{ASSET}:{SIGNAL},{ASSET}:hvac:ReadBack",
                           "from": "-2h", "agg": "1m"},
                   headers=hdr(KEY_ACME)).json()
    assert body["agg"] == "1m"
    assert body["count"] == 2
    assert {s["agg"] for s in body["series"]} == {"1m"}


def test_trends_rejects_a_malformed_pair(api, device):
    resp = api.get(f"/api/v1/twins/{TENANT}/trends",
                   params={"signals": "no-colon-here", "from": "-1h"},
                   headers=hdr(KEY_ACME))
    assert resp.status_code == 400
    assert "asset_id:signal" in resp.json()["detail"]


# ── Isolation ──────────────────────────────────────────────────────────────


def test_history_is_tenant_scoped(api, device):
    """The historian is behind the same global tenant dependency as everything
    else — a new store must not reintroduce the hole that was just closed."""
    assert api.get(f"/api/v1/entities/{ASSET}/history",
                   params={"tenant": OTHER, "signal": SIGNAL},
                   headers=hdr(KEY_ACME)).status_code == 403
    assert api.get(f"/api/v1/twins/{OTHER}/signals",
                   headers=hdr(KEY_ACME)).status_code == 403
    assert api.get(f"/api/v1/twins/{OTHER}/trends",
                   params={"signals": f"{ASSET}:{SIGNAL}"},
                   headers=hdr(KEY_ACME)).status_code == 403
    assert api.get(f"/api/v1/twins/{OTHER}/history/stats",
                   headers=hdr(KEY_ACME)).status_code == 403


def test_device_routes_are_scoped_and_404_not_403(api, device):
    """A device outside the caller's scope must be indistinguishable from one
    that does not exist, or the endpoint becomes a device-id oracle."""
    api.post("/api/v1/ingest/devices",
             json={"tenant": OTHER, "device_id": "gw-globex"},
             headers=hdr(KEY_ADMIN))
    for call in (
        lambda: api.post("/api/v1/ingest/devices/gw-globex/rotate",
                         headers=hdr(KEY_ACME)),
        lambda: api.post("/api/v1/ingest/devices/gw-globex/enabled",
                         json={"enabled": False}, headers=hdr(KEY_ACME)),
        lambda: api.delete("/api/v1/ingest/devices/gw-globex",
                           headers=hdr(KEY_ACME)),
    ):
        resp = call()
        assert resp.status_code == 404, resp.text


def test_device_listing_is_scope_filtered(api, device):
    body = api.get("/api/v1/ingest/devices", headers=hdr(KEY_ACME)).json()
    assert all(d["tenant_id"] == TENANT for d in body["devices"])
    assert "hidden_by_scope" in body


def test_ingest_with_api_key_requires_a_named_tenant(api, historian_ready):
    resp = api.post("/api/v1/ingest/telemetry",
                    json={"samples": _samples(
                        1, start=datetime.now(UTC) - timedelta(hours=1))},
                    headers=hdr(KEY_ACME))
    assert resp.status_code == 400
    assert "device token" in resp.json()["detail"].lower()


# ── Health / posture ───────────────────────────────────────────────────────


def test_health_reports_the_historian_backend(api, historian_ready):
    body = api.get("/api/v1/health").json()
    assert "historian" in body
    hist = body["historian"]
    assert hist["backend"] in ("mysql", "sqlite")
    # Neither backend has columnar compression or automatic retention, so must not
    # claim to be safe for unbounded production ingest.
    assert hist["scale_safe"] is False, \
        "a backend with no compression or retention must not claim to be safe"
    assert "retention" in hist["detail"]


def test_ingest_status_reports_silent_devices(api, device):
    body = api.get("/api/v1/ingest/status",
                   params={"tenant": TENANT}, headers=hdr(KEY_ADMIN)).json()
    assert "historian" in body and "devices" in body
    assert "silent_devices" in body
    assert body["tenant"]["samples"] > 0


def test_a_raising_behaviour_is_counted_not_swallowed(historian_ready):
    """A rule that silently stops firing is the worst outcome available: the twin
    looks healthy at exactly the moment it stopped watching. So an exception
    inside behaviour evaluation must land in `errors` — and must not stop the rest
    of the batch being evaluated, since one asset missing from the graph should
    not blind every other rule.

    Driven at the pipeline level rather than over HTTP because the failure has to
    be injected: with no Neo4j the registry builds fine and simply has no rule
    watching a synthetic signal, which is a no-op rather than a fault, and
    asserting on that would have proved nothing.
    """
    import ingest
    from historian import Measurement
    from ingest import pipeline

    class _Exploding:
        calls = 0

        def process(self, sample):
            type(self).calls += 1
            raise RuntimeError(f"rule blew up on {sample.entity_id}")

    start = datetime.now(UTC) - timedelta(minutes=90)
    batch = [Measurement(TENANT, f"asset-{i}", "t",
                         start + timedelta(seconds=i), 1.0, "x")
             for i in range(3)]

    pipeline.reset_loops()
    original = pipeline._loop_for
    pipeline._loop_for = lambda tenant_id: _Exploding()      # noqa: SLF001
    try:
        result = ingest.submit(TENANT, batch, evaluate=True, publish=False)
    finally:
        pipeline._loop_for = original                        # noqa: SLF001
        pipeline.reset_loops()

    assert result.accepted == 3, "history must persist even when rules fail"
    assert len(result.errors) == 3, result.errors
    assert _Exploding.calls == 3, \
        "one failing sample stopped the rest of the batch being evaluated"
    assert "blew up" in result.errors[0]


def test_unavailable_registry_is_reported(historian_ready):
    """When the behaviour registry cannot be built at all, that must surface too —
    not be mistaken for "no rules matched"."""
    import ingest
    from historian import Measurement
    from ingest import pipeline

    pipeline.reset_loops()
    original = pipeline._loop_for
    pipeline._loop_for = lambda tenant_id: None              # noqa: SLF001
    try:
        result = ingest.submit(
            TENANT,
            [Measurement(TENANT, "reg-test", "t",
                         datetime.now(UTC) - timedelta(minutes=5),
                         1.0, "x")],
            evaluate=True, publish=False)
    finally:
        pipeline._loop_for = original                        # noqa: SLF001
        pipeline.reset_loops()

    assert result.accepted == 1
    assert any("behaviours not evaluated" in e for e in result.errors), result.errors
