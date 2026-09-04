"""resolver.py — a verified ticket becomes a LOCAL user, or nothing at all.

The Python counterpart of `app/Sso/Services/AdminIdentityResolver.php`.

THE HUB VOUCHES FOR IDENTITY. IT DOES NOT GRANT ACCESS.
-------------------------------------------------------
A valid ticket proves who someone is at the Hub. It does not create an account
here, and it does not decide what they may touch. This app keeps its own user
table, its own org memberships and its own roles, and `server/tenancy.py` remains
the only thing that answers "may they?". So this module can do exactly one of two
things: hand back an existing local user, or refuse.

Auto-provisioning is deliberately absent. If it existed, anyone the Hub could
mint a ticket for would have an account here the moment they clicked a link, and
the `role` claim would quietly become this app's authorization source. Adding it
later is a one-function change and an explicit product decision; leaving it out
is the safe default while nobody has made that decision.

MATCHING, IN ORDER
------------------
    1. the stored link  hub_identities.hub_sub -> user_id     (every visit after
                                                               the first)
    2. email, exactly one match                                (first visit only)

`sub` is the permanent key and email is only ever the BOOTSTRAP. Emails get
reassigned — someone leaves, the address is handed to their replacement — and a
system that re-matched on email every time would hand the newcomer the leaver's
account. Once the link exists, a changed email on the Hub side is followed
silently and correctly, because the link never depended on it.

REFUSING ON AMBIGUITY
---------------------
Every refusal below returns None rather than guessing. The asymmetry is the
point: a wrong refusal costs someone a login and a support ticket, while a wrong
match hands one person another person's twin.
"""

from __future__ import annotations

from dataclasses import dataclass

from identity import store as identity_store

from . import store


@dataclass(frozen=True)
class Resolution:
    """Who the ticket resolved to, and how."""

    user: object                  # identity.models.User
    linked: bool                  # True if this call created the link
    matched_by: str               # "hub_sub" | "email"


class ResolutionError(Exception):
    """No local user may be signed in for this ticket. `reason` is for the log."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _usable(user) -> None:
    """Account-state checks that a password login would have applied.

    `identity.service.login()` runs these BEFORE it calls `_issue_session()`, and
    SSO calls `_issue_session()` directly — so without this, single sign-on would
    be a way around a disabled account, which is the one control an administrator
    reaches for first when someone leaves.

    Two checks a password login makes are deliberately NOT repeated:

      lockout          `locked_until` throttles password guessing. No password
                       was guessed here, so applying it would let an attacker
                       lock a colleague out of SSO by failing logins on their
                       behalf — a denial-of-service handed to anyone who knows
                       an email address.

      email_verified   the local check exists to prove the person controls the
                       address. The Hub has already proven it, and re-asking
                       would strand every SSO user behind a verification email
                       this app never sent them.
    """
    if not getattr(user, "is_active", False):
        raise ResolutionError("account_disabled", getattr(user, "user_id", ""))


def resolve(ticket) -> Resolution:
    """Map a verified `Ticket` to an existing local user, or raise.

    Never creates a user. Never rebinds an existing link.
    """
    # -- 1. The stored link. The normal path, from the second visit onward. --
    user_id = store.user_id_for_hub_sub(ticket.sub)
    if user_id:
        user = identity_store.get_user(user_id)
        if user is None:
            # The link outlived the account it pointed at. Refusing is right:
            # re-matching on email here would silently re-link a deleted user's
            # Hub identity to whoever now holds that address.
            raise ResolutionError("linked_user_missing", user_id)
        _usable(user)
        store.touch(ticket.sub)
        return Resolution(user=user, linked=False, matched_by="hub_sub")

    # -- 2. First visit: bootstrap the link from the email claim. ------------
    email = (ticket.email or "").strip()
    if not email:
        # No link and nothing to match on. A ticket for someone who has never
        # signed in here, from a Hub that did not send an email claim.
        raise ResolutionError("no_link_and_no_email", ticket.sub)

    user = identity_store.get_user_by_email(email)
    if user is None:
        # The Hub knows them; this app does not. Exactly the case where
        # auto-provisioning would be wrong by default.
        raise ResolutionError("no_local_account", identity_store.normalize_email(email))

    _usable(user)

    # Is this local user already claimed by a DIFFERENT Hub subject? Then two Hub
    # accounts share one email here, and picking either is a guess. Refuse.
    existing = store.hub_sub_for_user(user.user_id)
    if existing and existing != ticket.sub:
        raise ResolutionError("user_already_linked", f"{user.user_id} -> {existing}")

    created = store.link(ticket.sub, user.user_id, issuer=ticket.issuer,
                         linked_by="email_match")
    if not created:
        # Another request linked this same `sub` between our lookup and our
        # write. Re-read rather than assume: whoever won may have bound it to a
        # different user, and that link is now the truth.
        winner_id = store.user_id_for_hub_sub(ticket.sub)
        if winner_id and winner_id != user.user_id:
            raise ResolutionError("link_race_lost", f"{ticket.sub} -> {winner_id}")

    identity_store.audit(
        "sso.link", actor_user=user.user_id, target_type="hub_sub",
        target_id=ticket.sub, detail={"issuer": ticket.issuer, "matched_by": "email"})

    return Resolution(user=user, linked=bool(created), matched_by="email")
