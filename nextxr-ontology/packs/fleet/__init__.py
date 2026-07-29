"""fleet — tram / light-rail fleet-network digital-twin domain.

A whole-network twin (rolling stock, traction power, track & points,
signalling, operations) with live per-vehicle positions for the network map.
Ported from the Collins demo's `fleet/` module; built to the same self-
describing SPEC shape as the other machine-twin packs so the runtime registers
it unchanged.

  * network.py   — Melbourne tram dataset + the generic normaliser
  * physics.py   — service/power/track network physics + stateful forward sim
                   with live vehicle positions (network_state)
  * predict.py   — per-subsystem health + forward projection + RUL
  * behaviors.py — the 3-tier findings registry
  * scenario.py  — the injectable fault catalogue + demo scenario presets
"""
from .behaviors import build_fleet_registry
from .physics import (
    SIGNALS,
    UNITS,
    DesignPoint,
    FleetPhysics,
    FleetState,
    Redlines,
    redlines,
)
from .predict import component_health, predict
from .scenario import FAULTS as FAULT_CATALOGUE
from .scenario import SCENARIOS

# The runtime consumes a list of fault-id strings (mirrors the other packs).
FAULTS = list(FleetPhysics.FAULTS)

SPEC = {
    "key": "tram-network",
    "label": "Tram / Light-Rail Network",
    "class_iri": "https://ontology.nextxr.io/v3/fleet#TramNetwork",
    "control": "service_level",
    "physics": FleetPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_fleet_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["otp"]: ("On-Time Performance", "%"),
        SIGNALS["headway"]: ("Headway Adherence", "%"),
        SIGNALS["avg_speed"]: ("Network Speed", "km/h"),
        SIGNALS["fleet_avail"]: ("Fleet Availability", "%"),
        SIGNALS["in_service"]: ("Trams In Service", ""),
        SIGNALS["pax_load"]: ("Passenger Load", "%"),
        SIGNALS["dwell"]: ("Avg Dwell Time", "s"),
        SIGNALS["energy"]: ("Traction Power", "MW"),
        SIGNALS["regen"]: ("Regen Share", "%"),
        SIGNALS["ohl_v"]: ("Overhead Voltage", "V"),
        SIGNALS["sub_load"]: ("Substation Load", "%"),
        SIGNALS["track_temp"]: ("Rail Temperature", "°C"),
        SIGNALS["switch_faults"]: ("Switch Faults", ""),
        SIGNALS["signal_faults"]: ("Signal Faults", ""),
        SIGNALS["door_faults"]: ("Door Faults", ""),
        SIGNALS["brake_wear"]: ("Brake Wear", "%"),
        SIGNALS["panto_wear"]: ("Pantograph Wear", "%"),
        SIGNALS["traction_temp"]: ("Traction Motor Temp", "°C"),
        SIGNALS["vib"]: ("Bogie Vibration", "g"),
        SIGNALS["delay"]: ("Network Delay", "min"),
        SIGNALS["incidents"]: ("Active Incidents", ""),
        SIGNALS["hvac_load"]: ("HVAC Load", "%"),
    },
    "subsystems": [
        ("rolling_stock", "Rolling Stock"),
        ("power", "Traction Power"),
        ("track", "Track & Points"),
        ("signalling", "Signalling"),
        ("operations", "Operations & Service"),
    ],
    "checks": {
        SIGNALS["otp"]: (redlines.otp_min, "below"),
        SIGNALS["headway"]: (redlines.headway_min, "below"),
        SIGNALS["fleet_avail"]: (redlines.fleet_avail_min, "below"),
        SIGNALS["ohl_v"]: (redlines.ohl_v_min, "below"),
        SIGNALS["sub_load"]: (redlines.sub_load_max, "above"),
        SIGNALS["track_temp"]: (redlines.track_temp_max, "above"),
        SIGNALS["vib"]: (redlines.vib_max, "above"),
        SIGNALS["brake_wear"]: (redlines.brake_wear_max, "above"),
        SIGNALS["panto_wear"]: (redlines.panto_wear_max, "above"),
        SIGNALS["door_faults"]: (redlines.door_faults_max, "above"),
        SIGNALS["signal_faults"]: (redlines.signal_faults_max, "above"),
        SIGNALS["traction_temp"]: (redlines.traction_temp_max, "above"),
        SIGNALS["delay"]: (redlines.delay_max, "above"),
    },
}

SPECS = [SPEC]

__all__ = [
    "FleetPhysics", "FleetState", "DesignPoint", "Redlines",
    "SIGNALS", "UNITS", "redlines", "FAULTS", "FAULT_CATALOGUE", "SCENARIOS",
    "component_health", "predict", "build_fleet_registry", "SPEC", "SPECS",
]
