"""store.py — the two tables Hub SSO owns.

    hub_sso_jti      spent ticket ids, so a ticket works exactly once
    hub_identities   Hub `sub` -> local `user_id`, the permanent link

Both live in the `identity` store because that is where accounts live and a link
row is meaningless without the user it points at. They are NOT in
`identity/store.py`: nothing in `identity/` reads them, they arrive and leave
with this feature, and keeping them here means the whole SSO surface can be
reviewed — or removed — in one directory. The SQL idiom is the identity store's,
deliberately, so the two stay legible side by side.

WHY REPLAY PROTECTION IS A TABLE AND NOT A CACHE
------------------------------------------------
An in-process dict would be per-worker: with two Uvicorn workers, or two ECS
tasks behind a load balancer, a replayed ticket that lands on a different worker
than the original finds an empty cache and is accepted. Redis would work, but the
bus is optional in this deployment (`bus.py` degrades to an in-memory shim) and
"replay protection that silently stops protecting when Redis is down" is worse
than none, because nobody notices. The database is already required for the user
lookup that happens moments later, so this costs one INSERT on a path that runs
once per sign-in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from db import connect, schema

_STORE = "identity"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _future(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _row(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _ensure() -> None:
    schema.ensure(_STORE)


# -- Replay ---------------------------------------------------------------


def remember_jti(jti: str, *, ttl_seconds: int) -> bool:
    """Record a ticket id. True if it is NEW, False if it was already spent.

    Atomic by construction: the decision is the INSERT itself, via a primary-key
    conflict, not a SELECT followed by an INSERT. Two simultaneous deliveries of
    the same ticket — a double-submitted form, a proxy retry, an attacker
    racing the real browser — therefore produce exactly one True. A
    check-then-write would let both pass, which is the entire failure this
    function exists to prevent.

    `ON CONFLICT DO NOTHING` becomes `INSERT IGNORE` on MySQL (db/core.py),
    keeping first-writer-wins on both backends.
    """
    if not jti:
        return False
    _ensure()
    purge_expired()
    with connect(_STORE) as conn:
        cursor = conn.execute(
            "INSERT INTO hub_sso_jti (jti, seen_at, expires_at) "
            "VALUES (?, ?, ?) ON CONFLICT (jti) DO NOTHING",
            (jti, _now(), _future(ttl_seconds)))
        # rowcount is 1 when the row was inserted and 0 when the conflict
        # swallowed it. `getattr` because the DB-API does not guarantee the
        # attribute and this must never raise on the sign-in path.
        return bool(getattr(cursor, "rowcount", 0))


def purge_expired() -> int:
    """Drop jti rows past their TTL.

    Called from `remember_jti`, so the table stays bounded with no scheduled job.
    Affordable precisely because it runs on sign-in rather than per request: one
    indexed DELETE against a table holding at most a few minutes of logins.
    """
    _ensure()
    with connect(_STORE) as conn:
        cursor = conn.execute(
            "DELETE FROM hub_sso_jti WHERE expires_at < ?", (_now(),))
        return int(getattr(cursor, "rowcount", 0) or 0)


# -- The identity link ----------------------------------------------------


def user_id_for_hub_sub(hub_sub: str) -> str | None:
    """The local user this Hub subject is linked to, if any."""
    if not hub_sub:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT user_id FROM hub_identities WHERE hub_sub = ?", (hub_sub,)))
    return row["user_id"] if row else None


def hub_sub_for_user(user_id: str) -> str | None:
    """The Hub subject linked to a local user, if any. Used to refuse a SECOND
    Hub account trying to claim a user that is already linked."""
    if not user_id:
        return None
    _ensure()
    with connect(_STORE) as conn:
        row = _row(conn.execute(
            "SELECT hub_sub FROM hub_identities WHERE user_id = ?", (user_id,)))
    return row["hub_sub"] if row else None


def link(hub_sub: str, user_id: str, *, issuer: str = "",
         linked_by: str = "email_match") -> bool:
    """Bind a Hub subject to a local user. True if the link was created.

    First-writer-wins again: if a link for this `hub_sub` already exists the
    INSERT is ignored and this returns False, so a race between two tickets
    cannot repoint an existing link. Rebinding is deliberately not possible here
    — moving a link is an administrative act with an audit trail, not something a
    login should do silently.
    """
    if not hub_sub or not user_id:
        return False
    _ensure()
    with connect(_STORE) as conn:
        cursor = conn.execute(
            "INSERT INTO hub_identities (hub_sub, user_id, hub_iss, linked_at, "
            "last_seen_at, linked_by) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (hub_sub) DO NOTHING",
            (hub_sub, user_id, issuer, _now(), _now(), linked_by))
        return bool(getattr(cursor, "rowcount", 0))


def touch(hub_sub: str) -> None:
    """Record that this link was just used. Best-effort: a failed bookkeeping
    write must not fail the sign-in it is describing — the same trade
    `identity.store.audit()` makes."""
    if not hub_sub:
        return
    try:
        _ensure()
        with connect(_STORE) as conn:
            conn.execute(
                "UPDATE hub_identities SET last_seen_at = ? WHERE hub_sub = ?",
                (_now(), hub_sub))
    except Exception:
        pass
