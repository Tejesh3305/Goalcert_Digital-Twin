"""railway — urban metro (MRT) digital-twin domain.

Exposes two self-describing machine-twin SPECs the runtime can twin:

  * SPEC          — "railway-metro": the whole network (lines, stations,
                    permanent way, traction power, signalling, station services)
                    with a live map + per-station KPIs.
  * TRAINSET_SPEC — "railway-trainset": one rolling-stock train set at the
                    vehicle level (traction, running gear, braking, doors, aux).

Both are collected in SPECS so the runtime can register every railway domain
from one module import.
"""
from .behaviors import build_railway_registry
from .physics import (
    FAULTS,
    SIGNALS,
    UNITS,
    RailwayPhysics,
    escalator_motor_model,
    passenger_los_model,
    rail_thermal_expansion,
    redlines,
    station_hvac_load,
    traction_power_flow,
    train_dynamics,
    wheel_rail_contact,
)
from .predict import component_health, predict
from .trainset import SPEC as TRAINSET_SPEC

SPEC = {
    "key": "railway-metro",
    "label": "Metro Rail Network",
    "class_iri": "https://ontology.nextxr.io/v3/railway#RailNetwork",
    "control": "service_level",
    "physics": RailwayPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_railway_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["otp"]: ("On-Time Performance", "%"),
        SIGNALS["headway"]: ("Headway Adherence", "%"),
        SIGNALS["avg_speed"]: ("Network Speed", "km/h"),
        SIGNALS["train_avail"]: ("Train Availability", "%"),
        SIGNALS["in_service"]: ("Trains In Service", ""),
        SIGNALS["load_factor"]: ("Passenger Load Factor", "%"),
        SIGNALS["dwell"]: ("Avg Dwell Time", "s"),
        SIGNALS["delay"]: ("Network Delay", "min"),
        SIGNALS["incidents"]: ("Active Incidents", ""),
        SIGNALS["traction_power"]: ("Traction Power", "MW"),
        SIGNALS["regen"]: ("Regen Share", "%"),
        SIGNALS["third_rail_v"]: ("Third-Rail Voltage", "V"),
        SIGNALS["sub_load"]: ("Substation Load", "%"),
        SIGNALS["traction_temp"]: ("Traction Motor Temp", "°C"),
        SIGNALS["rail_temp"]: ("Rail Temperature", "°C"),
        SIGNALS["rail_stress"]: ("CWR Rail Stress", "MPa"),
        SIGNALS["wheel_qp"]: ("Derailment Q/P", ""),
        SIGNALS["bogie_vib"]: ("Bogie Vibration", "mm/s"),
        SIGNALS["track_circuit_faults"]: ("Track-Circuit Faults", ""),
        SIGNALS["signal_faults"]: ("Signal Faults", ""),
        SIGNALS["psd_faults"]: ("PSD Faults", ""),
        SIGNALS["escalator_temp"]: ("Escalator Motor Temp", "°C"),
        SIGNALS["hvac_load"]: ("Station HVAC Load", "%"),
        SIGNALS["hvac_dp"]: ("Platform HVAC ΔP", "Pa"),
        SIGNALS["station_los"]: ("Platform LOS", "LOS"),
    },
    "subsystems": [
        ("rolling_stock", "Rolling Stock"),
        ("traction_power", "Traction Power"),
        ("permanent_way", "Permanent Way"),
        ("signalling", "Signalling & Control"),
        ("stations", "Stations & Services"),
        ("operations", "Operations & Service"),
    ],
    "checks": {
        SIGNALS["otp"]: (redlines.otp_min, "below"),
        SIGNALS["headway"]: (redlines.headway_min, "below"),
        SIGNALS["train_avail"]: (redlines.train_avail_min, "below"),
        SIGNALS["third_rail_v"]: (redlines.third_rail_max, "above"),
        SIGNALS["sub_load"]: (redlines.sub_load_max, "above"),
        SIGNALS["traction_temp"]: (redlines.traction_temp_max, "above"),
        SIGNALS["rail_temp"]: (redlines.rail_temp_max, "above"),
        SIGNALS["rail_stress"]: (redlines.rail_stress_max, "above"),
        SIGNALS["wheel_qp"]: (redlines.wheel_qp_max, "above"),
        SIGNALS["bogie_vib"]: (redlines.bogie_vib_max, "above"),
        SIGNALS["hvac_dp"]: (redlines.hvac_dp_min, "below"),
        SIGNALS["station_los"]: (redlines.station_los_max, "above"),
        SIGNALS["delay"]: (redlines.delay_max, "above"),
    },
}

# Every railway machine domain, for one-shot registration by the runtime.
SPECS = [SPEC, TRAINSET_SPEC]

__all__ = [
    "RailwayPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
    "traction_power_flow", "train_dynamics", "station_hvac_load",
    "escalator_motor_model", "rail_thermal_expansion", "wheel_rail_contact",
    "passenger_los_model", "component_health", "predict",
    "build_railway_registry", "SPEC", "TRAINSET_SPEC", "SPECS",
]
