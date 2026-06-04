"""
flows.py — the flow vocabulary that makes coupling GENERIC across every domain.

The engine is domain-agnostic: it never says "HVAC". It only knows that ontology
relationships carry a FLOW, and a model consumes/produces flows. This tiny table
maps relationship predicates to flow types, so a model can ask "give me my
ELECTRICAL source" or "my THERMAL_FLUID source" without hardcoding a predicate —
and the same mechanism couples a maritime generator→switchboard→winch exactly
like a utility→transformer→chiller.

A predicate's flow can be context-dependent (nxr:feeds carries power, water, OR
data depending on the entities). We resolve that two ways:
  1. specialised predicates have a fixed flow (cfp:suppliesAirTo -> AIR), and
  2. for the generic nxr:feeds/dependsOn, the CONSUMER decides which upstream
     neighbour is its source by reading the signals it expects (a power consumer
     reads activePower; a thermal consumer reads chilled-water temp). The engine
     just hands over all upstream states grouped by predicate; flows.py provides
     helpers to pick the right ones.
"""

from __future__ import annotations

from enum import Enum


class Flow(str, Enum):
    ELECTRICAL = "electrical"      # kW / V / A along feeds, backsUp, fedBy
    THERMAL_FLUID = "thermal_fluid"  # chilled/hot/condenser water along feeds
    AIR = "air"                    # conditioned air along suppliesAirTo/servesSpace
    DATA = "data"                  # network throughput along feeds (ICT)
    CONTROL = "control"            # command/setpoint along controls
    SPATIAL = "spatial"            # containment: ambient conditions of the space
    OBSERVATION = "observation"    # sensor reads the monitored feature
    BACKUP = "backup"              # standby source along backsUp
    GENERIC = "generic"            # unclassified dependency


# Local-name (after # or /) -> default flow. Specialised predicates are
# unambiguous; the generic ones default to GENERIC and are disambiguated by the
# consuming model via the signals it reads.
_PREDICATE_FLOW = {
    # power
    "feeds": Flow.GENERIC,        # generic — could be power/water/data
    "fedBy": Flow.GENERIC,
    "serves": Flow.GENERIC,
    "backsUp": Flow.BACKUP,
    # HVAC air
    "suppliesAirTo": Flow.AIR,
    "servesSpace": Flow.AIR,
    # control
    "controls": Flow.CONTROL,
    # spatial / ambient
    "locatedAt": Flow.SPATIAL,
    "containedIn": Flow.SPATIAL,
    "contains": Flow.SPATIAL,
    "partOf": Flow.SPATIAL,
    # observation
    "monitors": Flow.OBSERVATION,
    "observes": Flow.OBSERVATION,
    # dependency
    "dependsOn": Flow.GENERIC,
}


def predicate_local(pred: str) -> str:
    """Local name of a predicate CURIE or IRI ('nxr:feeds' / '…#feeds' -> 'feeds')."""
    if "#" in pred:
        return pred.split("#")[-1]
    if ":" in pred and not pred.startswith("http"):
        return pred.split(":", 1)[1]
    return pred.rstrip("/").split("/")[-1]


def flow_of(predicate: str) -> Flow:
    return _PREDICATE_FLOW.get(predicate_local(predicate), Flow.GENERIC)


# ---- helpers a model uses to pick its inputs from ctx.inputs ----------------
def upstream_with_signal(ctx, signal_iri: str):
    """All upstream EntityStates (across any predicate) that publish `signal_iri`.
    This is how a power consumer finds its electrical source generically: it asks
    for the upstream that publishes cfp:activePower / voltage, regardless of which
    predicate connects them."""
    out = []
    for _pred, states in ctx.inputs.items():
        for st in states:
            if signal_iri in st.signals:
                out.append(st)
    return out


def upstream_by_flow(ctx, flow: Flow):
    """All upstream EntityStates connected by a predicate of the given flow type."""
    out = []
    for pred, states in ctx.inputs.items():
        if flow_of(pred) == flow:
            out.extend(states)
    return out


def first_signal(states, signal_iri: str, default: float = 0.0) -> float:
    """Sum (or take first) of a signal across a list of upstream states.
    For sources we usually take the first available; callers can sum if needed."""
    for st in states:
        if signal_iri in st.signals:
            return st.signals[signal_iri]
    return default


def sum_signal(states, signal_iri: str) -> float:
    return sum(st.signals.get(signal_iri, 0.0) for st in states)
