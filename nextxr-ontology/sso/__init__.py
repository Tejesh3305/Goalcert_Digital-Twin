"""sso — Goalcert Hub single sign-on (the satellite side).

A signed-in Hub user clicks "Open Digital Twin". The Hub mints a short-lived,
single-use, audience-bound HS256 ticket and sends the browser to
`/sso/hub/callback`. This package turns that ticket into an ordinary session.

    config.py     settings, and the guard against sharing the wrong secret
    verifier.py   is the ticket real, and is it for us          (6 checks, in order)
    resolver.py   which EXISTING local user is it               (never creates one)
    redirect.py   where the browser may land                    (relative, same-origin)
    store.py      spent ticket ids, and the sub -> user_id link

The route lives in `server/sso_routes.py`, matching the reference implementation's
split of `routes/sso.php` from `app/Sso/Services/`.

WHAT "START A SESSION" MEANS HERE
---------------------------------
It means calling the SAME function a password login calls —
`identity.service._issue_session()` — so what comes out is a real session row, a
real refresh cookie and a real access token. Nothing downstream can tell the
difference, which is the requirement: `server/tenancy.py`, the audit log and
`/auth/me` all behave exactly as they would after someone typed a password.
"""

from __future__ import annotations

from .config import audience, enabled, issuer, log_posture, posture  # noqa: F401
from .redirect import is_safe as is_safe_redirect  # noqa: F401
from .resolver import Resolution, ResolutionError, resolve  # noqa: F401
from .verifier import Ticket, TicketError, verify  # noqa: F401

__all__ = [
    "Resolution", "ResolutionError", "Ticket", "TicketError",
    "audience", "enabled", "is_safe_redirect", "issuer", "log_posture",
    "posture",
    "resolve", "verify",
]
