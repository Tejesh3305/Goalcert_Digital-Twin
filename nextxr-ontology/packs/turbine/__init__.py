"""turbine — gas-turbine digital-twin domain (physics, prediction, behaviours)."""
from .physics import (
    TurbinePhysics, SIGNALS, UNITS, redlines, FAULTS,
)
from .predict import component_health, predict
from .behaviors import build_turbine_registry

# The self-describing domain spec the machine-twin runtime consumes. Every
# machine domain exposes one of these; the runtime is otherwise domain-agnostic.
SPEC = {
    "key": "turbine-engine",
    "label": "Gas Turbine Engine",
    "class_iri": "https://ontology.nextxr.io/v3/turbine#GasTurbine",
    "control": "throttle",
    "physics": TurbinePhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_turbine_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["egt"]: ("Exhaust Gas Temp", "°C"),
        SIGNALS["n1"]: ("Shaft Speed N1", "RPM"),
        SIGNALS["n2"]: ("Shaft Speed N2", "RPM"),
        SIGNALS["fuel"]: ("Fuel Flow", "kg/h"),
        SIGNALS["vib"]: ("Vibration", "g"),
        SIGNALS["epr"]: ("EPR", ""),
        SIGNALS["oil_temp"]: ("Oil Temperature", "°C"),
        SIGNALS["oil_press"]: ("Oil Pressure", "PSI"),
    },
    "subsystems": [
        ("compressor", "Compressor"), ("combustor", "Combustor"),
        ("turbine", "Turbine"), ("bearings", "Rotor & Bearings"),
        ("lubrication", "Lubrication System"),
    ],
    # sensor status thresholds for the diagnostics surface: signal -> (limit, dir)
    "checks": {
        SIGNALS["egt"]: (redlines.egt, "above"),
        SIGNALS["vib"]: (redlines.vib, "above"),
        SIGNALS["oil_temp"]: (redlines.oil_temp, "above"),
        SIGNALS["oil_press"]: (redlines.oil_press_min, "below"),
    },
}

__all__ = [
    "TurbinePhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
    "component_health", "predict", "build_turbine_registry", "SPEC",
]
