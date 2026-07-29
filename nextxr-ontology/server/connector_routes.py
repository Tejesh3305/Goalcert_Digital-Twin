"""
connector_routes.py — commissioning and operating the field-protocol connectors.

    GET    /api/v1/connectors/protocols            what can run here, and why not
    GET    /api/v1/connectors/profiles             built-in device profiles
    POST   /api/v1/connectors/profiles/{key}/build render a profile to a point map
    GET    /api/v1/connectors                      list (scope-filtered)
    POST   /api/v1/connectors                      create
    GET    /api/v1/connectors/{id}                 one, with health
    PATCH  /api/v1/connectors/{id}                 update
    DELETE /api/v1/connectors/{id}                 remove
    POST   /api/v1/connectors/{id}/start|stop      lifecycle
    POST   /api/v1/connectors/{id}/test            probe WITHOUT committing
    GET    /api/v1/connectors/{id}/health          live counters
    POST   /api/v1/connectors/discover/sunspec     walk a device's model chain
    POST   /api/v1/connectors/{id}/browse          walk an OPC-UA address space

THE TWO ENDPOINTS THAT MATTER MOST DURING COMMISSIONING
------------------------------------------------------
`/test` and `/discover/sunspec`. An engineer on a roof needs to know whether the
wiring and the addressing are right BEFORE committing a connector, and needs the
SunSpec base address to be discovered rather than guessed — a Modbus offset that is
wrong by a few registers still decodes into plausible small integers, so the device
looks like it is working and every number is wrong.

`/test` returns the first 25 decoded points with their values, so the engineer can
compare them against what the inverter's own display says. That comparison is the
only real proof the point map is right, and it takes seconds.

CREDENTIALS
-----------
A connector config holds a customer's PLC password. `ConnectorConfig.redacted()` is
ALLOW-listed, not deny-listed, and every response here goes through it — a field
added later that a deny-list forgot would otherwise be published.
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import connectors as C
from connectors import manager as connector_manager
from connectors import profiles as connector_profiles
from connectors.base import ConnectorConfig, MissingDependency, build_connector
from connectors.pointmap import PointMap, PointMapError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from server.tenancy import require_write, scope_of

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


# ── Request models ─────────────────────────────────────────────────────────


class ConnectorCreate(BaseModel):
    tenant: str
    protocol: str = Field(..., description="modbus_tcp | modbus_rtu | opcua | mqtt")
    name: str = ""
    connector_id: str | None = None
    enabled: bool = True

    host: str = ""
    port: int = 0
    serial_port: str = ""
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    endpoint: str = ""
    username: str = ""
    password: str = ""
    use_tls: bool = True
    ca_cert: str = ""
    client_cert: str = ""
    client_key: str = ""
    tls_insecure: bool = False
    topics: list[str] = Field(default_factory=list)
    topic_asset_pattern: str = ""
    client_id: str = ""
    # §2.3 specifies 1 Hz to 0.2 Hz.
    poll_interval_s: float = Field(1.0, ge=0.05, le=3600.0)
    timeout_s: float = Field(3.0, ge=0.1, le=120.0)
    request_retries: int = Field(2, ge=0, le=10)
    default_asset_id: str = ""
    emit_bad_on_disconnect: bool = True

    # Either a full point map, or a built-in profile plus its arguments.
    point_map: dict | None = None
    profile: str | None = None
    profile_args: dict = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    """Partial update. Every field optional; only what is sent changes.

    `password` omitted means KEEP the existing one — a client that reads a connector
    (and receives `"***"`) and writes it back must not be able to overwrite the real
    credential with those three characters.
    """
    name: str | None = None
    enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    serial_port: str | None = None
    endpoint: str | None = None
    username: str | None = None
    password: str | None = None
    use_tls: bool | None = None
    tls_insecure: bool | None = None
    topics: list[str] | None = None
    topic_asset_pattern: str | None = None
    poll_interval_s: float | None = None
    timeout_s: float | None = None
    request_retries: int | None = None
    default_asset_id: str | None = None
    emit_bad_on_disconnect: bool | None = None
    point_map: dict | None = None


class SunSpecDiscover(BaseModel):
    tenant: str
    host: str
    port: int = 502
    unit_id: int = 1
    timeout_s: float = 3.0
    include_common: bool = True


class BrowseRequest(BaseModel):
    node_id: str = ""
    max_depth: int = Field(3, ge=1, le=8)
    max_nodes: int = Field(300, ge=1, le=2000)


class ProfileBuild(BaseModel):
    tenant: str
    args: dict = Field(default_factory=dict)


# ── Helpers ────────────────────────────────────────────────────────────────


def _owned(request: Request, connector_id: str) -> ConnectorConfig:
    """Fetch a connector and authorize it against the caller's scope.

    Connectors are addressed by id, so the global tenant dependency has nothing to
    check on these routes — authorization has to happen here. 404 rather than 403 for
    a connector outside the caller's scope, so the endpoint cannot be used to
    discover which connector ids exist elsewhere.
    """
    config = C.get(connector_id)
    if config is None or not scope_of(request).allows(config.tenant_id):
        raise HTTPException(status_code=404,
                            detail=f"Connector '{connector_id}' not found")
    return config


def _point_map_from(req: ConnectorCreate) -> PointMap | None:
    """Resolve either an explicit map or a named profile."""
    if req.point_map:
        return PointMap.from_dict(req.point_map)
    if req.profile:
        return connector_profiles.build_profile(req.profile, **(req.profile_args or {}))
    return None


def _with_health(config: ConnectorConfig) -> dict:
    out = config.redacted()
    health = connector_manager.get_manager().health(config.connector_id)
    out["health"] = health or {"state": "stopped", "connected": False,
                               "owned_by_this_task": False}
    return out


# ── Catalogue ──────────────────────────────────────────────────────────────


@router.get("/protocols")
def protocols():
    """Which protocols can actually run in this deployment.

    Reports the pip package and the install command for anything unavailable,
    because "the MQTT connector does nothing" is otherwise a long debugging session
    that ends at a missing dependency.
    """
    return {"protocols": C.protocol_availability(),
            "registered": C.available_protocols()}


@router.get("/profiles")
def profiles():
    """Built-in device profiles, one per sensor class in the specification."""
    return {"profiles": connector_profiles.list_profiles(),
            "note": ("Each profile maps a hardware class from §2.1/§2.2 to canonical "
                     "signals. sunspec_inverter_3ph REQUIRES base_address — discover "
                     "it with POST /connectors/discover/sunspec, never guess it.")}


@router.post("/profiles/{key}/build")
def build_profile_preview(key: str, req: ProfileBuild):
    """Render a profile to a concrete point map WITHOUT creating a connector.

    Lets an integrator inspect exactly which registers will be read and which asset
    ids will be written before committing anything — and see the read plan, which is
    how you find out a 40-point device will take one round trip rather than forty.
    """
    try:
        point_map = connector_profiles.build_profile(key, **(req.args or {}))
    except (ValueError, PointMapError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        from packs.solar.signals import ALL_SIGNAL_IRIS
        point_map.validate(known_signals=ALL_SIGNAL_IRIS)
    except PointMapError as e:
        raise HTTPException(status_code=400, detail=str(e))

    plan = []
    if point_map.protocol.startswith("modbus"):
        from connectors.modbus import _plan_blocks
        plan = [{"function_code": fc, "start": start, "count": count}
                for fc, start, count in _plan_blocks(point_map)]

    return {
        "profile": key, "point_map": point_map.to_dict(),
        "point_count": len(point_map.points),
        "address_span": point_map.address_span(),
        "read_plan": plan,
        "read_requests_per_poll": len(plan) or None,
        "assets": sorted({p.asset_id for p in point_map.points if p.asset_id}),
        "signals": sorted({p.signal for p in point_map.points}),
    }


# ── CRUD ───────────────────────────────────────────────────────────────────


@router.get("")
def list_connectors(request: Request, tenant: str | None = None):
    """Connectors this caller may see, each with live health."""
    scope = scope_of(request)
    configs = C.list_configs(tenant)
    visible = [c for c in configs if scope.allows(c.tenant_id)]
    return {"count": len(visible),
            "connectors": [_with_health(c) for c in visible],
            "hidden_by_scope": len(configs) - len(visible),
            "manager": connector_manager.get_manager().stats()
            if scope.is_admin else None}


@router.post("", status_code=201)
def create_connector(req: ConnectorCreate, request: Request):
    """Create a connector. Its point map is validated before anything is stored.

    Validation at CREATE time is the point: a map that decodes wrongly emits
    plausible numbers at 1 Hz, and by the time anyone questions them the historian
    holds days of corrupted baselines that every §4.2 threshold has been compared
    against. Rejecting a config is cheap; un-poisoning a baseline is not.
    """
    require_write(request)

    connector_id = (req.connector_id
                    or f"conn-{req.protocol.replace('_', '-')}-{uuid.uuid4().hex[:8]}")
    connector_id = connector_id.strip().lower()
    if not _ID_RE.match(connector_id):
        raise HTTPException(
            status_code=400,
            detail="connector_id must be 3-64 chars of [a-z0-9._-], starting "
                   "alphanumeric")
    if C.get(connector_id) is not None:
        raise HTTPException(status_code=409,
                            detail=f"connector '{connector_id}' already exists")

    try:
        point_map = _point_map_from(req)
    except (ValueError, PointMapError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    payload = req.model_dump(exclude={"tenant", "point_map", "profile",
                                      "profile_args", "connector_id"})
    config = ConnectorConfig(connector_id=connector_id, tenant_id=req.tenant,
                             point_map=point_map, **payload)
    try:
        C.save(config)
    except (ValueError, PointMapError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Reconcile immediately so an enabled connector starts polling now rather than
    # at the next lease tick — commissioning is interactive, and an eight-second
    # wait reads as "it didn't work".
    manager = connector_manager.get_manager()
    manager.ensure_reconciler()
    report = manager.reconcile()

    return {"connector": _with_health(config),
            "reconcile": {k: v for k, v in report.items() if v}}


@router.get("/{connector_id}")
def get_connector(connector_id: str, request: Request):
    return {"connector": _with_health(_owned(request, connector_id))}


@router.patch("/{connector_id}")
def update_connector(connector_id: str, req: ConnectorUpdate, request: Request):
    """Partial update. Restarts the connector so changes take effect at once."""
    require_write(request)
    config = _owned(request, connector_id)

    updates = req.model_dump(exclude_unset=True)
    point_map_raw = updates.pop("point_map", None)
    for key, value in updates.items():
        if value is not None:
            setattr(config, key, value)
    if point_map_raw is not None:
        try:
            config.point_map = PointMap.from_dict(point_map_raw)
        except PointMapError as e:
            raise HTTPException(status_code=400, detail=str(e))

    try:
        C.save(config)
    except (ValueError, PointMapError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    manager = connector_manager.get_manager()
    manager.stop_one(connector_id)
    report = manager.reconcile()
    return {"connector": _with_health(config),
            "reconcile": {k: v for k, v in report.items() if v}}


@router.delete("/{connector_id}")
def delete_connector(connector_id: str, request: Request):
    """Remove a connector. Its measurements are NOT deleted — history is the audit
    record, and decommissioning a gateway must not erase what it observed."""
    require_write(request)
    config = _owned(request, connector_id)
    C.delete(config.connector_id)
    return {"connector_id": config.connector_id, "status": "deleted",
            "note": "Measurements retained in the historian."}


# ── Lifecycle ──────────────────────────────────────────────────────────────


@router.post("/{connector_id}/start")
def start_connector(connector_id: str, request: Request):
    require_write(request)
    config = _owned(request, connector_id)
    if not config.enabled:
        config.enabled = True
        C.save(config, validate=False)
    try:
        result = connector_manager.get_manager().start_one(config.connector_id)
    except MissingDependency as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])
    return {"connector_id": config.connector_id, **result,
            "health": connector_manager.get_manager().health(config.connector_id)}


@router.post("/{connector_id}/stop")
def stop_connector(connector_id: str, request: Request):
    """Stop polling and mark the connector disabled, so the supervisor's next
    reconcile does not simply start it again."""
    require_write(request)
    config = _owned(request, connector_id)
    config.enabled = False
    C.save(config, validate=False)
    result = connector_manager.get_manager().stop_one(config.connector_id)
    return {"connector_id": config.connector_id, **result}


@router.get("/{connector_id}/health")
def connector_health(connector_id: str, request: Request):
    """Live counters: polls, failures, samples, deadband suppression, reconnects.

    `deadband_suppression_pct` is worth watching: it is the proof the §2.3 filter is
    earning its keep, and a sudden drop toward zero means a signal that used to be
    steady has started moving.
    """
    config = _owned(request, connector_id)
    health = connector_manager.get_manager().health(config.connector_id)
    if health is None:
        return {"connector_id": config.connector_id,
                "health": {"state": "stopped", "connected": False,
                           "owned_by_this_task": False},
                "note": ("Not running on this task. Either it is disabled, or "
                         "another task holds its ownership lease — check "
                         "/api/v1/health twin_runtime/connectors across the fleet.")}
    return {"connector_id": config.connector_id, "health": health}


@router.post("/{connector_id}/test")
def test_connector(connector_id: str, request: Request):
    """Probe the device and return the first decoded points, WITHOUT starting it.

    The most useful endpoint in this file. An engineer compares these values against
    the inverter's own display; if they match, the point map is right. That check
    takes seconds and is the only real proof — and doing it before committing avoids
    writing a week of wrongly-scaled history.
    """
    config = _owned(request, connector_id)
    try:
        connector = build_connector(config)
    except MissingDependency as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])
    result = connector.test_connection()
    return {"connector_id": config.connector_id, **result,
            "hint": ("Compare `sample` against the device's own display. Values that "
                     "are out by a factor of 10, 100 or 65536 mean a scale, scale "
                     "factor or word-order mismatch respectively."
                     if result.get("ok") else
                     "Check host/port reachability, the Modbus unit id, and whether "
                     "another master already holds the RS-485 segment.")}


# ── Discovery ──────────────────────────────────────────────────────────────


@router.post("/discover/sunspec")
def discover_sunspec(req: SunSpecDiscover, request: Request):
    """Walk a device's SunSpec model chain and report where each model lives.

    This is what makes `base_address` a discovered fact rather than a guess. A
    guessed offset is the single most dangerous mistake on this path: neighbouring
    SunSpec points are all small scaled integers, so an offset that is wrong by a few
    registers decodes into entirely plausible values and the device appears to work.

    Also returns the Common Model (manufacturer / model / serial), which confirms the
    device is the one the drawings say it is before anyone binds a point map to it.
    """
    require_write(request)
    try:
        from connectors.modbus import discover_sunspec as walk
        from connectors.modbus import read_sunspec_common
    except MissingDependency as e:
        raise HTTPException(status_code=501, detail=str(e))

    chain = walk(req.host, port=req.port, unit_id=req.unit_id,
                 timeout_s=req.timeout_s)
    out: dict[str, Any] = {"tenant": req.tenant, "host": req.host, **chain}

    if chain.get("ok") and req.include_common:
        try:
            out["device"] = read_sunspec_common(
                req.host, port=req.port, unit_id=req.unit_id,
                timeout_s=req.timeout_s)
        except Exception as e:
            out["device"] = {"ok": False, "error": str(e)[:200]}

    if chain.get("base_address"):
        out["next_step"] = {
            "profile": "sunspec_inverter_3ph",
            "args": {"asset_id": "<Zone_NN_Inverter_NN>",
                     "base_address": chain["base_address"],
                     "unit_id": req.unit_id},
            "endpoint": "POST /api/v1/connectors/profiles/sunspec_inverter_3ph/build",
        }
    return out


@router.post("/{connector_id}/browse")
def browse_connector(connector_id: str, req: BrowseRequest, request: Request):
    """Walk an OPC-UA server's address space to discover real node ids.

    Reads each Variable's live value during the walk, so an engineer can confirm they
    picked the right node by seeing the number rather than trusting its name. Bounded
    in depth AND node count — a large PLC's address space runs to tens of thousands of
    nodes and an unbounded browse would hang the request and hammer the server.
    """
    config = _owned(request, connector_id)
    if config.protocol != "opcua":
        raise HTTPException(
            status_code=400,
            detail=f"browse is OPC-UA only; this connector is '{config.protocol}'. "
                   f"For Modbus use POST /api/v1/connectors/discover/sunspec.")
    try:
        connector = build_connector(config)
    except MissingDependency as e:
        raise HTTPException(status_code=501, detail=str(e))

    try:
        connector.connect()
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"cannot reach the OPC-UA server: {str(e)[:250]}")
    try:
        nodes = connector.browse(req.node_id, max_depth=req.max_depth,
                                max_nodes=req.max_nodes)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"browse failed: {str(e)[:250]}")
    finally:
        try:
            connector.disconnect()
        except Exception:
            pass

    variables = [n for n in nodes if "Variable" in n.get("node_class", "")]
    return {"connector_id": config.connector_id, "count": len(nodes),
            "variables": len(variables), "nodes": nodes,
            "truncated": len(nodes) >= req.max_nodes,
            "hint": ("Copy the node_id of each Variable you want into a point map's "
                     "`node_id` field.")}
