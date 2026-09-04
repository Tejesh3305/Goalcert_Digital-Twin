"""redirect.py — where the browser is allowed to land.

The `rt` claim is a relative path chosen by the Hub. It is signed, so it is not
attacker-controlled in the usual sense — but it is still the one claim that turns
into a `Location:` header, and a signed value is not automatically a SAFE one. A
Hub-side bug, a mis-encoded deep link, or an operator pasting a full URL into a
config field are all ordinary ways an absolute target ends up in a ticket, and
any of them would turn this endpoint into an open redirect that launders its
credibility through our domain: the victim sees a real goal-cert.com link, clicks
it, and lands somewhere else already signed in.

So the rule is allow-list, not deny-list. A target must be a plain, rooted,
same-origin path or it is discarded for the default landing page. There is no
sanitising step that tries to repair a bad value — a target we do not fully
understand is replaced, never edited, because every classic open-redirect bypass
is a payload that survived somebody's cleanup.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Characters that must never reach a Location header. A raw CR or LF splits the
# response and lets the rest of the value be read as headers; NUL and the other
# C0 controls are stripped or normalised inconsistently between proxies, which is
# how two hops end up disagreeing about where the user is going.
_FORBIDDEN = set("\r\n\t\x00\x0b\x0c")


def is_safe(target: str) -> bool:
    """Whether `target` is a relative path that stays inside this app."""
    if not target or not isinstance(target, str):
        return False

    if any(ch in _FORBIDDEN for ch in target):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        return False

    # Backslashes are rejected outright rather than normalised. Browsers treat
    # "/\evil.com" and "\\evil.com" as protocol-relative — the backslash behaves
    # like a slash in a URL context — so a value containing one means something
    # different to the browser than it does to `urlsplit`, and disagreement
    # between our parser and the browser's is exactly the bypass.
    if "\\" in target:
        return False

    # Must be rooted. A bare "dashboard" is resolved against the callback's own
    # path, which is not what any caller means.
    if not target.startswith("/"):
        return False

    # "//host" is protocol-relative and goes off-site.
    if target.startswith("//"):
        return False

    parts = urlsplit(target)

    # Any scheme or authority means it is not a relative path, whatever it looks
    # like. This also catches "javascript:" and "data:" targets.
    if parts.scheme or parts.netloc:
        return False

    # The callback itself is not a landing page. Redirecting to it would replay a
    # spent ticket, fail, and bounce the user to the login screen — a confusing
    # loop for what is usually just a misconfigured default.
    if parts.path.rstrip("/").endswith("/sso/hub/callback"):
        return False

    return True


def resolve(target: str, fallback: str) -> str:
    """`target` when it is safe, otherwise `fallback`.

    The fallback is itself checked, so a deployment that sets
    NXR_SSO_DEFAULT_REDIRECT to something absolute gets "/" rather than an open
    redirect configured by accident.
    """
    if is_safe(target):
        return target
    if is_safe(fallback):
        return fallback
    return "/"
