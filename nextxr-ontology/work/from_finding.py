"""from_finding.py — the twin detects, a person gets dispatched.

THIS IS THE JOIN THE TWIN WAS MISSING.

Everything before this module ends at a Finding node: the behaviour registry
evaluates telemetry, the writer commits a Finding, the change log stamps it, and
the dashboard turns red. Nothing then says WHO FIXES IT. An operator learned
about a critical fault by looking at a screen, and the twin had no record that
anyone had been told, had started, or had finished.

    Finding committed  ->  matching rule  ->  Task raised (source=twin_finding)
                       ->  supervisor assigns  ->  operator runs the procedure
                       ->  XP awarded, task resolved
                       ->  supervisor closes  ->  Finding marked resolved

The last arrow is what makes this a loop rather than a queue. Closing the task
writes `status=resolved` back onto the Finding node through the ordinary
GraphWriter, so the twin's own state machine — not this module — decides whether
that transition is legal.

RULES ARE DATA, NOT CODE
------------------------
`work_rules` decides which findings become work. Raising the bar from "critical
only" to "warnings on chillers too" is a row edit rather than a deploy, which
matters because the right threshold is a customer's operational judgement and
not something to hardcode.

WHY THIS IS CALLED SYNCHRONOUSLY
--------------------------------
The findings loop calls `handle_finding` straight after the Finding commits,
rather than publishing to the bus. Dispatch must be transactional with detection:
"the fault was detected" and "somebody was told" arriving at different times is
precisely the gap this module closes. An eventually-consistent version of it
re-opens that gap.
"""
from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime

from work import store
from work.models import severity_rank

logger = logging.getLogger("work.from_finding")


def _matches(rule: dict, *, behavior_id: str, severity: str, tenant_id: str) -> bool:
    if not rule.get("enabled", 1):
        return False
    rule_tenant = (rule.get("tenant_id") or "").strip()
    if rule_tenant and rule_tenant != tenant_id:
        return False
    if not fnmatch.fnmatch(behavior_id or "", rule.get("behavior_glob") or "*"):
        return False
    return severity_rank(severity) >= severity_rank(rule.get("min_severity") or "critical")


def handle_finding(*, org_id: str, tenant_id: str, finding_node_id: str,
                   behavior_id: str, severity: str, message: str,
                   asset_node_id: str = "", asset_name: str = "",
                   changelog_ref: str = "") -> dict | None:
    """Raise a task for one NEW finding. Returns the task's public dict, or None.

    None is the ordinary outcome, not an error: most findings are below the
    threshold, and a fault that is already dispatched must not be dispatched
    twice.
    """
    if not org_id:
        # A twin running without an organisation (local dev, no identity tables)
        # has nobody to dispatch to. Detection still works; dispatch is simply
        # not configured, and that is not a failure worth logging loudly.
        return None

    store.ensure_default_rule(org_id)
    rules = store.list_rules(org_id)
    rule = next((r for r in rules
                 if _matches(r, behavior_id=behavior_id, severity=severity,
                             tenant_id=tenant_id)), None)
    if rule is None:
        return None

    # One live task per finding node. The findings loop collapses a persisting
    # fault into a single node, so an ongoing fault is one task however long it
    # runs — and a CLOSED task does not suppress the same fault recurring later.
    if store.live_task_for_finding(org_id=org_id,
                                   finding_node_id=finding_node_id) is not None:
        return None

    scenario_id = rule.get("scenario_id") or _scenario_for(behavior_id)
    task = store.create_task(
        org_id=org_id, tenant_id=tenant_id,
        title=(message or f"{behavior_id} on {asset_name or 'asset'}")[:200],
        detail=message or "",
        finding_node_id=finding_node_id, asset_node_id=asset_node_id,
        asset_name=asset_name, behavior_id=behavior_id, severity=severity,
        scenario_id=scenario_id, priority=rule.get("priority") or "high",
        source="twin_finding", status="open", changelog_ref=changelog_ref)

    store.add_event(
        org_id=org_id, task_id=task.task_id, kind="raised",
        actor_id="system", actor_name="twin",
        summary=f"{severity} finding raised by {behavior_id}",
        payload={"finding_node_id": finding_node_id, "behavior_id": behavior_id,
                 "severity": severity, "changelog_ref": changelog_ref})

    logger.info("finding %s (%s) raised task %s on %s",
                behavior_id, severity, task.code, asset_name or asset_node_id)
    return task.public()


def _scenario_for(behavior_id: str) -> str:
    """Which fix procedure teaches this fault.

    Delegated to the scenario catalog rather than decided here: the catalog owns
    the behaviour -> procedure mapping, and duplicating it would mean a new
    procedure had to be registered in two places to actually reach an operator.
    """
    try:
        from scenario import catalog
        return catalog.scenario_id_for_behavior(behavior_id)
    except Exception:
        logger.debug("no scenario catalog available", exc_info=True)
        return ""


def resolve_finding(task) -> bool:
    """Mark the twin's Finding resolved when its task closes.

    Routed through the ordinary GraphWriter so the ontology's Finding state
    machine judges the transition. If the graph refuses — because the node is
    already resolved, or in a state that cannot reach it — this returns False
    and the caller carries on: the task is closed either way, and a graph blip
    must not strand a finished job.
    """
    node_id = getattr(task, "finding_node_id", "") or ""
    tenant_id = getattr(task, "tenant_id", "") or ""
    if not node_id or not tenant_id:
        return False
    try:
        from changelog.service import ChangeLog
        from graph.writer import GraphWriter

        writer = GraphWriter(changelog=ChangeLog())
        result = writer.update(
            tenant_id=tenant_id, node_id=node_id, actor="work:closed",
            properties={"status": "resolved",
                        "resolvedAt": datetime.now(UTC).isoformat()})
        return bool(getattr(result, "ok", False))
    except Exception:
        logger.debug("could not resolve finding %s", node_id, exc_info=True)
        return False
