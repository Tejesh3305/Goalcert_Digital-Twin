"""
devices.py — the ingest device registry: who is allowed to push telemetry.

WHY DEVICES ARE NOT API KEYS
----------------------------
A tenant API key is a human's credential for a whole twin: it reads every asset,
drives the physics runtime, deletes entities, and bills the LLM endpoints. A
telemetry gateway is the opposite kind of principal — it lives in a plant room on
hardware anyone with a screwdriver can walk off with, it never needs to read
anything, and it will be revoked and re-issued far more often than a human's
credential. Giving it a tenant key means one stolen gateway is a full compromise
of that customer's twin, and rotating it breaks every dashboard at the same time.

So a device credential grants exactly one capability — append telemetry — in
exactly one tenant, optionally narrowed to an asset-id prefix, and it can be
disabled on its own.

WHY ONLY THE HASH IS STORED
---------------------------
The plaintext token is returned once, at creation, and is then unrecoverable. A
registry an operator can read back is a registry an attacker can read once; the
same reasoning behind `~/.ssh/authorized_keys` holding public keys and GitHub
showing a PAT exactly once. "Show me the token again" is not a feature, it is the
breach.

The hash is SHA-256 and deliberately NOT a slow KDF like bcrypt. Password hashes
must be slow because passwords are low-entropy and guessable. These tokens are 256
bits from `secrets.token_urlsafe`, so brute force is not the threat model — and a
deliberately slow hash on the ingest hot path would cap throughput at a few
hundred batches per second per task for no security gain. Lookup is by hash, so
authentication is one indexed read.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import db
from db import schema as db_schema

_STORE = "devices"

# Rendered into the token so an operator finding one in a log or a config file
# can tell what it is. Deliberately distinctive: it also lets a secret scanner
# (and `git-secrets` / GitHub push protection) match on it.
TOKEN_PREFIX = "nxrd_"

_DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_schema() -> None:
    db_schema.ensure(_STORE)


def hash_token(token: str) -> str:
    """SHA-256 of the presented token, hex. See the module docstring for why this
    is not bcrypt."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Device:
    device_id: str
    tenant_id: str
    name: str
    asset_prefix: str
    enabled: bool
    created_at: str
    created_by: str
    expires_at: str | None = None
    last_seen_at: str | None = None
    last_seen_ip: str | None = None
    samples_total: int = 0
    rejected_total: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expired"] = self.is_expired()
        d["active"] = self.enabled and not self.is_expired()
        return d

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            # An unparseable expiry is treated as EXPIRED, not as "no expiry".
            # Failing closed on corrupt data is the only safe reading: the
            # alternative silently grants a credential someone tried to time-box.
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return exp <= (now or datetime.now(UTC))

    def allows_asset(self, asset_id: str) -> bool:
        return not self.asset_prefix or asset_id.startswith(self.asset_prefix)


def _row_to_device(row) -> Device:
    r = dict(row)
    return Device(
        device_id=r["device_id"], tenant_id=r["tenant_id"],
        name=r.get("name") or "", asset_prefix=r.get("asset_prefix") or "",
        enabled=bool(r.get("enabled", 1)), created_at=r.get("created_at") or "",
        created_by=r.get("created_by") or "",
        expires_at=r.get("expires_at"), last_seen_at=r.get("last_seen_at"),
        last_seen_ip=r.get("last_seen_ip"),
        samples_total=int(r.get("samples_total") or 0),
        rejected_total=int(r.get("rejected_total") or 0),
    )


# ── Provisioning ────────────────────────────────────────────────────────────


def create(*, tenant_id: str, name: str = "", device_id: str | None = None,
           asset_prefix: str = "", ttl_days: int | None = None,
           created_by: str = "api") -> tuple[Device, str]:
    """Register a device. Returns (device, PLAINTEXT_TOKEN).

    The token is the only time the caller sees it. Store it in the gateway's
    config and treat it as a secret.
    """
    _ensure_schema()
    if not tenant_id:
        raise ValueError("tenant_id is required")

    device_id = (device_id or f"dev-{secrets.token_hex(6)}").strip().lower()
    if not _DEVICE_ID_RE.match(device_id):
        raise ValueError(
            "device_id must be 3-64 chars of [a-z0-9._-] and start alphanumeric")

    if get(device_id) is not None:
        raise ValueError(f"device '{device_id}' already exists")

    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = None
    if ttl_days:
        expires_at = (datetime.now(UTC)
                      + timedelta(days=int(ttl_days))).isoformat()

    with db.connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO ingest_devices (device_id, tenant_id, name, token_hash, "
            "asset_prefix, enabled, created_at, created_by, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (device_id, tenant_id, name or device_id, hash_token(token),
             asset_prefix or "", _now_iso(), created_by, expires_at))

    device = get(device_id)
    assert device is not None
    return device, token


def rotate(device_id: str) -> str:
    """Issue a new token for an existing device, invalidating the old one.
    Returns the new plaintext. Rotation is per device, which is the point of a
    per-device credential: replacing one gateway's token does not touch any
    other device or any human's key."""
    _ensure_schema()
    if get(device_id) is None:
        raise KeyError(device_id)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with db.connect(_STORE) as conn:
        conn.execute("UPDATE ingest_devices SET token_hash = ? WHERE device_id = ?",
                     (hash_token(token), device_id))
    return token


def set_enabled(device_id: str, enabled: bool) -> bool:
    _ensure_schema()
    with db.connect(_STORE) as conn:
        cur = conn.execute(
            "UPDATE ingest_devices SET enabled = ? WHERE device_id = ?",
            (1 if enabled else 0, device_id))
        return bool(getattr(cur, "rowcount", 0))


def delete(device_id: str) -> bool:
    _ensure_schema()
    with db.connect(_STORE) as conn:
        cur = conn.execute("DELETE FROM ingest_devices WHERE device_id = ?",
                           (device_id,))
        return bool(getattr(cur, "rowcount", 0))


def get(device_id: str) -> Device | None:
    _ensure_schema()
    with db.connect(_STORE) as conn:
        row = conn.execute(
            "SELECT * FROM ingest_devices WHERE device_id = ?",
            (device_id,)).fetchone()
    return _row_to_device(row) if row else None


def list_for_tenant(tenant_id: str | None = None) -> list[Device]:
    _ensure_schema()
    with db.connect(_STORE) as conn:
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM ingest_devices WHERE tenant_id = ? "
                "ORDER BY created_at DESC", (tenant_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ingest_devices ORDER BY created_at DESC"
            ).fetchall()
    return [_row_to_device(r) for r in rows]


# ── Authentication ──────────────────────────────────────────────────────────


class DeviceAuthError(Exception):
    """Device credential rejected. The message is deliberately coarse — see
    `authenticate`."""


def authenticate(token: str, *, ip: str = "") -> Device:
    """Resolve a presented token to its Device, or raise DeviceAuthError.

    Every failure mode returns the SAME message. An error that distinguished
    "unknown token" from "device disabled" from "token expired" would confirm to
    an attacker that a token is real, which is exactly the fact a stolen-hardware
    scenario turns on.
    """
    generic = DeviceAuthError("Invalid or inactive device token")
    if not token or not token.strip():
        raise generic

    _ensure_schema()
    presented = hash_token(token.strip())
    try:
        with db.connect(_STORE) as conn:
            row = conn.execute(
                "SELECT * FROM ingest_devices WHERE token_hash = ?",
                (presented,)).fetchone()
    except Exception as e:                     # store unreachable
        raise DeviceAuthError(f"device registry unavailable: {e}") from e

    if row is None:
        raise generic

    device = _row_to_device(row)

    # The lookup already matched on a SHA-256 of the full token, so this is not
    # the security boundary — it is a constant-time confirmation that keeps the
    # comparison from becoming a timing side channel if the lookup is ever
    # changed to fetch by device_id and compare in Python.
    if not hmac.compare_digest(presented, dict(row)["token_hash"]):
        raise generic
    if not device.enabled or device.is_expired():
        raise generic

    return device


def note_activity(device_id: str, *, accepted: int = 0, rejected: int = 0,
                  ip: str = "") -> None:
    """Record that a device was heard from.

    Best-effort and never raises: a failure to update a counter must not fail an
    ingest that already succeeded. `last_seen_at` is what makes a silent gateway
    visible — the most common real-world ingest fault is not bad data, it is a
    device that simply stopped talking and nobody noticed.
    """
    try:
        with db.connect(_STORE) as conn:
            conn.execute(
                "UPDATE ingest_devices SET last_seen_at = ?, last_seen_ip = ?, "
                "samples_total = samples_total + ?, "
                "rejected_total = rejected_total + ? WHERE device_id = ?",
                (_now_iso(), ip[:64], float(accepted), float(rejected), device_id))
    except Exception:
        pass


# ── Diagnostics ─────────────────────────────────────────────────────────────


def stats() -> dict:
    """Fleet summary for /health and the ingest status endpoint."""
    try:
        _ensure_schema()
        with db.connect(_STORE) as conn:
            row = conn.execute(
                "SELECT count(*) AS total, "
                "       sum(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled, "
                "       sum(samples_total) AS samples, "
                "       sum(rejected_total) AS rejected "
                "FROM ingest_devices").fetchone()
        r = dict(row or {})
        return {"devices": int(r.get("total") or 0),
                "enabled": int(r.get("enabled") or 0),
                "samples_total": int(r.get("samples") or 0),
                "rejected_total": int(r.get("rejected") or 0)}
    except Exception as e:
        return {"error": str(e)[:160]}


def silent_devices(threshold_s: int = 900) -> list[dict]:
    """Devices that have not reported within `threshold_s`.

    A gateway that stops publishing produces no error anywhere — the charts just
    stop moving, and the twin keeps serving its last known value as if it were
    current. This is the query that turns that into an alert.
    """
    now = datetime.now(UTC)
    out = []
    for d in list_for_tenant():
        if not d.enabled:
            continue
        if not d.last_seen_at:
            out.append({**d.to_dict(), "silent_for_s": None,
                        "reason": "never reported"})
            continue
        try:
            seen = datetime.fromisoformat(d.last_seen_at.replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=UTC)
        except ValueError:
            continue
        age = (now - seen).total_seconds()
        if age > threshold_s:
            out.append({**d.to_dict(), "silent_for_s": round(age, 1),
                        "reason": "no data within threshold"})
    return out
