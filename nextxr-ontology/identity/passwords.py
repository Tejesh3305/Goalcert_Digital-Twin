"""passwords.py — how a password becomes a stored string, and back.

WHY THIS SHAPE
--------------
Every hash this module writes is SELF-DESCRIBING::

    argon2id$<argon2-encoded>
    scrypt$<n>$<r>$<p>$<salt-b64>$<hash-b64>
    pbkdf2_sha256$<iterations>$<salt-b64>$<hash-b64>

The algorithm and its parameters travel WITH the hash rather than living in a
config value read at verification time. That is what makes the parameters
upgradable: raising the cost, or moving the fleet from scrypt to Argon2id, must
not invalidate a single existing password. `verify()` reads whatever algorithm
the stored string names, and `needs_rehash()` reports when that is weaker than
today's policy so `service.login()` can transparently re-hash on the next
successful sign-in. A deployment therefore migrates itself as users log in, with
no flag day and no forced reset.

ALGORITHM CHOICE
----------------
Argon2id is the preference (OWASP's first recommendation, and the winner of the
Password Hashing Competition) and is used whenever `argon2-cffi` is importable.
It is listed in requirements.txt, so a real deploy has it.

The fallback is `hashlib.scrypt` — memory-hard, in the standard library, and
therefore ALWAYS available. That matters more than it looks: the alternative
design, "require argon2-cffi or refuse to start", turns a missing wheel on some
Python build into a service that cannot boot, and the temptation then is to fall
back to plain PBKDF2 or, worse, to skip hashing in a dev path. A strong stdlib
default means there is never a reason to reach for a weak one.

PBKDF2 is read-only here: it is never written, only verified, so hashes created
by an older build still open and are upgraded on next login.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not decide policy about password STRENGTH — that is `validate_strength()`
below and is enforced at the route, where a bad password can be reported to the
user. And it does not log, ever: this module sees plaintext, so it must be the
one place in the codebase that is provably silent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

# Argon2id parameters. 64 MiB / 3 passes / 4 lanes is the OWASP "second choice"
# configuration and is comfortably above the minimum; it costs ~50-80 ms on the
# Fargate task sizes this service runs on, which is the right order of magnitude
# for a login (slow enough to matter to an attacker, fast enough not to be a DoS
# lever against ourselves — see the rate limiter, which is the other half of that
# trade).
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_KIB = 64 * 1024
_ARGON2_PARALLELISM = 4

# scrypt: N=2^15, r=8, p=1 — ~32 MiB, the parameters RFC 7914 suggests for
# interactive logins.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024      # headroom over N*r*128 so scrypt() won't refuse

_PBKDF2_ITERATIONS = 600_000           # OWASP 2023 figure for PBKDF2-HMAC-SHA256

_SALT_BYTES = 16
_HASH_BYTES = 32

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024             # bound the work an unauthenticated caller can buy


class PasswordError(ValueError):
    """A password that cannot be accepted, with a message safe to show a user."""


def _argon2_hasher():
    """The argon2-cffi hasher, or None when the library isn't installed."""
    try:
        from argon2 import PasswordHasher
        from argon2.profiles import RFC_9106_LOW_MEMORY  # noqa: F401  (import check)
    except Exception:
        return None
    return PasswordHasher(
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_KIB,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_HASH_BYTES,
        salt_len=_SALT_BYTES,
    )


def preferred_algorithm() -> str:
    return "argon2id" if _argon2_hasher() is not None else "scrypt"


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ── Hashing ─────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password with the strongest algorithm available.

    Raises PasswordError for input that must never reach a KDF — an empty
    password, or one long enough to be a denial-of-service vector rather than a
    credential.
    """
    _check_shape(password)

    hasher = _argon2_hasher()
    if hasher is not None:
        return "argon2id$" + hasher.hash(password)

    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=_HASH_BYTES,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Check a plaintext password against a stored hash of ANY supported scheme.

    Never raises for a bad password, a malformed hash or an unknown algorithm —
    all of those are simply False. A verification path that can raise is a
    verification path that distinguishes "wrong password" from "corrupt record"
    to whoever is probing it, and it turns a bad row into a 500 instead of a 401.
    """
    if not password or not stored:
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        return False

    try:
        algorithm, _, rest = stored.partition("$")
        if algorithm == "argon2id":
            hasher = _argon2_hasher()
            if hasher is None:
                # An Argon2 hash on a build without the library. Failing closed is
                # correct and must be LOUD in its cause, but this function cannot
                # log — the caller sees False, and `verify_supported()` is what a
                # startup check uses to catch this configuration before users do.
                return False
            try:
                hasher.verify(rest, password)
                return True
            except Exception:
                return False
        if algorithm == "scrypt":
            n, r, p, salt_b64, hash_b64 = rest.split("$")
            expected = _b64d(hash_b64)
            actual = hashlib.scrypt(
                password.encode("utf-8"), salt=_b64d(salt_b64),
                n=int(n), r=int(r), p=int(p),
                maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
            )
            return hmac.compare_digest(actual, expected)
        if algorithm == "pbkdf2_sha256":
            iterations, salt_b64, hash_b64 = rest.split("$")
            expected = _b64d(hash_b64)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), _b64d(salt_b64),
                int(iterations), dklen=len(expected),
            )
            return hmac.compare_digest(actual, expected)
    except Exception:
        return False
    return False


def needs_rehash(stored: str) -> bool:
    """True when `stored` was made with a weaker scheme than today's policy.

    Called after a SUCCESSFUL verification, which is the only moment the
    plaintext is available to re-hash with.
    """
    if not stored:
        return True
    algorithm, _, rest = stored.partition("$")
    preferred = preferred_algorithm()

    if algorithm != preferred:
        # pbkdf2 -> scrypt -> argon2id are all upgrades. The reverse (an argon2
        # hash on a build that has lost the library) is NOT: rehashing to scrypt
        # would silently downgrade every password as the fleet rolled. Only
        # upgrade.
        order = {"pbkdf2_sha256": 0, "scrypt": 1, "argon2id": 2}
        return order.get(algorithm, -1) < order.get(preferred, 0)

    if algorithm == "argon2id":
        hasher = _argon2_hasher()
        try:
            return bool(hasher and hasher.check_needs_rehash(rest))
        except Exception:
            return False
    if algorithm == "scrypt":
        try:
            n, r, p, _, _ = rest.split("$")
            return (int(n), int(r), int(p)) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
        except Exception:
            return True
    return False


def verify_supported(stored: str) -> bool:
    """Whether this process can verify `stored` at all — used by the startup
    self-check so "every login fails" is caught at boot, not in production."""
    algorithm = (stored or "").partition("$")[0]
    if algorithm == "argon2id":
        return _argon2_hasher() is not None
    return algorithm in ("scrypt", "pbkdf2_sha256")


# ── Strength policy ─────────────────────────────────────────────────────

# Passwords that are catastrophically common. This is NOT a serious breach-corpus
# check — that belongs behind a k-anonymity range query against Have I Been Pwned
# and is a network call this module must not make on the login path. It is the
# cheap 90% : it rejects the handful of passwords that appear in every credential
# -stuffing list, at zero cost and with no external dependency.
_BANNED = {
    "password", "password1", "password123", "passw0rd", "qwerty123456",
    "123456789012", "administrator", "letmein12345", "welcome12345",
    "iloveyou1234", "changeme1234", "secret123456", "trustno112345",
}


def _check_shape(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise PasswordError("Password must not be empty.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")


def validate_strength(password: str, *, email: str = "", name: str = "") -> None:
    """Raise PasswordError if `password` is too weak to accept.

    Length first and hardest: it is the only factor that reliably predicts
    resistance to offline cracking, which is the threat a stored hash faces. The
    composition rules deliberately stop at "not a single repeated character" and
    "not derived from your own email" rather than demanding a symbol and a digit
    — those push users toward `Password1!`, which is worse than a long
    passphrase, and NIST SP 800-63B advises against them explicitly.
    """
    _check_shape(password)

    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    lowered = password.lower()
    if lowered in _BANNED:
        raise PasswordError("That password is too common. Choose another.")

    if len(set(password)) < 5:
        raise PasswordError("Password is too repetitive. Choose another.")

    # A password containing the local part of your own email survives no credential
    # -stuffing list, because the attacker already has the email.
    local = (email or "").split("@")[0].strip().lower()
    if local and len(local) >= 4 and local in lowered:
        raise PasswordError("Password must not contain your email address.")

    for word in (name or "").lower().split():
        if len(word) >= 4 and word in lowered:
            raise PasswordError("Password must not contain your name.")


# ── Opaque secrets (API keys, refresh tokens, reset links) ──────────────


def generate_secret(prefix: str = "", *, nbytes: int = 32) -> str:
    """A URL-safe random secret with an optional human-readable prefix.

    The prefix is what makes a leaked key identifiable in a log or a public repo
    (`nxr_live_...`), which is how secret-scanning services find them — the same
    reason Stripe and GitHub prefix theirs. 32 bytes is 256 bits of entropy;
    guessing is not a threat model, so the length is set by what is comfortable
    to copy rather than by any margin argument.
    """
    body = secrets.token_urlsafe(nbytes)
    return f"{prefix}{body}" if prefix else body


def hash_secret(secret: str) -> str:
    """Hash an API key / refresh token / reset token for storage.

    SHA-256 with a server-side pepper, NOT a password KDF. That is deliberate and
    the reasoning is the opposite of `hash_password`: these secrets are 256-bit
    RANDOM values, so there is no dictionary to run and no offline-cracking risk
    for a KDF to slow down. What matters instead is that verification is a single
    indexed lookup — `WHERE key_hash = ?` — because the presented token is all we
    have and there is no id on the wire to narrow it first. An Argon2 hash cannot
    be looked up that way; it would force a scan-and-verify over every key in the
    table on every request.

    The pepper (NXR_SECRET_PEPPER) means a stolen database dump alone does not
    let an attacker match hashes against tokens found elsewhere. It is optional
    so local dev needs no configuration, and `identity.posture()` reports its
    absence at boot.
    """
    pepper = os.environ.get("NXR_SECRET_PEPPER", "")
    return hashlib.sha256(f"{pepper}{secret}".encode()).hexdigest()


def secret_prefix(secret: str, length: int = 12) -> str:
    """The displayable leading fragment of a secret, for "which key is this?"
    listings. Short enough to be useless on its own."""
    return (secret or "")[:length]


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))
