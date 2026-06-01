"""
state_machine.py — State machine enforcement for the Graph Writer.

Parses state machine declarations from the ontology (nxr:hasStateMachine)
and validates that status transitions on update() are legal.

Two state machines exist in the ontology:
  - AirHandler lifecycle: off → running → degraded → fault → off
  - Incident lifecycle:   open → diagnosed → acknowledged → resolved → closed

The writer calls validate_transition() before applying a status change.
If the transition is illegal, the update is rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from rdflib import URIRef
import gate

NXR = "https://ontology.nextxr.io/v3/core#"

_HAS_SM = URIRef(NXR + "hasStateMachine")
_HAS_STATE = URIRef(NXR + "hasState")
_INITIAL = URIRef(NXR + "initialState")
_ALLOWS = URIRef(NXR + "allowsTransition")
_FROM = URIRef(NXR + "fromState")
_TO = URIRef(NXR + "toState")
_SUBCLASS = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
_LABEL = URIRef("http://www.w3.org/2000/01/rdf-schema#label")

_cache: dict[str, Optional[dict]] = {}


def _load_state_machine(canonical_type: str) -> Optional[dict]:
    """Load the state machine for a canonical type from the ontology.
    Walks up rdfs:subClassOf chain to find inherited machines.
    Returns {initial, states, transitions: {from_state: [to_states]}} or None."""
    if canonical_type in _cache:
        return _cache[canonical_type]

    g = gate.ontology_graph()
    cls = URIRef(canonical_type)

    # Walk up the class hierarchy to find a state machine
    seen = set()
    frontier = [cls]
    sm = None
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        sm = g.value(cur, _HAS_SM)
        if sm is not None:
            break
        frontier.extend(g.objects(cur, _SUBCLASS))

    if sm is None:
        _cache[canonical_type] = None
        return None

    # Parse the state machine
    initial_node = g.value(sm, _INITIAL)
    initial = str(g.value(initial_node, _LABEL) or initial_node).lower() if initial_node else None

    states = set()
    for s in g.objects(sm, _HAS_STATE):
        label = g.value(s, _LABEL)
        if label:
            states.add(str(label).lower())

    transitions: dict[str, list[str]] = {}
    for t in g.objects(sm, _ALLOWS):
        f = g.value(t, _FROM)
        to = g.value(t, _TO)
        if f and to:
            f_label = str(g.value(f, _LABEL) or f).lower()
            to_label = str(g.value(to, _LABEL) or to).lower()
            transitions.setdefault(f_label, []).append(to_label)

    result = {
        "initial": initial,
        "states": states,
        "transitions": transitions,
    }
    _cache[canonical_type] = result
    return result


def validate_transition(canonical_type: str, old_status: Optional[str],
                        new_status: str) -> Optional[str]:
    """Check if transitioning from old_status to new_status is legal.
    Returns None if legal, or an error message string if illegal."""
    sm = _load_state_machine(canonical_type)
    if sm is None:
        return None  # No state machine defined — any status is fine

    new_lower = new_status.lower()

    # Check the new status is a known state
    if new_lower not in sm["states"]:
        return (f"Status '{new_status}' is not a legal state. "
                f"Legal states: {sorted(sm['states'])}")

    # If no old status, must be initial
    if old_status is None or old_status == "":
        if new_lower != sm["initial"]:
            return (f"Initial status must be '{sm['initial']}', "
                    f"not '{new_status}'")
        return None

    old_lower = old_status.lower()
    if old_lower == new_lower:
        return None  # No change

    # Check transition is allowed
    allowed = sm["transitions"].get(old_lower, [])
    if new_lower not in allowed:
        return (f"Transition '{old_status}' → '{new_status}' is not allowed. "
                f"From '{old_status}' you can go to: {allowed or ['(none — terminal state)']}")

    return None


def get_state_machine(canonical_type: str) -> Optional[dict]:
    """Get the state machine definition for a type, if one exists."""
    return _load_state_machine(canonical_type)
