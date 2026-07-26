"""
ingest_routes.py — the telemetry inbound HTTP surface.

    POST /api/v1/ingest/telemetry        JSON batch (the main path)
    POST /api/v1/ingest/telemetry/bulk   NDJSON, optionally gzipped (backfill)
    GET  /api/v1/ingest/status           is data arriving?
    POST /api/v1/ingest/devices          register a device -> returns its token ONCE
    GET  /api/v1/ingest/devices          list devices (scope-filtered)
    POST /api/v1/ingest/devices/{id}/rotate
    POST /api/v1/ingest/devices/{id}/enabled
    DELETE /api/v1/ingest/devices/{id}

TWO WAYS TO AUTHENTICATE, AND WHY
---------------------------------
`/ingest/telemetry` accepts EITHER an `X-Device-Token` (the normal path — a
gateway in a plant room) OR a tenant `X-API-Key` with write access (for a
backfill script or a one-off integration run by a human).

The device token is preferred and the difference matters: it establishes the
tenant BY ITSELF, so a device cannot name a tenant at all, and it grants nothing
except appending telemetry. A tenant key on a gateway would let stolen hardware
read every asset, drive the physics runtime and bill the LLM endpoints — see
ingest/devices.py.

WHY 207 AND NOT 400
-------------------
A batch with some bad samples returns 207 Multi-Status with per-sample reasons,
not a 400 for the whole batch. Rejecting 500 readings because one had a bad
timestamp loses 499 good measurements and tells the operator nothing about which
one was wrong. Whoever is wiring up a gateway needs to see exactly which samples
failed and why, while the good ones land.

IDEMPOTENCY
-----------
Guaranteed by the historian's primary key, not by a header: a replayed batch
collides and is skipped, and the response reports `duplicates` so a client can
tell "already had it" from "stored it". An optional `Idempotency-Key` header is
accepted and echoed for the caller's own correlation, but the safety does not
depend on the client sending one — a gateway that reconnects and resends its
buffer must be safe whether or not it remembered to.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

import ingest
from ingest import devices as device_registry
from server.tenancy import TenantForbidden, require_write, scope_of

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

# Bodies above this are refused outright. Without a cap a single request can pin
# a worker's memory, and an ingest endpoint is the most attractive place to try
# it because it is designed to accept bulk data.
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BULK_BYTES = 64 * 1024 * 1024
MAX_BATCH_SAMPLES = 10_000


# ── Request models ─────────────────────────────────────────────────────────


class SamplePayload(BaseModel):
    """One reading. `asset_id` may be omitted when the batch sets it once."""
    asset_id: str = ""
    signal: str
    value: Optional[float] = None
    ts: Optional[str] = None
    unit: str = ""
    quality: int = 192
    source: str = ""

    model_config = {"extra": "ignore"}


class TelemetryBatch(BaseModel):
    """A batch of readings.

    `tenant` is OPTIONAL and is ignored entirely when a device token is used —
    the token already establishes the tenant. It exists for the API-key path,
    where a human's script must say which twin it is writing to, and it is
    authorized by the global tenant dependency like every other tenant field.
    """
    tenant: Optional[str] = None
    asset_id: str = Field("", description="Default asset for samples that omit it")
    source: str = Field("", description="Default source label, e.g. 'opcua'")
    samples: list[SamplePayload] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class DeviceCreate(BaseModel):
    tenant: str
    name: str = ""
    device_id: Optional[str] = None
    asset_prefix: str = ""
    ttl_days: Optional[int] = None


class EnabledRequest(BaseModel):
    enabled: bool = True


# ── Authentication ─────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    """Best-effort client IP. `X-Forwarded-For`'s FIRST entry is the client when
    the request came through an ALB; anything after it is proxy chain. Used only
    for the device's last-seen record, never for authorization — the header is
    caller-controlled."""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (getattr(request.client, "host", "") or "")[:64]


def _resolve_writer(request: Request, body_tenant: Optional[str]) -> tuple[str, dict]:
    """(tenant_id, principal) for an ingest call.

    Device token first. It is the stronger credential for this purpose because the
    tenant comes from the credential rather than the payload, so nothing a client
    sends can redirect the write.

    `AuthMiddleware` has already authenticated the token and stashed the device on
    `request.state` — one registry lookup per request rather than two, which
    matters on a path designed to be called continuously. A device that names a
    DIFFERENT tenant in its body has already been refused by the global tenant
    dependency (its scope allows only its own tenant), which is deliberate:
    silently ignoring the field would leave a misconfigured gateway convinced it
    was writing to one twin while the data landed in another.
    """
    device = getattr(request.state, "device", None)
    if device is not None:
        device_registry.note_activity(device.device_id, ip=_client_ip(request))
        return device.tenant_id, {"kind": "device", "device": device}

    # Fall back to a tenant API key. The global tenant dependency has already
    # authorized `body_tenant` against the caller's scope, so reaching here means
    # it is allowed — but a tenant must still be NAMED, and a read-only key must
    # not be able to append history.
    scope = require_write(request)
    if not body_tenant:
        raise HTTPException(
            status_code=400,
            detail="No X-Device-Token, so 'tenant' is required in the body. "
                   "Prefer a device token: register one with "
                   "POST /api/v1/ingest/devices.")
    if not scope.allows(body_tenant):
        raise TenantForbidden(
            f"Key '{scope.name}' cannot write to tenant '{body_tenant}'.")
    return body_tenant, {"kind": "api-key", "name": scope.name}


def _note_device(principal: dict, result) -> None:
    device = principal.get("device")
    if device is None:
        return
    device_registry.note_activity(
        device.device_id, accepted=result.accepted,
        rejected=len(result.rejected))


def _enforce_asset_scope(principal: dict, rows: list[dict]) -> list[dict]:
    """Drop rows a device is not allowed to write.

    An `asset_prefix` on a device is what lets one gateway per plant, or per
    line, be issued a credential that cannot write outside its own equipment —
    so a compromised gateway in one building cannot fabricate readings for
    another. Violations are returned as rejections rather than failing the batch,
    so a misconfigured gateway reports precisely which tags were refused.
    """
    device = principal.get("device")
    if device is None or not device.asset_prefix:
        return []
    refused = []
    for i, row in enumerate(rows):
        asset = str(row.get("asset_id") or "")
        if not device.allows_asset(asset):
            refused.append({
                "index": i, "asset_id": asset,
                "signal": str(row.get("signal") or ""),
                "reason": (f"device '{device.device_id}' may only write assets "
                           f"beginning '{device.asset_prefix}'"),
            })
    if refused:
        blocked = {r["index"] for r in refused}
        rows[:] = [r for i, r in enumerate(rows) if i not in blocked]
    return refused


# ── Telemetry ──────────────────────────────────────────────────────────────


@router.post("/telemetry")
async def ingest_telemetry(batch: TelemetryBatch, request: Request,
                           response: Response):
    """Append a batch of readings.

    200 when everything landed, 207 when some samples were refused (the response
    lists each with a reason). Duplicates are not a failure — they are the
    idempotency guarantee doing its job.
    """
    tenant_id, principal = _resolve_writer(request, batch.tenant)

    if len(batch.samples) > MAX_BATCH_SAMPLES:
        raise HTTPException(
            status_code=413,
            detail=f"{len(batch.samples)} samples exceeds the {MAX_BATCH_SAMPLES} "
                   f"per-batch limit. Split the batch or use "
                   f"POST /api/v1/ingest/telemetry/bulk for backfill.")
    if not batch.samples:
        raise HTTPException(status_code=400, detail="'samples' is empty")

    rows = [s.model_dump() for s in batch.samples]
    for row in rows:
        if not row.get("asset_id"):
            row["asset_id"] = batch.asset_id
        if not row.get("source"):
            row["source"] = batch.source or ("device" if principal["kind"] == "device"
                                             else "api")
    refused = _enforce_asset_scope(principal, rows)

    result = ingest.submit_raw(tenant_id, rows,
                               default_source=batch.source or "api",
                               default_asset_id=batch.asset_id)
    result.rejected = refused + result.rejected
    result.submitted += len(refused)
    _note_device(principal, result)

    payload = {"tenant": tenant_id, **result.to_dict()}
    key = request.headers.get("Idempotency-Key")
    if key:
        payload["idempotency_key"] = key
    if result.rejected:
        response.status_code = 207
    return payload


@router.post("/telemetry/bulk")
async def ingest_bulk(request: Request, response: Response,
                      tenant: Optional[str] = None,
                      asset_id: str = "", source: str = "backfill"):
    """Backfill from NDJSON — one JSON object per line, optionally gzipped.

    NDJSON rather than a JSON array because a backfill is large and line-oriented:
    it streams, it survives truncation with a known boundary, and it is what every
    historian export and CSV-to-JSON tool already produces. Send
    `Content-Encoding: gzip` and this decompresses it — telemetry compresses ~10x,
    so it is the difference between a viable upload and a timeout.
    """
    body = await request.body()
    if len(body) > MAX_BULK_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"body exceeds {MAX_BULK_BYTES // (1024 * 1024)} MiB")

    encoding = (request.headers.get("content-encoding") or "").lower()
    if "gzip" in encoding or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception as e:
            raise HTTPException(status_code=400,
                                detail=f"gzip decode failed: {e}")

    tenant_id, principal = _resolve_writer(request, tenant)

    rows: list[dict] = []
    malformed: list[dict] = []
    for line_no, line in enumerate(body.splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except Exception as e:
            malformed.append({"index": line_no, "asset_id": "", "signal": "",
                              "reason": f"line {line_no + 1}: {str(e)[:120]}"})
            continue
        if not isinstance(obj, dict):
            malformed.append({"index": line_no, "asset_id": "", "signal": "",
                              "reason": f"line {line_no + 1}: not a JSON object"})
            continue
        if asset_id and not obj.get("asset_id"):
            obj["asset_id"] = asset_id
        rows.append(obj)

    if not rows and not malformed:
        raise HTTPException(status_code=400, detail="no NDJSON lines found")

    refused = _enforce_asset_scope(principal, rows)
    result = ingest.submit_raw(tenant_id, rows, default_source=source,
                               default_asset_id=asset_id)
    result.rejected = malformed + refused + result.rejected
    result.submitted += len(malformed) + len(refused)
    _note_device(principal, result)

    if result.rejected:
        response.status_code = 207
    return {"tenant": tenant_id, **result.to_dict()}


@router.get("/status")
def ingest_status(request: Request, tenant: Optional[str] = None):
    """Is data arriving, and from whom.

    Reports the historian backend, per-device last-seen, and any device that has
    gone silent. A gateway that simply stops publishing raises no error anywhere —
    the charts just stop moving while the twin keeps serving its last value as if
    it were current. This endpoint is how that becomes visible.
    """
    scope = scope_of(request)
    payload = ingest.pipeline.status(tenant)
    if not scope.is_admin:
        # Never let a scoped key see other tenants' devices via the fleet view.
        payload["silent_devices"] = [
            d for d in payload.get("silent_devices", [])
            if scope.allows(d.get("tenant_id", ""))]
        payload["devices"] = {"scope": scope.describe()}
    return payload


# ── Device management ──────────────────────────────────────────────────────


@router.post("/devices", status_code=201)
def create_device(req: DeviceCreate, request: Request):
    """Register a device. The token is in the response and NEVER retrievable
    again — store it in the gateway now."""
    require_write(request)
    try:
        device, token = device_registry.create(
            tenant_id=req.tenant, name=req.name, device_id=req.device_id,
            asset_prefix=req.asset_prefix, ttl_days=req.ttl_days,
            created_by=scope_of(request).name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "device": device.to_dict(),
        "token": token,
        "warning": "This token is shown once and cannot be recovered. Store it "
                   "in the device's configuration now. If it is lost, call "
                   f"POST /api/v1/ingest/devices/{device.device_id}/rotate.",
        "usage": {
            "header": "X-Device-Token: <token>",
            "endpoint": "POST /api/v1/ingest/telemetry",
            "note": "A device token establishes its own tenant — do not send "
                    "'tenant' in the body.",
        },
    }


@router.get("/devices")
def list_devices(request: Request, tenant: Optional[str] = None):
    """Devices this caller may see. Never includes a token or its hash."""
    scope = scope_of(request)
    rows = device_registry.list_for_tenant(tenant)
    visible = [d.to_dict() for d in rows if scope.allows(d.tenant_id)]
    return {"count": len(visible), "devices": visible,
            "hidden_by_scope": len(rows) - len(visible)}


def _owned_device(request: Request, device_id: str):
    """Fetch a device and authorize it against the caller's scope.

    Devices are addressed by id, not by tenant, so the global tenant dependency
    has nothing to check on these routes — the authorization has to happen here.
    A 404 (not a 403) is returned for a device outside the caller's scope, so the
    endpoint cannot be used to discover which device ids exist elsewhere.
    """
    device = device_registry.get(device_id)
    scope = scope_of(request)
    if device is None or not scope.allows(device.tenant_id):
        raise HTTPException(status_code=404,
                            detail=f"Device '{device_id}' not found")
    return device


@router.post("/devices/{device_id}/rotate")
def rotate_device(device_id: str, request: Request):
    """Issue a new token, invalidating the old one immediately."""
    require_write(request)
    device = _owned_device(request, device_id)
    token = device_registry.rotate(device.device_id)
    return {"device_id": device.device_id, "token": token,
            "warning": "The previous token stopped working immediately. Update "
                       "the device's configuration before its next publish."}


@router.post("/devices/{device_id}/enabled")
def set_device_enabled(device_id: str, req: EnabledRequest, request: Request):
    """Disable or re-enable a device without destroying its history or counters —
    the right response to a gateway suspected of being compromised or
    misconfigured, because it is instantly reversible and keeps the audit trail."""
    require_write(request)
    device = _owned_device(request, device_id)
    device_registry.set_enabled(device.device_id, req.enabled)
    return {"device_id": device.device_id, "enabled": req.enabled}


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, request: Request):
    """Remove a device. Its measurements are NOT deleted — history is the audit
    record, and a decommissioned gateway must not erase what it observed."""
    require_write(request)
    device = _owned_device(request, device_id)
    device_registry.delete(device.device_id)
    return {"device_id": device.device_id, "status": "deleted",
            "note": "Measurements retained — delete history via the twin, not "
                    "the device."}
