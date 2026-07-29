"""
manager.py — the connector supervisor: persistence, lifecycle, ownership.

Connectors are DURABLE configuration. A site commissions forty of them and expects
them to still be polling after a deploy, so the config lives in the relational store
and the supervisor reconciles running workers against it.

WHY OWNERSHIP LEASES, NOT "EVERY TASK RUNS EVERY CONNECTOR"
-----------------------------------------------------------
This is the same problem `twins/coordinator.py` already solved for the physics
runtime, and it has the same answer for the same reason. If every ECS task ran every
connector then, at three tasks:

  * each inverter would be polled three times per interval, tripling the load on a
    device whose Modbus stack is a small microcontroller and often cannot take it;
  * an RS-485 segment would have three masters transmitting, which corrupts frames
    rather than merely duplicating them;
  * three copies of every reading would be written. They would COLLIDE on the
    historian's primary key and be skipped, so the data would be correct — which is
    precisely what makes this dangerous, because nothing would fail. The only
    symptoms would be a device that intermittently stops answering and a
    `duplicates` count nobody is watching.

So each connector is owned by whichever task holds its Redis lease, exactly as twins
are. Ownership is per connector, so load still spreads across the fleet, and a task
that dies has its connectors adopted after the lease expires.

Without Redis (local dev, single process) the lease always succeeds — correct for one
process, and `NXR_REQUIRE_REDIS=1` is what stops a multi-task deploy reaching that
path by omission.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime

import db
from db import schema as db_schema

from .base import (
    Connector,
    ConnectorConfig,
    MissingDependency,
    available_protocols,
    build_connector,
)
from .pointmap import PointMap, PointMapError

log = logging.getLogger("nxr.connectors.manager")

_STORE = "connectors"

# Lease TTL and renewal. Renewal at a third of the TTL survives two missed renewals
# — one GC pause or one slow database call must not hand a connector to another task
# and briefly double-poll a device.
LEASE_TTL_S = 24
LEASE_RENEW_S = 8
_LEASE_PREFIX = "nxr:connlease:"

# Import the protocol modules for their @register_connector side effect. Each is
# guarded: a missing client library must not stop the OTHER protocols registering,
# and `protocol_availability()` reports precisely which are unusable and why.
_IMPORT_ERRORS: dict[str, str] = {}
for _module, _protocols in (("modbus", ("modbus_tcp", "modbus_rtu")),
                            ("mqtt", ("mqtt",)),
                            ("opcua", ("opcua",))):
    try:
        __import__(f"connectors.{_module}")
    except Exception as _e:            # noqa: BLE001
        for _p in _protocols:
            _IMPORT_ERRORS[_p] = str(_e)[:200]
        log.info("connector module '%s' unavailable: %s", _module, _e)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_schema() -> None:
    db_schema.ensure(_STORE)


# ── Persistence ─────────────────────────────────────────────────────────────


def _row_to_config(row) -> ConnectorConfig:
    data = dict(row)
    payload = db.json_load(data.get("config")) or {}
    point_map = None
    if payload.get("point_map"):
        point_map = PointMap.from_dict(payload["point_map"])
    fields = {k: v for k, v in payload.items()
              if k in ConnectorConfig.__dataclass_fields__ and k != "point_map"}
    fields.update({
        "connector_id": data["connector_id"],
        "tenant_id": data["tenant_id"],
        "protocol": data["protocol"],
        "name": data.get("name") or "",
        "enabled": bool(data.get("enabled", 1)),
        "point_map": point_map,
    })
    return ConnectorConfig(**fields)


def _config_to_payload(config: ConnectorConfig) -> dict:
    out = {}
    for name in ConnectorConfig.__dataclass_fields__:
        if name == "point_map":
            continue
        out[name] = getattr(config, name)
    if config.point_map is not None:
        out["point_map"] = config.point_map.to_dict()
    return out


def save(config: ConnectorConfig, *, validate: bool = True) -> ConnectorConfig:
    """Persist a connector config, validating its point map first.

    Validation happens at SAVE time deliberately. A map that decodes wrongly emits
    plausible numbers at 1 Hz, and by the time anyone questions them the historian
    holds days of corrupted baselines that every §4.2 threshold has been compared
    against. Rejecting the config is cheap; un-poisoning a baseline is not.
    """
    _ensure_schema()
    if config.protocol not in available_protocols():
        detail = _IMPORT_ERRORS.get(config.protocol)
        raise ValueError(
            f"protocol '{config.protocol}' is not available"
            + (f" ({detail})" if detail else
               f". Available: {available_protocols()}"))
    if validate and config.point_map is not None:
        try:
            from packs.solar.signals import ALL_SIGNAL_IRIS
            known = ALL_SIGNAL_IRIS
        except Exception:
            known = None
        # Signals outside the solar catalogue are legitimate (another domain's
        # connector), so validation of signal NAMES is only enforced when the map's
        # points all look like solar ones. Structural validation always runs.
        points = config.point_map.points
        looks_solar = any(p.signal.startswith("solar:") for p in points)
        config.point_map.validate(
            known_signals=known if (known and looks_solar) else None)

    payload = json.dumps(_config_to_payload(config))
    with db.connect(_STORE) as conn:
        existing = conn.execute(
            "SELECT connector_id FROM connectors WHERE connector_id = ?",
            (config.connector_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE connectors SET tenant_id = ?, protocol = ?, name = ?, "
                "enabled = ?, config = ?, updated_at = ? WHERE connector_id = ?",
                (config.tenant_id, config.protocol, config.name,
                 1 if config.enabled else 0, payload, _now_iso(),
                 config.connector_id))
        else:
            conn.execute(
                "INSERT INTO connectors (connector_id, tenant_id, protocol, name, "
                "enabled, config, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (config.connector_id, config.tenant_id, config.protocol,
                 config.name, 1 if config.enabled else 0, payload,
                 _now_iso(), _now_iso()))
    return config


def get(connector_id: str) -> ConnectorConfig | None:
    _ensure_schema()
    with db.connect(_STORE) as conn:
        row = conn.execute("SELECT * FROM connectors WHERE connector_id = ?",
                           (connector_id,)).fetchone()
    return _row_to_config(row) if row else None


def list_configs(tenant_id: str | None = None) -> list[ConnectorConfig]:
    _ensure_schema()
    with db.connect(_STORE) as conn:
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM connectors WHERE tenant_id = ? ORDER BY created_at",
                (tenant_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM connectors ORDER BY tenant_id, created_at").fetchall()
    out = []
    for row in rows:
        try:
            out.append(_row_to_config(row))
        except (PointMapError, TypeError, ValueError) as e:
            # One unreadable row must not hide every other connector from the UI.
            log.warning("connector row %s is unreadable: %s",
                        dict(row).get("connector_id"), e)
    return out


def delete(connector_id: str) -> bool:
    _ensure_schema()
    get_manager().stop_one(connector_id)
    with db.connect(_STORE) as conn:
        cur = conn.execute("DELETE FROM connectors WHERE connector_id = ?",
                           (connector_id,))
        return bool(getattr(cur, "rowcount", 0))


# ── Ownership leases ────────────────────────────────────────────────────────


class _Leases:
    """Per-connector ownership, so exactly one task polls each device."""

    def __init__(self):
        self._owner = f"task-{id(self):x}-{int(time.time())}"
        self._held: set[str] = set()

    @property
    def owner(self) -> str:
        return self._owner

    def _redis(self):
        try:
            import bus
            event_bus = bus.get_event_bus()
            if getattr(event_bus, "backend", "") != "redis":
                return None
            return getattr(event_bus, "client", None) or getattr(
                event_bus, "_client", None)
        except Exception:
            return None

    def acquire(self, connector_id: str) -> bool:
        client = self._redis()
        if client is None:
            # No Redis: single-process assumption. Correct locally and unsafe at
            # 2+ tasks, which is what NXR_REQUIRE_REDIS=1 exists to prevent.
            self._held.add(connector_id)
            return True
        key = _LEASE_PREFIX + connector_id
        try:
            if client.set(key, self._owner, nx=True, ex=LEASE_TTL_S):
                self._held.add(connector_id)
                return True
            # Already ours? Renew — this is the normal path after the first tick.
            current = client.get(key)
            if current is not None:
                if isinstance(current, bytes):
                    current = current.decode("utf-8", "replace")
                if current == self._owner:
                    client.expire(key, LEASE_TTL_S)
                    self._held.add(connector_id)
                    return True
        except Exception as e:
            log.debug("lease acquire failed for %s: %s", connector_id, e)
            # Redis unreachable mid-flight: KEEP a lease already held rather than
            # dropping the device. Releasing on a transient blip would stop
            # collection on a working connector for no reason.
            return connector_id in self._held
        self._held.discard(connector_id)
        return False

    def release(self, connector_id: str) -> None:
        self._held.discard(connector_id)
        client = self._redis()
        if client is None:
            return
        try:
            key = _LEASE_PREFIX + connector_id
            current = client.get(key)
            if isinstance(current, bytes):
                current = current.decode("utf-8", "replace")
            # Only delete OUR lease. Deleting another task's would let two tasks
            # poll the same device — the exact failure leases prevent.
            if current == self._owner:
                client.delete(key)
        except Exception:
            pass

    def held(self) -> list[str]:
        return sorted(self._held)


# ── Supervisor ──────────────────────────────────────────────────────────────


class ConnectorManager:
    """Reconciles running connectors against persisted, enabled configuration."""

    def __init__(self):
        self._running: dict[str, Connector] = {}
        self._lock = threading.RLock()
        self._leases = _Leases()
        self._reconciler: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_reconcile: str | None = None
        self._errors: dict[str, str] = {}

    # -- reconciliation ------------------------------------------------------

    def reconcile(self) -> dict:
        """Start what should run, stop what should not. Idempotent."""
        report = {"started": [], "stopped": [], "renewed": [], "failed": [],
                  "not_owned": []}
        try:
            configs = {c.connector_id: c for c in list_configs()}
        except Exception as e:
            self._errors["_registry"] = str(e)[:200]
            return {**report, "error": str(e)[:200]}
        self._errors.pop("_registry", None)

        with self._lock:
            # Stop connectors that were deleted or disabled.
            for connector_id in list(self._running):
                config = configs.get(connector_id)
                if config is None or not config.enabled:
                    self._stop_locked(connector_id)
                    report["stopped"].append(connector_id)

            for connector_id, config in configs.items():
                if not config.enabled:
                    continue
                if not self._leases.acquire(connector_id):
                    if connector_id in self._running:
                        # Lost the lease — another task adopted it. Stop promptly so
                        # the device is never polled by two tasks at once.
                        self._stop_locked(connector_id)
                        report["stopped"].append(connector_id)
                    report["not_owned"].append(connector_id)
                    continue

                existing = self._running.get(connector_id)
                if existing is not None:
                    if existing.is_running():
                        report["renewed"].append(connector_id)
                        continue
                    self._stop_locked(connector_id)      # died; restart below

                try:
                    connector = build_connector(config)
                    connector.start()
                    self._running[connector_id] = connector
                    self._errors.pop(connector_id, None)
                    report["started"].append(connector_id)
                except MissingDependency as e:
                    self._errors[connector_id] = str(e)
                    report["failed"].append({"connector_id": connector_id,
                                             "error": str(e)})
                except Exception as e:
                    self._errors[connector_id] = str(e)[:300]
                    report["failed"].append({"connector_id": connector_id,
                                             "error": str(e)[:300]})

        self._last_reconcile = _now_iso()
        return report

    def _stop_locked(self, connector_id: str) -> None:
        connector = self._running.pop(connector_id, None)
        if connector is not None:
            try:
                connector.stop()
            except Exception as e:
                log.debug("stop %s: %s", connector_id, e)
        self._leases.release(connector_id)

    # -- background loop ----------------------------------------------------

    def ensure_reconciler(self) -> None:
        """Start the background reconcile loop (idempotent).

        Renewal cadence is driven by the LEASE, not by how often config changes: a
        lease must be renewed well inside its TTL or another task adopts a connector
        this one is happily running, and the device gets polled twice during the
        overlap.
        """
        with self._lock:
            if self._reconciler is not None and self._reconciler.is_alive():
                return
            self._stop.clear()

            def loop():
                while not self._stop.is_set():
                    try:
                        self.reconcile()
                    except Exception as e:
                        log.warning("connector reconcile failed: %s", e)
                    if self._stop.wait(LEASE_RENEW_S):
                        break

            self._reconciler = threading.Thread(
                target=loop, name="connector-reconciler", daemon=True)
            self._reconciler.start()

    def shutdown(self) -> None:
        """Stop every connector and hand back the leases.

        Called from the app's shutdown hook. Without the release, a rolling deploy
        leaves each lease to time out, and for those ~24 s nobody polls those
        devices — a visible gap in the history of every site.
        """
        self._stop.set()
        reconciler = self._reconciler
        if reconciler is not None and reconciler.is_alive():
            reconciler.join(timeout=3.0)
        with self._lock:
            for connector_id in list(self._running):
                self._stop_locked(connector_id)

    # -- manual control -----------------------------------------------------

    def start_one(self, connector_id: str) -> dict:
        config = get(connector_id)
        if config is None:
            raise KeyError(connector_id)
        if not self._leases.acquire(connector_id):
            return {"started": False,
                    "reason": "another task owns this connector's lease"}
        with self._lock:
            self._stop_locked(connector_id)
            connector = build_connector(config)
            connector.start()
            self._running[connector_id] = connector
        return {"started": True}

    def stop_one(self, connector_id: str) -> dict:
        with self._lock:
            was_running = connector_id in self._running
            self._stop_locked(connector_id)
        return {"stopped": was_running}

    def health(self, connector_id: str) -> dict | None:
        with self._lock:
            connector = self._running.get(connector_id)
            error = self._errors.get(connector_id)
        if connector is not None:
            return {**connector.snapshot(), "owned_by_this_task": True}
        if error:
            return {"connector_id": connector_id, "state": "error",
                    "connected": False, "last_error": error,
                    "owned_by_this_task": False}
        return None

    def stats(self) -> dict:
        with self._lock:
            running = {cid: c.snapshot() for cid, c in self._running.items()}
        return {
            "owner": self._leases.owner,
            "running": len(running),
            "owned": self._leases.held(),
            "last_reconcile": self._last_reconcile,
            "errors": dict(self._errors),
            "connectors": running,
        }


_manager: ConnectorManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> ConnectorManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ConnectorManager()
    return _manager


def protocol_availability() -> list[dict]:
    """Which protocols can actually run here, and why not when they cannot.

    Surfaced by the API because "the MQTT connector does nothing" is otherwise a
    long debugging session that ends in a missing pip package. Naming the package
    turns it into one command.
    """
    packages = {
        "modbus_tcp": "pymodbus>=3.6",
        "modbus_rtu": "pymodbus>=3.6",
        "mqtt": "paho-mqtt>=2.0 (plus protobuf for Sparkplug B)",
        "opcua": "asyncua>=1.0",
    }
    out = []
    for protocol, package in packages.items():
        ok = protocol in available_protocols()
        entry = {"protocol": protocol, "available": ok, "package": package}
        if not ok:
            entry["error"] = _IMPORT_ERRORS.get(
                protocol, "client library not installed")
            entry["install"] = f"pip install {package.split()[0]}"
        out.append(entry)
    return out
