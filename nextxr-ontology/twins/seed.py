"""seed.py — ensure the standard demo twins exist on startup (idempotent).

A fresh deployment starts with an empty Neo4j, so the Twins library would be
blank. This seeds the same standard set the platform has always shipped — one
twin per machine domain plus a generic facility — so the library is populated
out of the box. It is safe to run on every boot: a domain that already has a
twin is skipped (never duplicated), and the whole thing no-ops if Neo4j is
unreachable. User-built twins are untouched.
"""
from __future__ import annotations

import logging

log = logging.getLogger("nxr.twins.seed")

# (template key, display name). These are the domains the hub renders a live
# machine dashboard for, plus a generic facility.
DEMO_TWINS = [
    ("turbine-engine", "Gas Turbine"),
    ("edm-machine", "Wire EDM"),
    ("tram-network", "Melbourne Tram Network"),
    ("railway-metro", "Singapore MRT"),
    ("railway-trainset", "Rolling Stock"),
    ("hospital-campus", "Hospital Campus"),
    ("ev-charging-network", "EV Charging Network"),
    ("ev-battery-pack", "EV Battery Pack"),
    ("defence-base", "Military Base"),
    ("defence-warship", "Warship"),
    ("generic-facility", "Generic Facility"),
]


def seed_demo_twins() -> None:
    """Create any missing standard demo twin. Best-effort; never raises."""
    try:
        from graph.connection import get_driver
        get_driver().verify_connectivity()  # fail fast if Neo4j is down
    except Exception as e:  # noqa: BLE001
        log.info("demo-twin seed skipped (Neo4j unavailable): %s", e)
        return

    try:
        from twins import TwinRegistry
        from graph.writer import GraphWriter
        from changelog.service import ChangeLog

        reg = TwinRegistry()  # __init__ rehydrates user twins from the graph
        have = {t.domain for t in reg.list()}
        writer = GraphWriter(changelog=ChangeLog())

        created = 0
        for domain, name in DEMO_TWINS:
            if domain in have:
                continue
            try:
                # Deterministic tenant id per domain: create() rejects an id that
                # already exists, so a re-run or a second booting instance can't
                # produce a duplicate for the same domain (the create is the lock).
                reg.create(name=name, domain=domain, writer=writer,
                           tenant_id=f"demo-{domain}")
                created += 1
            except Exception as e:  # noqa: BLE001 — one bad domain shouldn't stop the rest
                log.warning("demo-twin seed for %s failed: %s", domain, e)
        if created:
            log.info("seeded %d demo twins", created)
    except Exception as e:  # noqa: BLE001
        log.warning("demo-twin seed aborted: %s", e)
