"""tenants.py — who owns which twin, as an operator command.

WHY THIS EXISTS
---------------
`org_tenants` is the authorization relation: a signed-in user reaches a twin
because the organisation they belong to owns it there, and for no other reason.
That is the rule that replaced the old tenant-prefix convention, and it has one
consequence nothing else in the platform surfaces — a twin missing from the table
is invisible to every user, while remaining perfectly present in the registry.

Which is exactly the state of any twin created before `identity/` existed. The
API returns 200, the registry has fourteen twins, `GET /twins` returns none of
them, and no log line explains why. The boot log now warns (see
`server/tenancy.py`), and this is the command that warning points at.

WHY IT IS A COMMAND AND NOT A MIGRATION
---------------------------------------
Because the answer is a business fact, not a schema change. "Which customer owns
this existing twin" cannot be derived from the data — and the plausible-looking
default, handing them to whichever organisation happens to exist, is a
cross-tenant disclosure the moment a second customer is onboarded. So an operator
names the organisation, once, deliberately.

USAGE
-----
    python -m identity.tenants                        report ownership
    python -m identity.tenants --share --tenant <ID> [--tenant <ID> ...]
                                                      make twins common to EVERY
                                                      account
    python -m identity.tenants --unshare --tenant <ID>
                                                      stop sharing one (it goes
                                                      back to owned by nobody)
    python -m identity.tenants --org acme --adopt-unowned
                                                      claim every unowned twin
    python -m identity.tenants --org acme --tenant demo-railway-metro
                                                      claim one twin
    python -m identity.tenants --org acme --adopt-unowned --dry-run

`--share` assigns to the reserved `nxr:shared` organisation, which every
authenticated principal can reach (`resolve.py::shared_tenants`). That is how the
platform's demo twins become the set a brand-new account lands on instead of an
empty library. It grants READ AND WRITE, so a visitor can drive the twin, inject
a fault and run a repair — the changes are shared, which is the point of a
showcase and the reason customer data must never be put in this organisation.

Claiming is idempotent and NEVER reassigns: a twin already owned by another
organisation is reported and skipped, because a transfer between customers is a
deliberate act (`store.transfer_tenant`) rather than something a bulk command
does as a side effect.
"""
from __future__ import annotations

import sys

from . import store


def registry_tenants() -> list[str]:
    """Every tenant id in the twin registry, oldest first."""
    from db import connect

    with connect("twins") as conn:
        rows = conn.execute(
            "SELECT tenant_id FROM twins ORDER BY created_at").fetchall()
    return [dict(r)["tenant_id"] for r in rows]


def ownership() -> tuple[dict[str, str], list[str]]:
    """({tenant: org}, [unowned tenants]) across the whole registry."""
    owners: dict[str, str] = {}
    unowned: list[str] = []
    for tenant in registry_tenants():
        owner = store.tenant_owner(tenant)
        if owner:
            owners[tenant] = owner
        else:
            unowned.append(tenant)
    return owners, unowned


def adopt(org_id: str, tenants: list[str], *,
          dry_run: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """Claim `tenants` for `org_id`.

    Returns (claimed, [(tenant, existing_owner)]) — the second list is the twins
    that were left alone because somebody else already owns them.
    """
    claimed: list[str] = []
    conflicts: list[tuple[str, str]] = []
    for tenant in tenants:
        existing = store.tenant_owner(tenant)
        if existing == org_id:
            continue                       # already ours
        if existing:
            conflicts.append((tenant, existing))
            continue
        if not dry_run:
            store.claim_tenant(tenant, org_id)
            store.audit("tenant.adopted", org_id=org_id, target_type="tenant",
                        target_id=tenant, detail={"via": "identity.tenants"})
        claimed.append(tenant)
    return claimed, conflicts


# ── CLI ─────────────────────────────────────────────────────────────────


def _arg(argv: list[str], name: str) -> str:
    """`--name value`, or "" when absent."""
    if name not in argv:
        return ""
    index = argv.index(name)
    return argv[index + 1] if index + 1 < len(argv) else ""


def _args(argv: list[str], name: str) -> list[str]:
    """Every `--name value` occurrence, so several twins can be named at once."""
    out = []
    for i, token in enumerate(argv):
        if token == name and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def unshare(tenants: list[str], *, dry_run: bool = False) -> list[str]:
    """Stop sharing twins. They become owned by nobody — deliberately, rather
    than falling to some organisation: whoever should own one next is a decision,
    and `--org <ID> --tenant <ID>` is how it gets made."""
    done = []
    for tenant in tenants:
        if store.tenant_owner(tenant) != store.SHARED_ORG_ID:
            continue
        if not dry_run:
            store.release_tenant(tenant)
            store.audit("tenant.unshared", org_id=store.SHARED_ORG_ID,
                        target_type="tenant", target_id=tenant,
                        detail={"via": "identity.tenants"})
        done.append(tenant)
    return done


def main(argv: list[str]) -> int:
    org_id = store.SHARED_ORG_ID if "--share" in argv else _arg(argv, "--org")
    tenants = _args(argv, "--tenant")
    adopt_all = "--adopt-unowned" in argv
    dry_run = "--dry-run" in argv

    if "--unshare" in argv:
        if not tenants:
            print("Nothing to do: --unshare needs at least one --tenant <ID>.")
            return 1
        released = unshare(tenants, dry_run=dry_run)
        for name in released:
            print(f"  {'would stop' if dry_run else 'stopped'} sharing {name}")
        skipped = [t for t in tenants if t not in released]
        for name in skipped:
            print(f"  skipped {name} - not shared "
                  f"(owner: {store.tenant_owner(name) or 'nobody'})")
        if not dry_run and released:
            from . import resolve
            resolve.clear_shared_cache()
        print(f"\n{len(released)} twin(s) no longer shared."
              f"{' Dry run - nothing changed.' if dry_run else ''}")
        return 0

    if "--share" in argv and not dry_run:
        store.ensure_shared_org()

    owners, unowned = ownership()

    if not org_id:
        orgs = store.list_orgs(limit=1000)
        print(f"organisations: {len(orgs)}")
        for org in orgs:
            owned = [t for t, o in owners.items() if o == org.org_id]
            label = (" [shared with every account]"
                     if org.org_id == store.SHARED_ORG_ID else "")
            print(f"  {org.org_id:<28} {org.name}  ({len(owned)} twin(s)){label}")
        shared = [t for t, o in owners.items() if o == store.SHARED_ORG_ID]
        if shared:
            print(f"\ncommon to every account: {len(shared)}")
            for name in shared:
                print(f"  {name}")
        print(f"\nunowned twins: {len(unowned)}")
        for name in unowned:
            print(f"  {name}")
        if unowned:
            print("\nNo signed-in user can see these. To assign them:\n"
                  "  python -m identity.tenants --org <ORG_ID> --adopt-unowned\n"
                  "  python -m identity.tenants --share --tenant <ID>"
                  "   (visible to everyone)")
        return 0

    if store.get_org(org_id) is None:
        print(f"No such organisation: {org_id!r}. Run without --org to list them.")
        return 1

    known = set(registry_tenants())
    if tenants:
        targets = tenants
        for name in targets:
            if name not in known:
                print(f"  !! {name} is not in the twin registry. Sharing it now "
                      f"is harmless but grants nothing until a twin with that "
                      f"exact id exists.")
    elif adopt_all:
        targets = unowned
    else:
        print("Nothing to do: pass --tenant <ID> or --adopt-unowned.")
        return 1

    if not targets:
        print("No unowned twins — every twin already belongs to an organisation.")
        return 0

    claimed, conflicts = adopt(org_id, targets, dry_run=dry_run)

    for name, owner in conflicts:
        print(f"  skipped {name} - already owned by {owner}. Transferring "
              f"between organisations is deliberate; use store.transfer_tenant.")
    for name in claimed:
        print(f"  {'would claim' if dry_run else 'claimed'} {name} -> {org_id}")

    shared_now = org_id == store.SHARED_ORG_ID
    print(f"\n{len(claimed)} twin(s) "
          f"{'would be assigned' if dry_run else 'assigned'} to {org_id}"
          f"{' — every account now lands on them' if shared_now and not dry_run else ''}."
          f"{' Dry run - nothing changed.' if dry_run else ''}")

    # The reachable set is cached per task for a few seconds; drop it here so a
    # server already running in this process sees the change at once.
    if not dry_run:
        from . import resolve
        resolve.clear_shared_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
