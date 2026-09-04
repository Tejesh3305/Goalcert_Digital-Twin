"""verifier.py — is this ticket real, and is it for us?

The Python counterpart of `app/Sso/Services/HubTokenVerifier.php`.

A ticket is a short-lived (~60s), single-use, audience-bound HS256 JWT minted by
the Goalcert Hub. This module answers one question — is it valid FOR THIS APP —
and answers it in a fixed order. It resolves no users and starts no sessions;
that separation is deliberate for the same reason `server/auth.py` keeps
authentication apart from `server/tenancy.py`. A verifier that also looked up
accounts would be tempted to let a known email excuse a bad signature.

THE ORDER IS THE CONTRACT
-------------------------
    1. signature          — HS256, against the shared secret, algorithm PINNED
    2. exp / nbf          — with a small leeway for clock skew
    3. iss                — exact string equality with the configured Hub
    4. aud                — our audience present in the claim
    5. jti                — not seen before (persisted; see store.py)
    6. sub                — present, and the permanent link key

Cheapest and most fundamental first. Nothing downstream of a failed signature
means anything, and checking `aud` before the signature would let an unsigned
ticket tell us which app it was aiming at.

WHY THE ALGORITHM IS PINNED
---------------------------
`algorithms=["HS256"]` is not defensive decoration. Without it, a forged header
of `{"alg":"none"}` verifies trivially, and `{"alg":"RS256"}` invites the classic
confusion where a public key is accepted as an HMAC secret. The Hub mints HS256
and only HS256, so anything else is an attack, not a compatibility case.

WHY FAILURES ARE COARSE OUTSIDE AND SPECIFIC INSIDE
---------------------------------------------------
`TicketError.reason` is a short machine code, logged in full. What the BROWSER
gets is only that SSO failed. Telling an unauthenticated caller "the audience was
wrong" or "that jti was already used" confirms which guess was close, and the
person who needs the detail is the operator reading the log, not the visitor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

from . import config, store

ALGORITHM = "HS256"

# Claims a ticket cannot be missing. PyJWT enforces presence before it enforces
# meaning, so a ticket without `exp` is rejected outright rather than treated as
# one that never expires — which is how a 60-second credential quietly becomes a
# permanent one.
REQUIRED_CLAIMS = ("iss", "aud", "sub", "jti", "iat", "nbf", "exp")


class TicketError(Exception):
    """A ticket that must not start a session. `reason` is for the log."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Ticket:
    """A verified ticket. Constructing one is a claim that all six checks passed."""

    sub: str
    email: str
    username: str
    name: str
    role: str
    org_id: str
    rt: str
    jti: str
    issuer: str
    claims: dict[str, Any]


def _as_audience_list(raw: Any) -> list[str]:
    """`aud` is a string or a list of strings (RFC 7519 4.1.3). Both shapes are
    legal and the Hub may use either, so normalise rather than assume."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [a for a in raw if isinstance(a, str)]
    return []


def _text(claims: dict, key: str) -> str:
    """A claim as a trimmed string. Non-strings become empty rather than raising:
    a numeric `org_id` is a Hub-side shape change, not a security event, and it
    must not take the whole login down."""
    value = claims.get(key)
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def verify(token: str) -> Ticket:
    """Verify a Hub ticket, or raise `TicketError`.

    Consumes the `jti` as a side effect: a ticket that reaches the end of this
    function is spent, whether or not the caller goes on to find a local user for
    it. That is the correct trade — a ticket burned by a failed identity lookup
    costs one extra click through the Hub, while a ticket left unburned is a
    replayable credential sitting in a browser history and a proxy log.
    """
    if not token:
        raise TicketError("missing_token")

    if not config.enabled():
        # Distinguishes "SSO is off" from "your ticket was bad" in the log,
        # because they need completely different fixes.
        raise TicketError(
            "sso_disabled",
            "no NXR_SSO_HUB_SECRET, or it collides with NXR_JWT_SECRET")

    secret = config.hub_secret()

    # -- 1 + 2. Signature, then exp/nbf ---------------------------------
    # `iss` and `aud` verification are turned OFF here and done by hand below.
    # PyJWT would check them inside this same call, but it reports them through
    # the same exception family in an order we do not control — and the contract
    # specifies the order. Doing them explicitly also lets the log say WHICH of
    # the two failed, which is the difference between "the Hub was reconfigured"
    # and "a ticket for another app arrived here".
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            leeway=config.leeway(),
            options={
                "require": list(REQUIRED_CLAIMS),
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": False,   # presence required; skew is the leeway's job
                "verify_aud": False,   # checked below, in contract order
                "verify_iss": False,   # checked below, in contract order
            },
        )
    except jwt.ExpiredSignatureError:
        raise TicketError("expired") from None
    except jwt.ImmatureSignatureError:
        raise TicketError("not_yet_valid") from None
    except jwt.MissingRequiredClaimError as e:
        raise TicketError("missing_claim", str(e)) from None
    except jwt.InvalidSignatureError:
        raise TicketError("bad_signature") from None
    except jwt.InvalidAlgorithmError:
        raise TicketError("bad_algorithm") from None
    except jwt.DecodeError as e:
        raise TicketError("malformed", str(e)) from None
    except jwt.InvalidTokenError as e:
        raise TicketError("invalid", str(e)) from None

    if not isinstance(claims, dict):
        raise TicketError("malformed", "payload is not an object")

    # -- 3. Issuer, exactly ---------------------------------------------
    expected_issuer = config.issuer()
    got_issuer = _text(claims, "iss")
    if got_issuer != expected_issuer:
        raise TicketError("bad_issuer", repr(got_issuer))

    # -- 4. Audience — ours must be in it -------------------------------
    ours = config.audience()
    if ours not in _as_audience_list(claims.get("aud")):
        raise TicketError(
            "bad_audience",
            f"expected {ours!r}, got {claims.get('aud')!r}")

    # -- 5. Replay -------------------------------------------------------
    # Last of the cryptographic checks and first of the stateful ones, so a
    # garbage ticket never reaches the database. `remember_jti()` is atomic: it
    # returns False if this jti was already recorded, so two simultaneous
    # deliveries of the same ticket cannot both win.
    jti = _text(claims, "jti")
    if not jti:
        raise TicketError("missing_claim", "jti")
    if not store.remember_jti(jti, ttl_seconds=config.jti_ttl()):
        raise TicketError("replayed", jti)

    # -- 6. Subject ------------------------------------------------------
    # The permanent link key. Checked last because it is the cheapest to satisfy
    # and the one whose absence is a Hub bug rather than an attack.
    sub = _text(claims, "sub")
    if not sub:
        raise TicketError("missing_claim", "sub")

    return Ticket(
        sub=sub,
        email=_text(claims, "email"),
        username=_text(claims, "username"),
        name=_text(claims, "name"),
        role=_text(claims, "role"),
        org_id=_text(claims, "org_id"),
        rt=_text(claims, "rt"),
        jti=jti,
        issuer=expected_issuer,
        claims=claims,
    )
