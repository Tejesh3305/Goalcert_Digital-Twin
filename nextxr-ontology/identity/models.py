"""models.py — the identity records, as plain frozen dataclasses.

These are read models: what a row means once it has left the database. They are
frozen because an authorization decision must not be able to mutate the record it
was derived from, and they carry no persistence logic — `store.py` owns that, so
there is exactly one module that knows the column names.

ROLES
-----
Four, ordered, and the SAME vocabulary as the API-key roles that predate this
module — so `tenancy.Scope.role` means one thing whether the caller arrived with
a session or a key, and there is no translation table to get wrong.

    owner   the org's billing/ownership role: everything admin can do, plus
            managing members and deleting the organisation itself.
    admin   full read/write across every tenant the org owns, and can issue and
            revoke API keys.
    write   read + write within the org's tenants. Cannot manage people or keys.
    read    read-only within the org's tenants.

`owner` is separate from `admin` because "can change who has access" is a
different blast radius from "can change the data", and collapsing them is how an
ordinary operator ends up able to lock out the account holder.

A membership carries a SECOND role on a different axis — `persona`
(supervisor / frontline), the job the person does rather than the data they may
touch. See the PERSONAS block below.

Note that NONE of these is the platform-wide superuser. That is
`User.is_platform_admin`, a column on the user rather than a role in an org,
because it is our staff rather than a customer's — and keeping it off the role
ladder means no amount of role escalation inside an org can reach it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_WRITE = "write"
ROLE_READ = "read"

ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_WRITE, ROLE_READ)

# Rank for "at least this role" comparisons. Higher is more privileged.
_RANK = {ROLE_READ: 0, ROLE_WRITE: 1, ROLE_ADMIN: 2, ROLE_OWNER: 3}


def role_rank(role: str) -> int:
    """An unknown role ranks BELOW read. A typo in a role name must lose
    privileges, never gain them."""
    return _RANK.get((role or "").strip().lower(), -1)


def role_at_least(role: str, minimum: str) -> bool:
    return role_rank(role) >= role_rank(minimum)


def normalize_role(role: str) -> str:
    """Coerce to a known role, defaulting to the least privileged."""
    candidate = (role or "").strip().lower()
    return candidate if candidate in ROLES else ROLE_READ


# The roles an API key may carry. A key is a machine credential, so it never gets
# `owner`: issuing a key that can issue more keys turns one leaked secret into
# permanent access that survives revoking it.
API_KEY_ROLES = (ROLE_ADMIN, ROLE_WRITE, ROLE_READ)


# ── Personas: the OTHER role vocabulary ─────────────────────────────────
#
# The four roles above are the DATA ladder: what a caller may read and write.
# A persona is the OPERATIONAL role: what job the person does.
#
#     supervisor  dispatches faults to the people on their team
#     frontline   fixes what they are dispatched, and learns doing it
#
# These are deliberately NOT rungs on the role ladder, and the temptation to
# make them rungs is worth resisting explicitly. "Supervisor = admin" breaks the
# moment you meet a shift supervisor who may dispatch people but has no business
# editing the plant model — and "operator = write" breaks on the operator whose
# closing a job must write back. Dispatch authority and data authority vary
# independently, so they are two columns rather than one.
#
# `work/authority.py` holds what each persona MAY DO; this is only the
# vocabulary, because the column lives on Membership and Membership lives here.
PERSONA_SUPERVISOR = "supervisor"
PERSONA_FRONTLINE = "frontline"

PERSONAS = (PERSONA_SUPERVISOR, PERSONA_FRONTLINE)

#: The persona an account gets when nobody has said otherwise. Deliberately the
#: one that cannot dispatch work: a defaulting bug must not mint supervisors.
DEFAULT_PERSONA = PERSONA_FRONTLINE


def normalize_persona(persona: str) -> str:
    """Coerce to a known persona, defaulting to the least privileged.

    Same rule as `normalize_role`: an unrecognised value LOSES authority rather
    than gaining it, so a typo in a seed script or an API payload produces an
    operator, never a supervisor.
    """
    candidate = (persona or "").strip().lower()
    return candidate if candidate in PERSONAS else DEFAULT_PERSONA


@dataclass(frozen=True)
class Organization:
    org_id: str
    name: str
    plan: str = "trial"
    status: str = "active"
    tenant_prefix: str = ""
    settings: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    name: str = ""
    status: str = "active"
    is_platform_admin: bool = False
    email_verified_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_login_at: str | None = None
    failed_logins: int = 0
    locked_until: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def email_verified(self) -> bool:
        return bool(self.email_verified_at)

    def public(self) -> dict:
        """The representation safe to return over the API. Allow-listed rather
        than deny-listed: a field added to this dataclass must be deliberately
        published, not published by default."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "name": self.name,
            "status": self.status,
            "is_platform_admin": self.is_platform_admin,
            "email_verified": self.email_verified,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


@dataclass(frozen=True)
class Membership:
    """What one user is, inside one organisation — on BOTH axes.

    `role` is the data ladder, `persona` is the job. See the PERSONAS block
    above for why one column could not carry both.
    """

    org_id: str
    user_id: str
    role: str = ROLE_READ
    persona: str = DEFAULT_PERSONA
    created_at: str = ""

    @property
    def is_supervisor(self) -> bool:
        return self.persona == PERSONA_SUPERVISOR

    def public(self) -> dict:
        return {"org_id": self.org_id, "user_id": self.user_id,
                "role": self.role, "persona": self.persona}


@dataclass(frozen=True)
class ApiKeyRecord:
    """A key as stored. The secret itself is NOT here — only its hash lives in the
    database, and the plaintext exists once, in the response that created it."""
    key_id: str
    org_id: str
    prefix: str
    name: str = ""
    role: str = ROLE_READ
    tenants: tuple[str, ...] = ()
    created_by: str = ""
    created_at: str = ""
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None

    def public(self) -> dict:
        return {
            "key_id": self.key_id,
            "org_id": self.org_id,
            "prefix": self.prefix,
            "name": self.name,
            "role": self.role,
            "tenants": list(self.tenants),
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_used_at": self.last_used_at,
            "active": self.revoked_at is None,
        }


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    org_id: str | None
    issued_at: str
    expires_at: str
    revoked_at: str | None = None
    rotated_from: str | None = None
    ip: str | None = None
    user_agent: str | None = None

    def public(self) -> dict:
        return {
            "session_id": self.session_id,
            "org_id": self.org_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "ip": self.ip,
            "user_agent": (self.user_agent or "")[:200],
            "active": self.revoked_at is None,
        }


@dataclass(frozen=True)
class Principal:
    """WHO a request is, after authentication — from either credential type.

    This is the join point that lets one authorization path serve both. A browser
    session and a machine key produce the same shape, so `server/tenancy.py`
    builds a Scope from a Principal without caring which door the caller came
    through, and a route never has to ask.
    """
    kind: str                        # "user" | "api_key" | "device"
    user_id: str | None = None
    session_id: str | None = None
    key_id: str | None = None
    org_id: str | None = None
    role: str = ROLE_READ
    email: str = ""
    is_platform_admin: bool = False
    # Tenants this principal may reach. None means "every tenant the org owns",
    # resolved by the store; an explicit tuple is a key narrowed to a subset.
    tenants: tuple[str, ...] | None = None
    label: str = ""

    @property
    def can_write(self) -> bool:
        return role_at_least(self.role, ROLE_WRITE)

    @property
    def can_administer(self) -> bool:
        return role_at_least(self.role, ROLE_ADMIN)

    @property
    def can_own(self) -> bool:
        return role_at_least(self.role, ROLE_OWNER)
