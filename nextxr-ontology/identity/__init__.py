"""identity — who the caller is, and which organisation they act for.

WHAT THIS REPLACED
------------------
The platform's only credential used to be `NXR_API_KEYS`, a JSON blob in an
environment variable. Adding a customer meant editing it and rolling the fleet;
revoking one meant the same; and the change log's `actor` column pointed at
nothing, because there were no users. There was no login at all.

    routes            server/auth_routes.py   HTTP surface
    ------------------------------------------------------------------
    service.py        the rules: login, refresh, roles, reset, bootstrap
    resolve.py        credential -> Principal (the two doors)
    store.py          every SQL statement against the identity tables
    models.py         the records, and the role ladder
    passwords.py      hashing, strength policy, opaque secrets
    tokens.py         the signed access token and the signing key

Nothing outside `store.py` names a column, and nothing outside `resolve.py`
turns a credential into a Principal — so authentication has one shape and
authorization has one input.

WHERE AUTHORIZATION ACTUALLY HAPPENS
------------------------------------
Not here. `server/tenancy.py` is still the single enforcement point for tenant
access; this package changes what it resolves AGAINST — a real `org_tenants`
lookup instead of a string-prefix convention over an environment variable. The
one exception is account-level permission (who may change a role, issue a key,
remove a member), which is `service._require_role` because it is about orgs
rather than tenants.
"""
from __future__ import annotations

from .models import (  # noqa: F401
    API_KEY_ROLES,
    ROLE_ADMIN,
    ROLE_OWNER,
    ROLE_READ,
    ROLE_WRITE,
    ROLES,
    ApiKeyRecord,
    Membership,
    Organization,
    Principal,
    Session,
    User,
    normalize_role,
    role_at_least,
    role_rank,
)
from .resolve import (  # noqa: F401
    anonymous_principal,
    clear_cache,
    platform_admin_principal,
    principal_for_device,
    principal_from_access_token,
    principal_from_api_key,
    tenants_for,
)
from .service import (  # noqa: F401
    AuthFailed,
    Conflict,
    Forbidden,
    bootstrap_admin,
    posture,
    signup_allowed,
)

__all__ = [
    "API_KEY_ROLES", "ROLES", "ROLE_ADMIN", "ROLE_OWNER", "ROLE_READ",
    "ROLE_WRITE", "ApiKeyRecord", "AuthFailed", "Conflict", "Forbidden",
    "Membership", "Organization", "Principal", "Session", "User",
    "anonymous_principal", "bootstrap_admin", "clear_cache",
    "normalize_role", "platform_admin_principal", "posture",
    "principal_for_device", "principal_from_access_token",
    "principal_from_api_key", "role_at_least", "role_rank", "signup_allowed",
    "tenants_for",
]


def log_posture() -> None:
    """Print the identity posture lines. Called from AuthMiddleware's constructor
    next to the other `[component]` posture lines, so one boot log answers "is
    this deployment configured correctly"."""
    for line in posture():
        print(line, flush=True)
