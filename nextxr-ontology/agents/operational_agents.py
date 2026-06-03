"""
operational_agents.py — Team 2: Runtime / Operational agents.

These wake when the Correlation Engine (feed loop) groups findings into an
Incident. They turn that Incident into something an operator can act on.

    Context Gatherer → Diagnosis Agent → Recommender Agent → END

The Context Gatherer is deterministic (graph reads only). The Diagnosis and
Recommender agents use LLM reasoning but have full deterministic stubs that
reproduce the existing DiagnosisEngine output. This means the upgrade is
additive — the deterministic pipeline keeps working; the LLM adds richer
hypotheses and recommendations.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.gateway import get_gateway

CORE = "https://ontology.nextxr.io/v3/core#"

# Simple in-memory cache for diagnosis results (24h TTL).
_diagnosis_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 86400  # 24 hours


# ==========================================================================
#  Context Gatherer   (deterministic — reads graph, populates state)
# ==========================================================================
def context_gatherer(state: dict) -> dict:
    """Fetch finding details, downstream dependents, and past incidents from
    the graph so the Diagnosis Agent has rich context to reason over."""
    from graph.query import GraphQuery

    tenant_id = state["tenant_id"]
    finding_ids = state.get("finding_ids") or []
    affected_id = state.get("affected_entity_id") or ""

    try:
        q = GraphQuery()
    except Exception:
        return {"finding_details": [], "graph_dependents": [],
                "past_incidents": [], "next_action": "diagnose",
                "errors": state.get("errors", []) + ["Graph unavailable"]}

    # 1. Fetch each finding.
    details = []
    for fid in finding_ids:
        try:
            node = q.get_node(tenant_id, fid)
            if node:
                details.append(node)
        except Exception:
            pass

    # 2. Downstream dependents of the affected entity.
    dependents = []
    if affected_id:
        try:
            dependents = q.dependents(tenant_id, affected_id, max_depth=3)
        except Exception:
            pass

    # 3. Past incidents for pattern matching (last 20).
    past = []
    try:
        past = q.get_incidents(tenant_id, limit=20)
        # Exclude the current incident.
        past = [i for i in past if i.get("id") != state.get("incident_id")]
    except Exception:
        pass

    has_details = bool(details)
    return {
        "finding_details": details,
        "graph_dependents": dependents,
        "past_incidents": past[:10],
        "next_action": "diagnose" if has_details else "done",
    }


# ==========================================================================
#  Diagnosis Agent   (LLM — ranked root-cause hypotheses)
# ==========================================================================
def diagnosis_agent(state: dict) -> dict:
    """Given an Incident with findings, produces ranked root-cause hypotheses
    with confidence scores and supporting evidence. Cached 24h by signature.
    Writes the Diagnosis node to the graph via GraphWriter."""
    gw = get_gateway()
    tenant_id = state["tenant_id"]
    incident_id = state.get("incident_id") or ""
    findings = state.get("finding_details") or []

    # Build a cache signature from finding characteristics.
    sig_parts = sorted(set(
        f.get("behaviorId", "") + ":" + f.get("severity", "")
        for f in findings
    ))
    signature = hashlib.sha256(
        (tenant_id + ":" + ":".join(sig_parts)).encode()
    ).hexdigest()[:16]

    # Check cache.
    cached = _diagnosis_cache.get(signature)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return {"diagnosis": cached[1], "diagnosis_cached": True,
                "next_action": "recommend"}

    # Build context for the LLM.
    finding_summary = []
    behavior_ids = set()
    tiers = set()
    for f in findings:
        behavior_ids.add(f.get("behaviorId", "unknown"))
        tiers.add(f.get("tier", "?"))
        finding_summary.append({
            "severity": f.get("severity"), "tier": f.get("tier"),
            "behavior": f.get("behaviorId"), "message": f.get("message"),
            "confidence": f.get("confidence"),
        })

    dependents = state.get("graph_dependents") or []
    dep_summary = [{"id": d.get("id"), "type": d.get("canonicalType"),
                    "name": d.get("displayName")} for d in dependents[:10]]

    past = state.get("past_incidents") or []
    past_summary = [{"severity": p.get("severity"),
                     "finding_count": p.get("findingCount")}
                    for p in past[:5]]

    def _stub() -> dict:
        # Deterministic diagnosis matching existing DiagnosisEngine logic.
        bids = behavior_ids
        if "threshold" in str(bids) and "zscore" in str(bids):
            cause = "Cooling capacity degradation detected across multiple tiers."
            conf = 0.85
        elif "threshold" in str(bids):
            cause = "Possible setpoint misconfiguration or equipment failure."
            conf = 0.7
        elif "zscore" in str(bids):
            cause = "Statistical anomaly — possible sensor drift or load change."
            conf = 0.65
        elif "physics" in str(bids):
            cause = "Thermal behaviour deviates from energy balance model."
            conf = 0.75
        else:
            cause = "Anomaly detected by behaviour model."
            conf = 0.6
        return {"hypotheses": [
            {"cause": cause, "confidence": conf,
             "evidence": f"Based on {len(findings)} finding(s) from "
                         f"tier(s) {', '.join(tiers)}.", "rank": 1},
        ], "signature": signature}

    result = gw.complete_json(
        tenant_id=tenant_id, session_id=state["session_id"],
        system=("You are a facility diagnostician for a digital-twin platform. "
                "Given an incident with grouped findings, produce ranked "
                "root-cause hypotheses. Return JSON "
                "{\"hypotheses\": [{\"cause\": str, \"confidence\": 0.0-1.0, "
                "\"evidence\": str, \"rank\": int}]}. "
                "Rank by confidence descending. Max 5 hypotheses. "
                "Be specific about equipment/system root causes."),
        user=json.dumps({
            "incident_id": incident_id,
            "findings": finding_summary,
            "affected_dependents": dep_summary,
            "past_incidents": past_summary,
            "behavior_ids": list(behavior_ids),
            "tiers": list(tiers),
        }),
        stub=_stub(),
        max_tokens=900,
    )

    hypotheses = result.get("hypotheses") or _stub()["hypotheses"]
    diagnosis = {"hypotheses": hypotheses, "signature": signature}

    # Write diagnosis node to graph (best-effort).
    try:
        from graph.writer import GraphWriter
        from changelog.service import ChangeLog
        writer = GraphWriter(changelog=ChangeLog())
        top = hypotheses[0] if hypotheses else {"cause": "Unknown", "confidence": 0.5}
        res = writer.create(
            tenant_id=tenant_id,
            canonical_type=CORE + "Diagnosis",
            actor="agent:diagnosis",
            properties={
                "displayName": top["cause"][:200],
                "confidence": float(top.get("confidence", 0.5)),
                "status": "pending",
                "evidence": json.dumps(hypotheses)[:500],
            },
        )
        if res.ok and incident_id:
            writer.relate(
                tenant_id=tenant_id, actor="agent:diagnosis",
                source_id=incident_id, predicate="nxr:diagnosedAs",
                target_id=res.node_id,
            )
            diagnosis["diagnosis_node_id"] = res.node_id
    except Exception:
        pass

    # Cache the result.
    _diagnosis_cache[signature] = (time.time(), diagnosis)

    return {"diagnosis": diagnosis, "diagnosis_cached": False,
            "next_action": "recommend"}


# ==========================================================================
#  Recommender Agent   (LLM — ranked actions, advisory-only)
# ==========================================================================
def recommender_agent(state: dict) -> dict:
    """Given a diagnosed Incident, produces ranked recommended actions, each
    tagged auto (advisory, no approval needed) or manual (needs a human).
    Advisory-only by lock — this agent NEVER actuates, only recommends."""
    gw = get_gateway()
    tenant_id = state["tenant_id"]
    diagnosis = state.get("diagnosis") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    findings = state.get("finding_details") or []

    # Determine overall severity from findings.
    severities = [f.get("severity", "info") for f in findings]
    is_critical = "critical" in severities

    def _stub() -> dict:
        if is_critical:
            return {"recommendations": [
                {"action": "Dispatch maintenance team for emergency inspection.",
                 "priority": "critical", "mode": "manual",
                 "rationale": "Critical findings require immediate human response."},
                {"action": "Create high-priority maintenance ticket.",
                 "priority": "high", "mode": "auto",
                 "rationale": "Document the incident for follow-up."},
            ]}
        return {"recommendations": [
            {"action": "Schedule maintenance inspection within 48 hours.",
             "priority": "medium", "mode": "manual",
             "rationale": "Warning-level findings warrant timely investigation."},
            {"action": "Create maintenance ticket for tracking.",
             "priority": "medium", "mode": "auto",
             "rationale": "Ensure the observation is logged."},
        ]}

    hyp_text = json.dumps(hypotheses[:3])
    finding_text = json.dumps([{
        "severity": f.get("severity"), "message": f.get("message"),
        "tier": f.get("tier"),
    } for f in findings[:5]])

    result = gw.complete_json(
        tenant_id=tenant_id, session_id=state["session_id"],
        system=("You recommend actions for a diagnosed facility incident. "
                "Each action is tagged 'auto' (can be automated, advisory) or "
                "'manual' (requires human). Return JSON "
                "{\"recommendations\": [{\"action\": str, "
                "\"priority\": \"critical\"|\"high\"|\"medium\"|\"low\", "
                "\"mode\": \"auto\"|\"manual\", \"rationale\": str}]}. "
                "Max 5 recommendations. Be specific and actionable."),
        user=f"Diagnosis hypotheses: {hyp_text}\n\n"
             f"Findings: {finding_text}\n\n"
             f"Overall severity: {'critical' if is_critical else 'warning'}",
        stub=_stub(),
        max_tokens=700,
    )

    recs = result.get("recommendations") or _stub()["recommendations"]

    # Write recommendations to graph (best-effort).
    try:
        from graph.writer import GraphWriter
        from changelog.service import ChangeLog
        writer = GraphWriter(changelog=ChangeLog())
        diag_node = diagnosis.get("diagnosis_node_id")

        for rec in recs[:3]:  # write top 3
            res = writer.create(
                tenant_id=tenant_id,
                canonical_type=CORE + "Recommendation",
                actor="agent:recommender",
                properties={
                    "displayName": rec["action"][:200],
                    "status": "proposed",
                    "priority": rec.get("priority", "medium"),
                },
            )
            if res.ok and diag_node:
                writer.relate(
                    tenant_id=tenant_id, actor="agent:recommender",
                    source_id=diag_node, predicate="nxr:recommends",
                    target_id=res.node_id,
                )
            # Write the Action node linked to the Recommendation.
            if res.ok:
                action_res = writer.create(
                    tenant_id=tenant_id,
                    canonical_type=CORE + "Action",
                    actor="agent:recommender",
                    properties={
                        "displayName": rec["action"][:200],
                        "executionMode": rec.get("mode", "manual"),
                        "status": "pending",
                    },
                )
                if action_res.ok:
                    writer.relate(
                        tenant_id=tenant_id, actor="agent:recommender",
                        source_id=res.node_id, predicate="nxr:proposesAction",
                        target_id=action_res.node_id,
                    )
    except Exception:
        pass

    return {"recommendations": recs, "advisory_lock": True,
            "next_action": "done"}


# ==========================================================================
#  Routers
# ==========================================================================
def route_after_gather(state: dict) -> str:
    return "diagnose" if state.get("finding_details") else "done"


def route_after_diagnose(state: dict) -> str:
    return "recommend" if state.get("diagnosis") else "done"
