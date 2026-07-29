"""defence — defence / military digital-twin domain (machine-twin runtime).

Exposes two SPECs:
  * SPEC         — "defence-base": a military base / C4ISR twin with a NATO APP-6
                   tactical map and a mission board.
  * WARSHIP_SPEC — "defence-warship": a naval surface combatant with propulsion,
                   stability, structural fatigue and a damage-control view.
"""
from .behaviors import build_defence_registry
from .physics import (
    FAULTS,
    SIGNALS,
    UNITS,
    DefenceBasePhysics,
    gas_turbine_brayton,
    nbc_contamination_spread,
    radar_propagation,
    redlines,
    ship_stability,
    structural_fatigue,
)
from .predict import component_health, predict
from .warship import SPEC as WARSHIP_SPEC

SPEC = {
    "key": "defence-base",
    "label": "Military Base (C4ISR)",
    "class_iri": "https://ontology.nextxr.io/v3/defence#MilitaryBase",
    "control": "readiness",
    "physics": DefenceBasePhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_defence_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["threat_level"]: ("Threat Level", ""),
        SIGNALS["perimeter_breaches"]: ("Perimeter Breaches", ""),
        SIGNALS["tracked_objects"]: ("Tracked Objects", ""),
        SIGNALS["radar_coverage"]: ("Radar Coverage", "%"),
        SIGNALS["radar_range"]: ("Radar Range", "km"),
        SIGNALS["jamming_level"]: ("Jamming Level", "%"),
        SIGNALS["comms_availability"]: ("Comms Availability", "%"),
        SIGNALS["ammo_temp"]: ("Ammunition Temp", "°C"),
        SIGNALS["ammo_humidity"]: ("Ammunition Humidity", "%"),
        SIGNALS["cookoff_margin"]: ("Cook-off Margin", "°C"),
        SIGNALS["fuel_level"]: ("Fuel Level", "%"),
        SIGNALS["nbc_reading"]: ("NBC Reading", ""),
        SIGNALS["nbc_plume_range"]: ("NBC Plume Range", "km"),
        SIGNALS["aircraft_avail"]: ("Aircraft Availability", "%"),
        SIGNALS["flight_hours"]: ("Flight Hours to Service", "h"),
        SIGNALS["sorties_ready"]: ("Sorties Ready", ""),
        SIGNALS["mission_readiness"]: ("Mission Readiness", "%"),
        SIGNALS["force_protection"]: ("Force Protection", ""),
    },
    "subsystems": [
        ("force_protection", "Force Protection"),
        ("isr", "ISR / Radar"),
        ("c4isr", "C4ISR / Comms"),
        ("logistics", "Logistics (Fuel/Ammo)"),
        ("air_ops", "Air Operations"),
    ],
    "checks": {
        SIGNALS["radar_coverage"]: (redlines.radar_coverage_min, "below"),
        SIGNALS["comms_availability"]: (redlines.comms_min, "below"),
        SIGNALS["cookoff_margin"]: (redlines.cookoff_margin_min, "below"),
        SIGNALS["fuel_level"]: (redlines.fuel_min, "below"),
        SIGNALS["nbc_reading"]: (redlines.nbc_max, "above"),
        SIGNALS["jamming_level"]: (redlines.jamming_max, "above"),
        SIGNALS["mission_readiness"]: (redlines.mission_readiness_min, "below"),
        SIGNALS["flight_hours"]: (redlines.flight_hours_min, "below"),
    },
}

SPECS = [SPEC, WARSHIP_SPEC]

__all__ = [
    "DefenceBasePhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
    "gas_turbine_brayton", "radar_propagation", "ship_stability",
    "nbc_contamination_spread", "structural_fatigue",
    "component_health", "predict", "build_defence_registry", "SPEC", "WARSHIP_SPEC", "SPECS",
]
