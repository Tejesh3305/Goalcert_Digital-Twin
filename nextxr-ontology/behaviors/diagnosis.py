"""
diagnosis.py — The Diagnosis Engine.

Implements the full reasoning chain from the ontology:

    Finding  →  Incident  →  Diagnosis  →  Recommendation  →  Action

The engine runs AFTER the behavior registry emits findings. It:
  1. Groups correlated findings into an Incident (temporal + entity proximity)
  2. Generates a Diagnosis explaining the root cause
  3. Produces a Recommendation with proposed actions
  4. Writes everything through the Graph Writer (validated by SHACL)

Each step uses the ontology predicates:
  - Finding  --[nxr:groupedInto]-->  Incident
  - Incident --[nxr:affects]-->      Asset
  - Incident --[nxr:diagnosedAs]-->  Diagnosis
  - Diagnosis --[nxr:recommends]-->  Recommendation
  - Recommendation --[nxr:proposesAction]--> Action
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

CORE = "https://ontology.nextxr.io/v3/core#"


@dataclass
class DiagnosisResult:
    incident_id: Optional[str] = None
    diagnosis_id: Optional[str] = None
    recommendation_id: Optional[str] = None
    action_id: Optional[str] = None
    findings_grouped: int = 0
    error: Optional[str] = None


class DiagnosisEngine:
    """Runs the full reasoning chain: findings → incident → diagnosis → recommendation."""

    def __init__(self, writer, query):
        self.writer = writer
        self.query = query

    def analyze(self, tenant_id: str, finding_ids: list[str],
                affected_entity_id: str) -> DiagnosisResult:
        """Given a list of correlated finding IDs, create the full reasoning chain."""
        if not finding_ids:
            return DiagnosisResult(error="No findings to analyze")

        # Gather finding details
        findings = []
        for fid in finding_ids:
            node = self.query.get_node(tenant_id, fid)
            if node:
                findings.append(node)

        if not findings:
            return DiagnosisResult(error="No valid findings found")

        # Determine severity: if any finding is critical, incident is critical
        severities = [f.get("severity", "info") for f in findings]
        incident_severity = "critical" if "critical" in severities else "warning"

        # Build incident summary from findings
        behavior_ids = set(f.get("behaviorId", "") for f in findings)
        tier_labels = set(f.get("tier", "") for f in findings)

        # ── Step 1: Create Incident ──────────────────────────────────
        incident_name = f"Incident: {len(findings)} finding(s) on entity"
        incident = self.writer.create(
            tenant_id=tenant_id,
            canonical_type=CORE + "Incident",
            actor="diagnosis-engine",
            properties={
                "displayName": incident_name,
                "severity": incident_severity,
                "status": "open",
                "findingCount": len(findings),
            },
            relationships=[
                # Incident affects the flagged entity
                {"predicate": "nxr:affects", "target_id": affected_entity_id},
            ],
        )
        if not incident.ok:
            return DiagnosisResult(error=f"Failed to create incident: {incident.error}")

        # Group each finding into the incident
        grouped = 0
        for fid in finding_ids:
            r = self.writer.relate(
                tenant_id=tenant_id, actor="diagnosis-engine",
                source_id=fid, predicate="nxr:groupedInto",
                target_id=incident.node_id,
            )
            if r.ok:
                grouped += 1

        # ── Step 2: Generate Diagnosis ───────────────────────────────
        # Analyze the pattern of findings to determine root cause
        diagnosis_text = self._generate_diagnosis_text(findings, behavior_ids, tier_labels)
        confidence = self._compute_confidence(findings, tier_labels)

        diagnosis = self.writer.create(
            tenant_id=tenant_id,
            canonical_type=CORE + "Diagnosis",
            actor="diagnosis-engine",
            properties={
                "displayName": diagnosis_text,
                "confidence": confidence,
                "status": "pending",
                "evidence": f"Based on {len(findings)} finding(s) from tiers {', '.join(sorted(tier_labels))}",
            },
        )
        if not diagnosis.ok:
            return DiagnosisResult(
                incident_id=incident.node_id,
                findings_grouped=grouped,
                error=f"Incident created but diagnosis failed: {diagnosis.error}",
            )

        # Link incident to diagnosis
        self.writer.relate(
            tenant_id=tenant_id, actor="diagnosis-engine",
            source_id=incident.node_id, predicate="nxr:diagnosedAs",
            target_id=diagnosis.node_id,
        )

        # Update incident status to "diagnosed"
        self.writer.update(
            tenant_id=tenant_id, node_id=incident.node_id,
            actor="diagnosis-engine",
            properties={"status": "diagnosed"},
        )

        # ── Step 3: Generate Recommendation ──────────────────────────
        rec_text = self._generate_recommendation(findings, diagnosis_text, incident_severity)

        recommendation = self.writer.create(
            tenant_id=tenant_id,
            canonical_type=CORE + "Recommendation",
            actor="diagnosis-engine",
            properties={
                "displayName": rec_text,
                "status": "proposed",
                "priority": incident_severity,
            },
        )

        if recommendation.ok:
            # Link diagnosis to recommendation
            self.writer.relate(
                tenant_id=tenant_id, actor="diagnosis-engine",
                source_id=diagnosis.node_id, predicate="nxr:recommends",
                target_id=recommendation.node_id,
            )

        # ── Step 4: Generate Action ──────────────────────────────────
        action_text, exec_mode = self._generate_action(incident_severity, rec_text)

        action = self.writer.create(
            tenant_id=tenant_id,
            canonical_type=CORE + "Action",
            actor="diagnosis-engine",
            properties={
                "displayName": action_text,
                "executionMode": exec_mode,
                "status": "pending",
            },
        )

        action_id = None
        if action.ok and recommendation.ok:
            action_id = action.node_id
            self.writer.relate(
                tenant_id=tenant_id, actor="diagnosis-engine",
                source_id=recommendation.node_id, predicate="nxr:proposesAction",
                target_id=action.node_id,
            )

        return DiagnosisResult(
            incident_id=incident.node_id,
            diagnosis_id=diagnosis.node_id,
            recommendation_id=recommendation.node_id if recommendation.ok else None,
            action_id=action_id,
            findings_grouped=grouped,
        )

    def _generate_diagnosis_text(self, findings, behavior_ids, tier_labels) -> str:
        """Generate a root-cause diagnosis from the pattern of findings."""
        has_threshold = any("threshold" in b for b in behavior_ids)
        has_zscore = any("zscore" in b for b in behavior_ids)
        has_physics = any("physics" in b for b in behavior_ids)

        if has_threshold and has_zscore:
            return ("Supply air temperature exceeded threshold AND deviated "
                    "from learned baseline. Likely cause: cooling capacity "
                    "degradation or increased thermal load.")
        elif has_threshold:
            return ("Supply air temperature exceeded operational threshold. "
                    "Possible cause: setpoint misconfiguration, fan failure, "
                    "or refrigerant loss.")
        elif has_zscore:
            return ("Supply air temperature shows statistical anomaly "
                    "relative to learned baseline. Possible drift in "
                    "operating conditions or sensor calibration issue.")
        elif has_physics:
            return ("Physics model residual exceeds tolerance. Actual "
                    "thermal behavior deviates from expected energy balance.")
        else:
            return f"Multiple anomalies detected by {', '.join(sorted(behavior_ids))}."

    def _compute_confidence(self, findings, tier_labels) -> float:
        """Confidence increases with corroborating evidence from multiple tiers."""
        tier_count = len(tier_labels - {""})
        base = 0.6
        if tier_count >= 3:
            return min(0.98, base + 0.3)
        elif tier_count >= 2:
            return min(0.95, base + 0.25)
        else:
            return base + (len(findings) - 1) * 0.05

    def _generate_recommendation(self, findings, diagnosis, severity) -> str:
        """Generate actionable recommendation based on diagnosis."""
        if severity == "critical":
            return ("URGENT: Inspect HVAC equipment immediately. Check "
                    "refrigerant levels, compressor operation, and fan belt "
                    "tension. Consider switching to backup unit if available.")
        else:
            return ("Schedule maintenance inspection within 48 hours. "
                    "Review sensor calibration and operating parameters. "
                    "Monitor trend for further deviation.")

    def _generate_action(self, severity, recommendation) -> tuple[str, str]:
        """Generate a concrete action step."""
        if severity == "critical":
            return ("Dispatch maintenance team for emergency HVAC inspection", "manual")
        else:
            return ("Create maintenance ticket for scheduled inspection", "manual")
