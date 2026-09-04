"""config.py — the Hub SSO settings, and the one secret that must never be shared.

Mirrors `config/sso.php` in the GoalCert LMS reference implementation. Everything
here is environment-driven so a deployment can be re-pointed without a rebuild,
and every value has a safe default EXCEPT the secret, which has none: an
unconfigured deployment must refuse tickets rather than invent a key and accept
nothing (or, worse, accept everything a misconfiguration hands it).

THE TWO SECRETS ARE NOT THE SAME SECRET
---------------------------------------
`NXR_JWT_SECRET` signs THIS app's own access tokens (identity/tokens.py).
`NXR_SSO_HUB_SECRET` verifies tickets minted by the Hub, and is the same value as
the Hub's `SSO_SECRET`.

They must never be set to the same string, and `posture()` refuses to accept
tickets when they are. The reason is asymmetric and worth stating plainly: the
Hub's ticket secret is shared with every satellite app, so if this app's own
token secret equalled it, ANY satellite app holding that shared value could mint
an access token for THIS app — turning a shared verification key into a shared
forgery key. The check is cheap and the failure it prevents is total.
"""

from __future__ import annotations

import os

# The Hub's identity. Compared with `==`, never a prefix or substring test:
# "https://hub.goal-cert.com.evil.example" starts with the real issuer.
DEFAULT_ISSUER = "https://hub.goal-cert.com"

# This app's slot in the Hub's audience table. A ticket minted for the Scenario
# Engine or AUTOMIND Hive carries a different value and must be refused here even
# though it is signed with the same shared secret — the audience claim is the
# ONLY thing separating three apps that trust one key.
DEFAULT_AUDIENCE = "goalcert-twin"

# Tickets live ~60s. The leeway absorbs clock skew between the Hub and this app;
# it widens the replay window by the same amount, so it stays small.
DEFAULT_LEEWAY_SECONDS = 30

# How long a spent `jti` is remembered. MUST exceed the ticket lifetime plus the
# leeway, or a ticket could outlive the record that says it was already used —
# which is precisely the replay this table exists to stop.
DEFAULT_JTI_TTL_SECONDS = 600

# Where a user lands when the ticket is good but carries no usable `rt`.
DEFAULT_REDIRECT = "/"

# Where a user lands when anything at all goes wrong. The contract says: never a
# 500, always the normal login page.
LOGIN_PATH = "/login"


def _clean(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _int(name: str, default: int) -> int:
    raw = _clean(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def hub_secret() -> str:
    """The shared HS256 key. Empty means SSO is not configured."""
    return _clean("NXR_SSO_HUB_SECRET")


def issuer() -> str:
    return _clean("NXR_SSO_HUB_ISSUER", DEFAULT_ISSUER)


def audience() -> str:
    return _clean("NXR_SSO_AUDIENCE", DEFAULT_AUDIENCE)


def leeway() -> int:
    return _int("NXR_SSO_LEEWAY", DEFAULT_LEEWAY_SECONDS)


def jti_ttl() -> int:
    """Kept at or above the ticket lifetime by construction — a deployment that
    lowers it below the leeway would open the replay window it is closing."""
    return max(_int("NXR_SSO_JTI_TTL", DEFAULT_JTI_TTL_SECONDS), leeway() * 2)


def default_redirect() -> str:
    return _clean("NXR_SSO_DEFAULT_REDIRECT", DEFAULT_REDIRECT)


def login_path() -> str:
    return _clean("NXR_SSO_LOGIN_PATH", LOGIN_PATH)


def secret_collides() -> bool:
    """Whether the Hub ticket secret has been set to this app's OWN token secret.

    See the module docstring: this is a forgery hazard, not a style problem.
    """
    secret = hub_secret()
    if not secret:
        return False
    try:
        from identity.tokens import has_configured_secret, signing_secret
        # `signing_secret()` invents a per-process value in local dev; comparing
        # against that would be meaningless, so only a CONFIGURED secret counts.
        if not has_configured_secret():
            return False
        return secret == signing_secret()
    except Exception:
        return False


def enabled() -> bool:
    """Whether the callback should accept tickets at all.

    Fail-closed, and for the same reason `server/auth.py` inverted its default:
    an unconfigured or dangerously-configured SSO surface must refuse, not
    improvise.
    """
    if not hub_secret():
        return False
    if secret_collides():
        return False
    return True


def posture() -> list[str]:
    """The SSO configuration, as lines for the boot log — the same shape as
    `server/auth.py::posture()`, so an operator reading CloudWatch after a rollout
    sees auth and SSO on adjacent lines."""
    lines: list[str] = []
    if not hub_secret():
        lines.append(
            "[sso] Hub SSO is OFF (NXR_SSO_HUB_SECRET is unset). "
            "/sso/hub/callback will send every visitor to the login page.")
        return lines
    if secret_collides():
        lines.append(
            "[sso] !! REFUSING TICKETS: NXR_SSO_HUB_SECRET is identical to "
            "NXR_JWT_SECRET. The Hub's ticket key is shared with every satellite "
            "app, so this would let any of them forge an access token for this "
            "one. Set NXR_SSO_HUB_SECRET to the Hub's SSO_SECRET, which is a "
            "DIFFERENT value from this app's own signing key.")
        return lines
    lines.append(f"[sso] Hub SSO ON - issuer {issuer()}, audience "
                 f"{audience()}, leeway {leeway()}s, jti memory {jti_ttl()}s")
    if len(hub_secret()) < 32:
        lines.append("[sso] !! NXR_SSO_HUB_SECRET is shorter than 32 characters, "
                     "which is below the floor for HS256 to be worth its name.")
    return lines


def log_posture() -> None:
    """Print the SSO posture at boot.

    Named `log_posture` to match `identity`, `db`, `storage`, `bus` and
    `historian` — `AuthMiddleware.__init__` walks that tuple by name, so an
    operator reading CloudWatch after a rollout finds every subsystem's posture
    on adjacent lines rather than hunting for this one.
    """
    for line in posture():
        print(line, flush=True)
