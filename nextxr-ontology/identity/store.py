"""store.py — every read and write against the identity tables.

ONE module knows the column names. Routes call `service.py`, `service.py` calls
this, and nothing else touches the `users` / `organizations` / `sessions` /
`api_keys` tables. That is what makes the schema movable and what keeps
authorization decisions from being spread across handlers.

WRITTEN AGAINST `db/core.py`, SO IT RUNS ON BOTH BACKENDS
---------------------------------------------------------
SQLite-flavoured SQL with `?` placeholders; `db.core` rewrites for PyMySQL. The
portable-by-construction subset (`ON CONFLICT ... DO UPDATE`, `CURRENT_TIMESTAMP`)
is the same one the other stores use — see the note at the top of `db/core.py`.
Timestamps are ISO-8601 UTC strings written by Python rather than by the
database, because `datetime('now')` and `NOW()` are not the same function and the
column type differs between backends.

LOOKUPS ARE BY HASH, ALWAYS
---------------------------
An API key and a refresh token arrive as bare secrets — there is no id alongside
them to narrow a query. So both are found by `WHERE ..._hash = ?` against a
UNIQUE index. This is the reason `passwords.hash_secret()` is a plain peppered
SHA-256 rather than a KDF; the reasoning is written out there.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from db import Json, connect, json_load, schema

from .models import (
    ApiKeyRecord,
    Membership,
    Organization,
    Session,
    User,
    normalize_role,
)
from .passwords import hash_secret

_STORE = "identity"


# ── Small helpers ───────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _future(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _is_past(value: str | None) -> bool:
    """Whether an ISO timestamp is in the past. A missing value is NOT past —
    a NULL `expires_at` means "never expires", which is the common case for an
    API key. An UNPARSEABLE value is treated as past: a corrupt expiry must
    revoke access, not grant it forever.
    """
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def normalize_email(email: str) -> str:
    """Lowercased and stripped. Case-folding the domain is required by DNS; doing
    it to the local part too is a product decision, and the right one — users do
    not expect Alice@ and alice@ to be different accounts, and treating them as
    such is a duplicate-account and account-takeover-confusion source."""
    return (email or "").strip().lower()


def _ensure() -> None:
    schema.ensure(_STORE)


def _row(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def _tenants_tuple(raw: Any) -> tuple[str, ...]:
    value = json_load(raw) or []
    if not isinstance(value, list):
        return ()
    return tuple(str(t) for t in value if t)


# ── Organizations ───────────────────────────────────────────────────────


def slugify(value: str) -> str:
    """An org id from a display name: lowercase, [a-z0-9-], no leading/trailing
    or doubled dashes. Kept strict because this string becomes a tenant PREFIX,
    so anything that could carry path or query syntax is removed rather than
    escaped downstream."""
    out: list[str] = []
    for ch in (value or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_.":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:40]


# ── The reserved organisation that owns the platform's shared twins ─────
#
# WHY AN ORGANISATION AND NOT A LIST IN THE ENVIRONMENT. "Which twins does every
# account land on" is the same question as "who owns this twin", and this codebase
# already has one answer for that: a row in `org_tenants`. Expressing it as an env
# var would add a SECOND authorization source that the audit log, the ownership
# report and `tenants_for()` all have to learn about separately — and would make
# the answer differ between the local database and the deployed one, which is
# exactly how a demo ends up empty in front of a client.
#
# THE ID CANNOT COLLIDE WITH A REAL CUSTOMER'S. `slugify()` emits only
# [a-z0-9-], so no organisation created from a company name can ever be called
# "nxr:shared" — the colon is unproducible. A customer signing up as "NXR Shared"
# becomes `nxr-shared`, a different row.
SHARED_ORG_ID = "nxr:shared"
_SHARED_ORG_NAME = "Shared demo twins"


def ensure_shared_org() -> Organization:
    """Create the reserved shared organisation if it is missing. Idempotent.

    Inserted directly rather than through `create_org()`, which slugifies the id
    it is given — that is the right behaviour for a customer name and would turn
    this id into "nxrshared", quietly defeating the collision guarantee above.
    """
    _ensure()
    existing = get_org(SHARED_ORG_ID)
    if existing is not None:
        return existing
    now = _now()
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO organizations (org_id, name, plan, status, "
            "tenant_prefix, settings, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (org_id) DO NOTHING",
            (SHARED_ORG_ID, _SHARED_ORG_NAME, "internal", "active",
             f"{SHARED_ORG_ID}-", Json({"shared": True}), now, now))
    return get_org(SHARED_ORG_ID) or Organization(
        org_id=SHARED_ORG_ID, name=_SHARED_ORG_NAME, plan="internal",
        status="active", tenant_prefix=f"{SHARED_ORG_ID}-", settings={},
        created_at=now, updated_at=now)


def share_tenant(tenant_id: str) -> None:
    """Make one twin common to every account. Never reassigns: a twin a real
    organisation already owns stays theirs (see `claim_tenant`)."""
    ensure_shared_org()
    claim_tenant(tenant_id, SHARED_ORG_ID)


def create_org(name: str, *, org_id: str = "", plan: str = "trial") -> Organization:
    _ensure()
    base = slugify(org_id or name) or "org"
    candidate = base
    # Collision suffix. Two customers called "Acme" is ordinary, and failing the
    # second signup with "that name is taken" would be a strange thing to say
    # about a company name.
    for attempt in range(1, 100):
        if get_org(candidate) is None:
            break
        candidate = f"{base}-{attempt}"
    else:
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"

    now = _now()
    org = Organization(
        org_id=candidate, name=(name or candidate).strip()[:200], plan=plan,
        status="active", tenant_prefix=f"{candidate}-", settings={},
        created_at=now, updated_at=now,
    )
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO organizations (org_id, name, plan, status, "
            "tenant_prefix, settings, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (org.org_id, org.name, org.plan, org.status, org.tenant_prefix,
             Json(org.settings), org.created_at, org.updated_at),
        )
    return org


def get_org(org_id: str) -> Organization | None:
    if not org_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM organizations WHERE org_id = ?", (org_id,)))
    if not row:
        return None
    return Organization(
        org_id=row["org_id"], name=row["name"], plan=row["plan"],
        status=row["status"], tenant_prefix=row["tenant_prefix"] or "",
        settings=json_load(row["settings"]) or {},
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def list_orgs(limit: int = 200) -> list[Organization]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT org_id FROM organizations ORDER BY created_at DESC LIMIT ?",
            (limit,)))
    return [org for org in (get_org(r["org_id"]) for r in rows) if org]


def update_org(org_id: str, *, name: str | None = None,
               plan: str | None = None,
               status: str | None = None,
               settings: dict | None = None) -> Organization | None:
    org = get_org(org_id)
    if org is None:
        return None
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE organizations SET name = ?, plan = ?, status = ?, "
            "settings = ?, updated_at = ? WHERE org_id = ?",
            (name if name is not None else org.name,
             plan if plan is not None else org.plan,
             status if status is not None else org.status,
             Json(settings if settings is not None else org.settings),
             _now(), org_id),
        )
    return get_org(org_id)


# ── Users ───────────────────────────────────────────────────────────────


def _user_from_row(row: dict) -> User:
    return User(
        user_id=row["user_id"], email=row["email"], name=row["name"] or "",
        status=row["status"], is_platform_admin=bool(row["is_platform_admin"]),
        email_verified_at=row["email_verified_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
        failed_logins=int(row["failed_logins"] or 0),
        locked_until=row["locked_until"],
    )


def create_user(email: str, password_hash: str, *, name: str = "",
                status: str = "active",
                is_platform_admin: bool = False,
                email_verified: bool = False) -> User:
    _ensure()
    address = normalize_email(email)
    now = _now()
    user_id = _new_id("usr")
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO users (user_id, email, password_hash, name, status, "
            "is_platform_admin, email_verified_at, created_at, updated_at, "
            "failed_logins) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (user_id, address, password_hash, (name or "").strip()[:200], status,
             1 if is_platform_admin else 0, now if email_verified else None,
             now, now),
        )
    user = get_user(user_id)
    assert user is not None                       # just inserted
    return user


def get_user(user_id: str) -> User | None:
    if not user_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)))
    return _user_from_row(row) if row else None


def get_user_by_email(email: str) -> User | None:
    address = normalize_email(email)
    if not address:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM users WHERE email = ?", (address,)))
    return _user_from_row(row) if row else None


def get_password_hash(user_id: str) -> str:
    """The stored hash, fetched separately from the User record.

    Deliberately not a field on `User`: that object is passed to routes and
    serialised in responses, and a credential that is never loaded into the read
    model cannot be leaked by a `public()` that forgets to exclude it.
    """
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT password_hash FROM users WHERE user_id = ?", (user_id,)))
    return (row or {}).get("password_hash") or ""


def set_password_hash(user_id: str, password_hash: str) -> None:
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ?, "
            "failed_logins = 0, locked_until = NULL WHERE user_id = ?",
            (password_hash, _now(), user_id),
        )


def update_user(user_id: str, *, name: str | None = None,
                status: str | None = None,
                is_platform_admin: bool | None = None) -> User | None:
    user = get_user(user_id)
    if user is None:
        return None
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE users SET name = ?, status = ?, is_platform_admin = ?, "
            "updated_at = ? WHERE user_id = ?",
            (name if name is not None else user.name,
             status if status is not None else user.status,
             (1 if is_platform_admin else 0) if is_platform_admin is not None
             else (1 if user.is_platform_admin else 0),
             _now(), user_id),
        )
    return get_user(user_id)


def mark_email_verified(user_id: str) -> None:
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE users SET email_verified_at = ?, updated_at = ? "
            "WHERE user_id = ? AND email_verified_at IS NULL",
            (_now(), _now(), user_id),
        )


def record_login_success(user_id: str) -> None:
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ?, failed_logins = 0, "
            "locked_until = NULL, updated_at = ? WHERE user_id = ?",
            (_now(), _now(), user_id),
        )


# Lockout policy. Slow enough to stop online guessing, short enough that a user
# who fat-fingers their password five times is not calling support. The offline
# threat is handled by the KDF, not by this.
MAX_FAILED_LOGINS = 8
LOCKOUT_SECONDS = 15 * 60


def record_login_failure(user_id: str) -> int:
    """Increment the failure counter and lock the account when it trips.

    Returns the new count. The increment happens in SQL rather than
    read-modify-write so concurrent guesses from a distributed source all count,
    instead of racing and losing increments — which would silently raise the real
    threshold well above MAX_FAILED_LOGINS.
    """
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE users SET failed_logins = failed_logins + 1, updated_at = ? "
            "WHERE user_id = ?", (_now(), user_id))
        row = _row(conn.execute(
            "SELECT failed_logins FROM users WHERE user_id = ?", (user_id,)))
        count = int((row or {}).get("failed_logins") or 0)
        if count >= MAX_FAILED_LOGINS:
            conn.execute(
                "UPDATE users SET locked_until = ? WHERE user_id = ?",
                (_future(LOCKOUT_SECONDS), user_id))
    return count


def is_locked(user: User) -> bool:
    return bool(user.locked_until) and not _is_past(user.locked_until)


def list_users(limit: int = 500) -> list[User]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)))
    return [_user_from_row(r) for r in rows]


def count_users() -> int:
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute("SELECT COUNT(*) AS n FROM users"))
    return int((row or {}).get("n") or 0)


# ── Memberships ─────────────────────────────────────────────────────────


def add_member(org_id: str, user_id: str, role: str) -> Membership:
    _ensure()
    membership = Membership(org_id=org_id, user_id=user_id,
                            role=normalize_role(role), created_at=_now())
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO memberships (org_id, user_id, role, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (org_id, user_id) DO UPDATE SET role = excluded.role",
            (membership.org_id, membership.user_id, membership.role,
             membership.created_at),
        )
    return membership


def get_membership(org_id: str, user_id: str) -> Membership | None:
    if not org_id or not user_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM memberships WHERE org_id = ? AND user_id = ?",
            (org_id, user_id)))
    if not row:
        return None
    return Membership(org_id=row["org_id"], user_id=row["user_id"],
                      role=row["role"], created_at=row["created_at"])


def list_memberships_for_user(user_id: str) -> list[Membership]:
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM memberships WHERE user_id = ? ORDER BY created_at",
            (user_id,)))
    return [Membership(org_id=r["org_id"], user_id=r["user_id"], role=r["role"],
                       created_at=r["created_at"]) for r in rows]


def list_members(org_id: str) -> list[tuple[Membership, User]]:
    """Members of an org, each with their user record. Two queries rather than a
    join because the two tables live in one store on Postgres but separate files
    on SQLite, and a cross-file join is not available there."""
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM memberships WHERE org_id = ? ORDER BY created_at",
            (org_id,)))
    out: list[tuple[Membership, User]] = []
    for r in rows:
        user = get_user(r["user_id"])
        if user is None:
            continue                              # membership outlived the user
        out.append((Membership(org_id=r["org_id"], user_id=r["user_id"],
                               role=r["role"], created_at=r["created_at"]),
                    user))
    return out


def remove_member(org_id: str, user_id: str) -> None:
    with connect(_STORE) as conn:
        conn.execute(
            "DELETE FROM memberships WHERE org_id = ? AND user_id = ?",
            (org_id, user_id))


def count_owners(org_id: str) -> int:
    """How many owners an org has. `service.py` uses this to refuse the change
    that removes the last one — an org nobody can administer is unrecoverable
    without a platform admin, and support tickets are not an access model."""
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT COUNT(*) AS n FROM memberships WHERE org_id = ? AND role = ?",
            (org_id, "owner")))
    return int((row or {}).get("n") or 0)


# ── Org ↔ tenant ownership ──────────────────────────────────────────────


def claim_tenant(tenant_id: str, org_id: str) -> None:
    """Record that `org_id` owns `tenant_id`.

    ON CONFLICT DO NOTHING, not DO UPDATE: a tenant already claimed by another
    org must NOT be silently reassigned by whoever writes second. Transferring
    ownership is a deliberate operation (`transfer_tenant`) precisely so it
    cannot happen as a side effect of twin creation.
    """
    if not tenant_id or not org_id:
        return
    _ensure()
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO org_tenants (tenant_id, org_id, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT (tenant_id) DO NOTHING",
            (tenant_id, org_id, _now()))


def transfer_tenant(tenant_id: str, org_id: str) -> None:
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO org_tenants (tenant_id, org_id, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT (tenant_id) DO UPDATE SET "
            "org_id = excluded.org_id",
            (tenant_id, org_id, _now()))


def release_tenant(tenant_id: str) -> None:
    with connect(_STORE) as conn:
        conn.execute("DELETE FROM org_tenants WHERE tenant_id = ?", (tenant_id,))


def tenant_owner(tenant_id: str) -> str | None:
    if not tenant_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT org_id FROM org_tenants WHERE tenant_id = ?", (tenant_id,)))
    return (row or {}).get("org_id")


def org_tenants(org_id: str) -> tuple[str, ...]:
    """Every tenant this org owns. THE authorization primitive: a Scope is built
    from this, so a tenant absent from this table is unreachable by any member."""
    if not org_id:
        return ()
    _ensure()
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(
            "SELECT tenant_id FROM org_tenants WHERE org_id = ? "
            "ORDER BY created_at", (org_id,)))
    return tuple(r["tenant_id"] for r in rows)


# ── API keys ────────────────────────────────────────────────────────────


def _key_from_row(row: dict) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_id=row["key_id"], org_id=row["org_id"], prefix=row["prefix"],
        name=row["name"] or "", role=row["role"],
        tenants=_tenants_tuple(row["tenants"]),
        created_by=row["created_by"] or "", created_at=row["created_at"],
        expires_at=row["expires_at"], revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
    )


def create_api_key(org_id: str, *, name: str = "", role: str = "read",
                   tenants: Iterable[str] = (), created_by: str = "",
                   expires_in_days: int | None = None) -> tuple[ApiKeyRecord, str]:
    """Issue a key. Returns (record, PLAINTEXT) — the plaintext is the only copy
    and is never recoverable afterwards."""
    from .passwords import generate_secret, secret_prefix

    _ensure()
    secret = generate_secret("nxr_live_", nbytes=32)
    now = _now()
    record = ApiKeyRecord(
        key_id=_new_id("key"), org_id=org_id, prefix=secret_prefix(secret, 16),
        name=(name or "").strip()[:120], role=normalize_role(role),
        tenants=tuple(t for t in tenants if t), created_by=created_by,
        created_at=now,
        expires_at=_future(expires_in_days * 86400) if expires_in_days else None,
    )
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO api_keys (key_id, org_id, key_hash, prefix, name, role, "
            "tenants, created_by, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.key_id, record.org_id, hash_secret(secret), record.prefix,
             record.name, record.role, Json(list(record.tenants)),
             record.created_by, record.created_at, record.expires_at),
        )
    return record, secret


def find_api_key(secret: str) -> ApiKeyRecord | None:
    """Resolve a presented key. Returns None for unknown, revoked AND expired —
    the caller gets one undifferentiated 401, because telling an attacker that a
    key is real but expired confirms the key is real."""
    if not secret:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (hash_secret(secret),)))
    if not row:
        return None
    record = _key_from_row(row)
    if record.revoked_at is not None or _is_past(record.expires_at):
        return None
    return record


def touch_api_key(key_id: str) -> None:
    """Record last use. Best-effort and swallowed on failure: this runs on the
    authentication hot path, and a write blip must not turn a valid credential
    into a 500."""
    try:
        with connect(_STORE) as conn:
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                         (_now(), key_id))
    except Exception:
        pass


def list_api_keys(org_id: str, *, include_revoked: bool = False) -> list[ApiKeyRecord]:
    _ensure()
    sql = "SELECT * FROM api_keys WHERE org_id = ?"
    if not include_revoked:
        sql += " AND revoked_at IS NULL"
    sql += " ORDER BY created_at DESC"
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(sql, (org_id,)))
    return [_key_from_row(r) for r in rows]


def get_api_key(key_id: str) -> ApiKeyRecord | None:
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute("SELECT * FROM api_keys WHERE key_id = ?",
                                (key_id,)))
    return _key_from_row(row) if row else None


def revoke_api_key(key_id: str) -> bool:
    with connect(_STORE) as conn:
        cursor = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? "
            "AND revoked_at IS NULL", (_now(), key_id))
        return bool(getattr(cursor, "rowcount", 0))


# ── Sessions (refresh tokens) ───────────────────────────────────────────


def _session_from_row(row: dict) -> Session:
    return Session(
        session_id=row["session_id"], user_id=row["user_id"],
        org_id=row["org_id"], issued_at=row["issued_at"],
        expires_at=row["expires_at"], revoked_at=row["revoked_at"],
        rotated_from=row["rotated_from"], ip=row["ip"],
        user_agent=row["user_agent"],
    )


def create_session(user_id: str, org_id: str | None, refresh_token: str, *,
                   ttl_seconds: int, ip: str = "", user_agent: str = "",
                   rotated_from: str = "") -> Session:
    _ensure()
    now = _now()
    session = Session(
        session_id=_new_id("ses"), user_id=user_id, org_id=org_id,
        issued_at=now, expires_at=_future(ttl_seconds),
        rotated_from=rotated_from or None, ip=ip or None,
        user_agent=(user_agent or "")[:400] or None,
    )
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, user_id, org_id, refresh_hash, "
            "issued_at, expires_at, rotated_from, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session.session_id, session.user_id, session.org_id,
             hash_secret(refresh_token), session.issued_at, session.expires_at,
             session.rotated_from, session.ip, session.user_agent),
        )
    return session


def find_session_by_refresh(refresh_token: str) -> Session | None:
    """Look up a refresh token, INCLUDING revoked and expired ones.

    Returning a revoked session on purpose: `service.refresh()` needs to see it
    to detect REUSE. A token presented after it was rotated away means the token
    leaked, and the correct response is to revoke the entire session family, not
    to return a bland 401 and let the thief keep the newer token.
    """
    if not refresh_token:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM sessions WHERE refresh_hash = ?",
            (hash_secret(refresh_token),)))
    return _session_from_row(row) if row else None


def get_session(session_id: str) -> Session | None:
    if not session_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)))
    return _session_from_row(row) if row else None


def session_is_valid(session: Session | None) -> bool:
    return bool(session) and session.revoked_at is None \
        and not _is_past(session.expires_at)


def revoke_session(session_id: str) -> None:
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE session_id = ? "
            "AND revoked_at IS NULL", (_now(), session_id))


def revoke_session_family(session_id: str) -> int:
    """Revoke a session and every session rotated from it, transitively.

    This is the response to detected refresh-token reuse. Walking the chain
    matters because rotation means the thief may hold a token several
    generations newer than the one they replayed; revoking only the presented
    session would leave them logged in and log the victim out — exactly backwards.
    """
    _ensure()
    revoked = 0
    frontier = [session_id]
    seen: set[str] = set()
    with connect(_STORE) as conn:
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_id = ? "
                "AND revoked_at IS NULL", (_now(), current))
            revoked += int(getattr(cursor, "rowcount", 0) or 0)
            children = _rows(conn.execute(
                "SELECT session_id FROM sessions WHERE rotated_from = ?",
                (current,)))
            frontier.extend(c["session_id"] for c in children
                            if c["session_id"] not in seen)
    return revoked


def revoke_user_sessions(user_id: str, *, except_session: str = "") -> int:
    """Log a user out everywhere. Called on password change, on role change and
    from "sign out other devices"."""
    _ensure()
    with connect(_STORE) as conn:
        if except_session:
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? "
                "AND session_id != ? AND revoked_at IS NULL",
                (_now(), user_id, except_session))
        else:
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? "
                "AND revoked_at IS NULL", (_now(), user_id))
        return int(getattr(cursor, "rowcount", 0) or 0)


def list_sessions(user_id: str, *, active_only: bool = True) -> list[Session]:
    _ensure()
    sql = "SELECT * FROM sessions WHERE user_id = ?"
    if active_only:
        sql += " AND revoked_at IS NULL"
    sql += " ORDER BY issued_at DESC LIMIT 100"
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(sql, (user_id,)))
    sessions = [_session_from_row(r) for r in rows]
    if active_only:
        sessions = [s for s in sessions if not _is_past(s.expires_at)]
    return sessions


def purge_expired_sessions(older_than_days: int = 60) -> int:
    """Delete long-dead session rows. Revoked/expired rows are kept for a while
    because they are the audit trail for a reuse incident; past that they are
    only table growth."""
    _ensure()
    cutoff = (datetime.now(UTC)
              - timedelta(days=older_than_days)).isoformat()
    with connect(_STORE) as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?",
                              (cutoff,))
        return int(getattr(cursor, "rowcount", 0) or 0)


# ── One-shot tokens (verify email, reset password) ──────────────────────

PURPOSE_VERIFY = "verify_email"
PURPOSE_RESET = "password_reset"


def create_auth_token(user_id: str, purpose: str, *,
                      ttl_seconds: int = 3600) -> str:
    from .passwords import generate_secret

    _ensure()
    secret = generate_secret("nxr_tk_", nbytes=32)
    with connect(_STORE) as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token_hash, user_id, purpose, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?)",
            (hash_secret(secret), user_id, purpose, _now(),
             _future(ttl_seconds)))
    return secret


def consume_auth_token(secret: str, purpose: str) -> str | None:
    """Redeem a one-shot token, returning its user_id — or None.

    The read and the mark-as-used are one transaction, and the UPDATE carries
    `used_at IS NULL` in its WHERE clause. That makes redemption atomic: two
    simultaneous requests with the same reset link cannot both succeed, which
    they could if this checked-then-updated.
    """
    if not secret:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = ?",
            (hash_secret(secret), purpose)))
        if not row or row["used_at"] is not None or _is_past(row["expires_at"]):
            return None
        cursor = conn.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE token_hash = ? "
            "AND used_at IS NULL", (_now(), row["token_hash"]))
        if not getattr(cursor, "rowcount", 1):
            return None                            # lost the race
        return row["user_id"]


def invalidate_auth_tokens(user_id: str, purpose: str) -> None:
    """Burn any outstanding tokens of a purpose. Called after a successful reset
    so a second reset email in the same inbox is dead."""
    with connect(_STORE) as conn:
        conn.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE user_id = ? "
            "AND purpose = ? AND used_at IS NULL", (_now(), user_id, purpose))


# ── Audit log ───────────────────────────────────────────────────────────


def audit(action: str, *, org_id: str = "", actor_user: str = "",
          actor_key: str = "", target_type: str = "", target_id: str = "",
          outcome: str = "ok", ip: str = "", user_agent: str = "",
          detail: dict | None = None) -> None:
    """Append one account-level event.

    Best-effort by design: an audit write that fails must not fail the operation
    it is describing. That is the standard trade for an application-level log —
    the tamper-evident record with the opposite guarantee is the change log next
    door, which is hash-chained and part of the request's transaction.
    """
    try:
        _ensure()
        with connect(_STORE) as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, org_id, actor_user, actor_key, "
                "action, target_type, target_id, outcome, ip, user_agent, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), org_id or None, actor_user or None, actor_key or None,
                 action, target_type, target_id, outcome, ip or None,
                 (user_agent or "")[:400] or None, Json(detail or {})),
            )
    except Exception:
        pass


def list_audit(org_id: str = "", *, limit: int = 200,
               action: str = "") -> list[dict]:
    _ensure()
    clauses, params = [], []
    if org_id:
        clauses.append("org_id = ?")
        params.append(org_id)
    if action:
        clauses.append("action = ?")
        params.append(action)
    sql = "SELECT * FROM audit_log"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(min(max(int(limit), 1), 1000))
    with connect(_STORE) as conn:
        rows = _rows(conn.execute(sql, tuple(params)))
    for r in rows:
        r["detail"] = json_load(r.get("detail")) or {}
    return rows
