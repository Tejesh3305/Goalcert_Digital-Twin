"""
scenario.py — the fleet-network fault catalogue + demo scenario presets.

Mirrors edm/scenario.py: one place that names every injectable fault the
physics understands (FleetPhysics.FAULTS), with operator-facing labels and
descriptions, so the orchestrator / UI / agents all speak the same language.
"""
from __future__ import annotations

FAULTS = [
    {"id": "ohl_damage", "label": "Overhead line damage",
     "subsystem": "power",
     "description": "Contact-wire damage / de-wirement on one corridor: arcing "
                    "losses, voltage sag, and a blocked route until isolated."},
    {"id": "substation_overload", "label": "Substation overload",
     "subsystem": "power",
     "description": "A traction substation loses rectifier capacity; feeder "
                    "load and voltage sag grow until protection trips."},
    {"id": "track_buckling", "label": "Track buckling (heat)",
     "subsystem": "track",
     "description": "Extreme rail temperature forces heat speed restrictions "
                    "and risks a buckle on exposed track."},
    {"id": "switch_failure", "label": "Points / switch failure",
     "subsystem": "track",
     "description": "Junction points fail; routes through the junction run "
                    "restricted or blocked, and headways collapse."},
    {"id": "signal_failure", "label": "Signalling failure",
     "subsystem": "signalling",
     "description": "Signalling outage puts junctions on manual working — "
                    "network throughput and headway adherence fall."},
    {"id": "brake_degradation", "label": "Fleet brake degradation",
     "subsystem": "rolling_stock",
     "description": "Fleet-wide brake pad wear: braking margin and regen "
                    "recovery fall, cars start failing brake tests."},
    {"id": "pantograph_wear", "label": "Pantograph carbon wear",
     "subsystem": "rolling_stock",
     "description": "Worn carbon strips arc against the contact wire — energy "
                    "waste now, de-wirement risk if unaddressed."},
    {"id": "door_system_fault", "label": "Door system faults",
     "subsystem": "rolling_stock",
     "description": "Door faults across the fleet blow out dwell times and "
                    "pull vehicles from service."},
    {"id": "wheel_flats", "label": "Wheel flats",
     "subsystem": "rolling_stock",
     "description": "Slide-induced wheel flats hammer bogies and track — "
                    "vibration rises and ride quality drops."},
    {"id": "demand_surge", "label": "Passenger demand surge",
     "subsystem": "operations",
     "description": "Event crowds surge demand: loads, dwell times and delays "
                    "climb; headways destabilise."},
]

SCENARIOS = [
    {"id": "heatwave", "name": "40 °C heatwave afternoon",
     "fault": "track_buckling", "severity": 0.8, "control": 0.85,
     "horizon_min": 240,
     "description": "Extreme heat drives rail temperature toward the buckling "
                    "limit while saloon HVAC load peaks the substations."},
    {"id": "footy-night", "name": "MCG event crowd surge",
     "fault": "demand_surge", "severity": 0.9, "control": 1.0,
     "horizon_min": 180,
     "description": "80k spectators leave the ground in 40 minutes — route 70/75 "
                    "corridor loads spike and dwell times blow out."},
    {"id": "cbd-signal", "name": "CBD signalling outage",
     "fault": "signal_failure", "severity": 0.85, "control": 0.85,
     "horizon_min": 120,
     "description": "An interlocking fault puts the Collins/Swanston junctions "
                    "on manual working in the peak."},
    {"id": "ohl-storm", "name": "Storm damage to overhead",
     "fault": "ohl_damage", "severity": 0.9, "control": 0.8,
     "horizon_min": 180,
     "description": "A storm brings a tree limb through the contact wire on one "
                    "corridor; sections isolate and services divert."},
]
