"""sso_routes.py — the Hub SSO callback.

    GET|POST /sso/hub/callback?token=...

The Python counterpart of `routes/sso.php` -> `HubSsoController` in the GoalCert
LMS. All the thinking lives in the `sso/` package; this file is the HTTP shape
around it — read the token, run the two steps, start the session, redirect.

NOT UNDER /api/v1
-----------------
The path is fixed by the Hub, which is already configured to send tickets to
`https://twin.goal-cert.com/sso/hub/callback`. It is a browser navigation
endpoint, not part of the JSON API, and it answers with redirects rather than
payloads — so sitting outside the API prefix is right on both counts. Two
consequences follow, and both are handled rather than inherited:

  * `server/auth.py` treats non-API GETs as public SPA routes but NOT non-API
    POSTs, so the callback path is named explicitly in `_PUBLIC_SSO_PATHS`
    there. Without that, the POST form the Hub prefers would 401 before this
    handler ever ran.
  * `server/main.py` has a catch-all `GET /{full_path:path}` that serves the SPA
    shell. FastAPI matches in registration order, so this router is included
    with the others, well before that fallback.

EVERY FAILURE IS A REDIRECT
---------------------------
The contract is explicit: any failure sends the user to the normal login page,
never a 500. A stack trace on this endpoint would be a bad experience for the
person and an information leak to everyone else, and the useful detail belongs
in the log where an operator can act on it. So the handler catches broadly on
purpose — including the failures nobody predicted — and the only thing the
browser learns is `?sso=failed`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sso
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from identity import store as identity_store
from sso import config as sso_config
from sso import redirect as sso_redirect
from sso import resolver as sso_resolver
from sso import verifier as sso_verifier

# The cookie helper is imported from the auth routes rather than reimplemented.
# The refresh cookie's name, path, TTL, HttpOnly/Secure/SameSite flags and the
# `/api/v1/auth` scoping have to match a password login EXACTLY — the SPA calls
# `/auth/refresh` on boot and finds the session by that cookie, so a single
# attribute drifting apart would produce a sign-in that appears to work and then
# silently does not stick. One implementation, one behaviour.
from server.auth_routes import _set_refresh_cookie

# The callback path. `server/auth.py` carries the same literal in
# `_PUBLIC_SSO_PATHS` — it cannot import this module (this one imports
# `server/auth_routes.py`, which imports `server/auth.py`), so the two are kept in
# step by the cross-reference comment at each end rather than by a shared symbol.
CALLBACK_PATH = "/sso/hub/callback"

router = APIRouter(tags=["sso"])


def _client_ip(request: Request) -> str:
    """The caller's address, preferring the proxy header the deployment sets.

    Mirrors `server/auth_routes.py::_client_ip` so the audit rows an SSO login
    writes are comparable with the ones a password login writes.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


async def _read_token(request: Request) -> str:
    """The ticket, POST body first and query string second.

    The contract prefers the body because a token in a query string is written to
    the browser's history, the proxy's access log and any `Referer` the next page
    sends. The query form stays supported because the Hub may use it and because
    it is the only option for a plain link.
    """
    if request.method == "POST":
        try:
            form = await request.form()
            token = (form.get("token") or "").strip()
            if token:
                return token
        except Exception:
            # No parseable form body — fall through to JSON, then the query.
            pass
        try:
            body = await request.json()
            if isinstance(body, dict):
                token = str(body.get("token") or "").strip()
                if token:
                    return token
        except Exception:
            pass

    return (request.query_params.get("token") or "").strip()


def _to_login(request: Request, reason: str, *, detail: str = "") -> RedirectResponse:
    """Give up, and send the browser to the ordinary login page.

    `reason` is logged, never shown. The visitor gets `?sso=failed` and nothing
    else: an unauthenticated caller who learns that the audience was wrong, or
    that a jti had already been spent, has been told which guess was close.
    """
    print(f"[sso] ticket refused ({reason})"
          f"{': ' + detail if detail else ''} "
          f"from {_client_ip(request) or 'unknown'}", flush=True)
    identity_store.audit(
        "user.login.sso", outcome="denied", target_type="sso",
        target_id=reason, ip=_client_ip(request),
        user_agent=_user_agent(request), detail={"reason": reason})
    return RedirectResponse(url=f"{sso_config.login_path()}?sso=failed",
                            status_code=303)


@router.api_route(CALLBACK_PATH, methods=["GET", "POST"], include_in_schema=False)
async def hub_callback(request: Request):
    """Verify a Hub ticket and start a normal authenticated session.

    303 See Other on every path out. For the POST case that matters: 303 tells
    the browser to follow with a GET, so the landing page is not re-POSTed and a
    refresh cannot resubmit a spent ticket.
    """
    token = await _read_token(request)

    # -- Verify -----------------------------------------------------------
    try:
        ticket = sso_verifier.verify(token)
    except sso_verifier.TicketError as e:
        return _to_login(request, e.reason, detail=e.detail)
    except Exception as e:                                # never a 500
        return _to_login(request, "verify_error", detail=f"{type(e).__name__}: {e}")

    # -- Resolve to an EXISTING local user --------------------------------
    try:
        resolution = sso_resolver.resolve(ticket)
    except sso_resolver.ResolutionError as e:
        return _to_login(request, e.reason, detail=e.detail)
    except Exception as e:
        return _to_login(request, "resolve_error", detail=f"{type(e).__name__}: {e}")

    # -- Start the session ------------------------------------------------
    # `_issue_session` is what `identity.service.login()` calls once a password
    # has been proven. Calling it directly is the whole point: the session row,
    # the refresh token, the access token and the audit entry are produced by the
    # same code path, so nothing downstream can distinguish this from a password
    # login.
    #
    # `org_id` is deliberately NOT taken from the ticket. The Hub's `org_id` is a
    # HUB identifier and this app's orgs have their own ids; passing it through
    # would ask `_resolve_acting_org` for a membership that cannot exist and
    # raise Forbidden on every SSO login. Leaving it empty selects the user's
    # highest-privileged membership — exactly what a password login with no org
    # does. Mapping Hub orgs onto local ones is a real feature, and a separate
    # one from authentication.
    try:
        from identity import service as identity_service
        result = identity_service._issue_session(
            resolution.user,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
            action="user.login.sso",
        )
    except Exception as e:
        return _to_login(request, "session_error", detail=f"{type(e).__name__}: {e}")

    # -- Land them somewhere sane -----------------------------------------
    destination = sso_redirect.resolve(ticket.rt, sso_config.default_redirect())

    response = RedirectResponse(url=destination, status_code=303)
    _set_refresh_cookie(response, result["refresh_token"])

    # The access token is NOT put in the URL, and does not need to be. The SPA's
    # `session.js::bootstrap()` runs on load, calls `/api/v1/auth/refresh`, and
    # exchanges the cookie we just set for one — so the browser lands already
    # signed in with no token ever appearing in history, logs or a Referer
    # header. That is why this integration needs no frontend change at all.
    print(f"[sso] {resolution.user.email} signed in via Hub "
          f"(matched by {resolution.matched_by}"
          f"{', link created' if resolution.linked else ''}) -> {destination}",
          flush=True)
    return response


@router.get("/api/v1/sso/status", tags=["sso"])
def sso_status():
    """Whether Hub SSO is configured, for an operator checking a deployment.

    Reports posture, never secrets — and never whether a given ticket would work,
    which would be an oracle. Behind the normal API auth because it describes the
    deployment's configuration.
    """
    return {
        "enabled": sso.enabled(),
        "issuer": sso_config.issuer(),
        "audience": sso_config.audience(),
        "callback": CALLBACK_PATH,
        "secret_configured": bool(sso_config.hub_secret()),
        "secret_collides_with_jwt": sso_config.secret_collides(),
    }
