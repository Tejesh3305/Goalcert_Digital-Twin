"""edm — wire-EDM machine digital-twin domain (physics, prediction, behaviours)."""
from .physics import EDMPhysics, SIGNALS, UNITS, redlines, FAULTS
from .predict import component_health, predict
from .behaviors import build_edm_registry

SPEC = {
    "key": "edm-machine",
    "label": "Wire EDM Machine",
    "class_iri": "https://ontology.nextxr.io/v3/edm#WireEDM",
    "control": "intensity",
    "physics": EDMPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_edm_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["gap_v"]: ("Gap Voltage", "V"),
        SIGNALS["peak_i"]: ("Peak Current", "A"),
        SIGNALS["ton"]: ("Pulse On-Time", "µs"),
        SIGNALS["toff"]: ("Pulse Off-Time", "µs"),
        SIGNALS["spark_freq"]: ("Spark Frequency", "kHz"),
        SIGNALS["energy"]: ("Discharge Energy", "mJ"),
        SIGNALS["wire_tension"]: ("Wire Tension", "N"),
        SIGNALS["wire_feed"]: ("Wire Feed Rate", "m/min"),
        SIGNALS["wire_wear"]: ("Wire Wear", "%"),
        SIGNALS["cut_speed"]: ("Cutting Speed", "mm²/min"),
        SIGNALS["die_flow"]: ("Dielectric Flow", "L/min"),
        SIGNALS["die_press"]: ("Dielectric Pressure", "bar"),
        SIGNALS["die_temp"]: ("Dielectric Temperature", "°C"),
        SIGNALS["die_cond"]: ("Dielectric Conductivity", "µS/cm"),
        SIGNALS["short_rate"]: ("Short-Circuit Rate", "%"),
        SIGNALS["spark_gap"]: ("Spark Gap", "µm"),
        SIGNALS["ra"]: ("Surface Finish Ra", "µm"),
        SIGNALS["break_risk"]: ("Wire-Break Risk", "%"),
    },
    "subsystems": [
        ("generator", "Discharge Generator"),
        ("dielectric", "Dielectric & Flushing"),
        ("wire_system", "Wire Transport System"),
        ("guides_axes", "Guides & Axes"),
    ],
    "checks": {
        SIGNALS["short_rate"]: (redlines.short_rate, "above"),
        SIGNALS["break_risk"]: (redlines.break_risk, "above"),
        SIGNALS["die_temp"]: (redlines.die_temp, "above"),
        SIGNALS["die_cond"]: (redlines.die_cond, "above"),
        SIGNALS["die_press"]: (redlines.die_press_min, "below"),
        SIGNALS["wire_tension"]: (redlines.wire_tension_min, "below"),
        SIGNALS["gap_v"]: (redlines.gap_v_min, "below"),
    },
}

__all__ = ["EDMPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
           "component_health", "predict", "build_edm_registry", "SPEC"]
