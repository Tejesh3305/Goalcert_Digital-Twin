"""seed.py — provision a working supervisor + operator pair, and a team.

    python -m work.seed --org "Northgate Facilities" --tenant t_northgate

The dispatch feature needs at least three things that no amount of code can
invent: an organisation, two accounts with DIFFERENT personas, and a team that
connects them. Without the team a supervisor can assign to nobody — which is
correct behaviour and looks exactly like a broken page, so this script exists to
make the working state reachable in one command.

IDEMPOTENT. Re-running reuses accounts that already exist rather than failing on
a duplicate email, so it is safe to run against a database that has been seeded
before — including as part of a demo reset.

PASSWORDS ARE PRINTED, ONCE. They are generated rather than fixed, because a
seed script with a hardcoded password is how `demo/demo` reaches production. Pass
--password to set them yourself for a local demo.
"""
from __future__ import annotations

import argparse
import secrets
import sys

from db import schema
from identity import store as ident
from identity.models import PERSONA_FRONTLINE, PERSONA_SUPERVISOR

# `hash_password` (argon2/bcrypt), NOT `hash_secret` (sha256).
#
# They are not interchangeable and picking the wrong one fails SILENTLY in the
# worst possible way: the account is created, looks completely normal in every
# listing, and simply cannot ever log in — because `verify_password` is given a
# digest no algorithm it knows produced. `hash_secret` is for API keys and
# tokens, which are looked up by hash rather than verified against.
from identity.passwords import hash_password, validate_strength

from work import store


def _password() -> str:
    """A generated password that satisfies the identity layer's strength policy."""
    return f"Gc-{secrets.token_urlsafe(12)}-9x!"


def _account(*, org_id: str, email: str, name: str, role: str, persona: str,
             password: str) -> tuple[object, str | None]:
    """Create or reuse one account. Returns (user, password-if-new)."""
    existing = ident.get_user_by_email(email)
    if existing is not None:
        ident.add_member(org_id, existing.user_id, role, persona=persona)
        return existing, None

    # Checked BEFORE the account is written. The strength policy is enforced on
    # the signup path, so a --password that would be refused there must be
    # refused here too — otherwise the seed creates an account whose password
    # the user can never change to itself, and the inconsistency surfaces later.
    validate_strength(password, email=email, name=name)

    user = ident.create_user(email, hash_password(password), name=name,
                             email_verified=True)
    ident.add_member(org_id, user.user_id, role, persona=persona)
    return user, password


def seed(*, org_name: str, tenant: str, domain: str,
         password: str | None = None) -> dict:
    schema.ensure("identity")
    schema.ensure("work")

    # Match by NAME rather than creating unconditionally: `create_org` appends a
    # collision suffix, so re-running would quietly produce "Acme", "acme-1",
    # "acme-2" and seed each with its own pair of accounts.
    org = next((o for o in ident.list_orgs() if o.name == org_name), None)
    if org is None:
        org = ident.create_org(name=org_name)

    sup_pw = password or _password()
    op_pw = password or _password()

    supervisor, sup_new = _account(
        org_id=org.org_id, email=f"supervisor@{domain}", name="Shift Supervisor",
        # `write` rather than `admin`: a supervisor dispatches people, they do
        # not administer the organisation. The persona is what grants dispatch.
        role="write", persona=PERSONA_SUPERVISOR, password=sup_pw)
    operator, op_new = _account(
        org_id=org.org_id, email=f"operator@{domain}", name="Field Operator",
        role="write", persona=PERSONA_FRONTLINE, password=op_pw)

    teams = [t for t in store.list_teams(org.org_id) if t.name == "Shift A"]
    team = teams[0] if teams else store.create_team(
        org_id=org.org_id, name="Shift A", supervisor_id=supervisor.user_id,
        site=org_name, shift="A")
    if team.supervisor_id != supervisor.user_id:
        store.set_team_supervisor(team_id=team.team_id,
                                  supervisor_id=supervisor.user_id)
    store.add_team_member(org_id=org.org_id, team_id=team.team_id,
                          user_id=operator.user_id)

    # The tenant has to be OWNED by this org, or findings on it dispatch to
    # nobody — `from_finding` looks the org up by tenant and returns early when
    # there isn't one. This is the step most easily forgotten by hand.
    if tenant:
        ident.claim_tenant(tenant, org.org_id)

    store.ensure_default_rule(org.org_id)

    return {
        "org": org, "team": team,
        "supervisor": supervisor, "supervisor_password": sup_new,
        "operator": operator, "operator_password": op_new,
        "tenant": tenant,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", default="Goalcert Facilities",
                    help="organisation name (created if absent)")
    ap.add_argument("--tenant", default="",
                    help="twin tenant id to claim for this org, so its findings dispatch")
    ap.add_argument("--domain", default="goalcert.test",
                    help="email domain for the seeded accounts")
    ap.add_argument("--password", default=None,
                    help="set both passwords explicitly (local demos only)")
    args = ap.parse_args(argv)

    result = seed(org_name=args.org, tenant=args.tenant, domain=args.domain,
                  password=args.password)

    org, team = result["org"], result["team"]
    print(f"\norganisation : {org.name}  ({org.org_id})")
    print(f"team         : {team.name}  ({team.team_id})")
    if result["tenant"]:
        print(f"tenant       : {result['tenant']}  (claimed — its findings will dispatch)")
    else:
        print("tenant       : none claimed. Findings will NOT raise tasks until an")
        print("               organisation owns the tenant — re-run with --tenant <id>.")

    print("\n  role        email                         persona     password")
    print("  " + "-" * 74)
    for label, user, pw, persona in (
        ("supervisor", result["supervisor"], result["supervisor_password"], PERSONA_SUPERVISOR),
        ("operator", result["operator"], result["operator_password"], PERSONA_FRONTLINE),
    ):
        shown = pw if pw else "(unchanged — account already existed)"
        print(f"  {label:<11} {user.email:<29} {persona:<11} {shown}")

    print("\nSign in as each in a separate browser profile: the supervisor gets")
    print("Dispatch, the operator gets My Work. Passwords are shown once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
