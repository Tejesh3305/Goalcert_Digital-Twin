"""
solar — enterprise photovoltaic array digital-twin domain.

Implements the Enterprise Solar Digital Twin specification:

    §2.1 power/telemetry hardware ..... connectors/profiles.py (Modbus/OPC-UA/MQTT)
    §2.2 micro-climate sensors ........ connectors/profiles.weather_station
    §2.3 edge gateway ................. connectors/mqtt.py (TLS 1.3, Sparkplug B)
    §3   asset hierarchy .............. solar-classes.ttl + asset templating
    §4.1 De Soto 5-parameter model .... desoto.py
    §4.2 residual analytics ........... behaviors.py (the three signatures)
    §5   operating modes .............. server/solar_routes.py + the SPEC below

    signals.py    the canonical signal catalogue — one name per measurement
    desoto.py     the single-diode model: the clean-array baseline
    physics.py    the array forward model and its degradation accumulators
    behaviors.py  §4.2's maintenance triggers as live rules
    predict.py    subsystem health and the degradation forecast

WHAT MAKES THIS A TWIN RATHER THAN A DASHBOARD
----------------------------------------------
A PV array's output is dictated by the weather, so its raw output carries almost no
information about its condition: 400 kW from a 500 kW array is excellent at 8 a.m.
and alarming at noon. The De Soto model supplies the missing half — what this array,
at this irradiance and this cell temperature, SHOULD be producing — and every
diagnosis in §4.2 is a statement about the residual between the two.

That is also why §2.2's pyranometer and RTDs are not optional extras. Without
measured irradiance and cell temperature there is no baseline, and every
"underperformance" alert would just be tracking the sky.
"""

from .behaviors import build_solar_registry
from .desoto import (
    G_REF,
    REFERENCE_MODULE,
    REFERENCE_MODULE_PMP_STC,
    T_REF_C,
    MaxPowerPoint,
    ModuleParams,
    OperatingParams,
    cell_temp_from_module,
    estimate_rsh_ref,
    iv_curve,
    max_power_point,
    module_temp_faiman,
    open_circuit_voltage,
    short_circuit_current,
    solve_current,
    translate,
)
from .physics import (
    FAULTS,
    SIGNALS,
    SolarPhysics,
    SolarState,
    StringState,
    build_strings,
    inverter_efficiency,
    redlines,
)
from .predict import component_health, predict
from .signals import (
    DERIVED_SIGNALS,
    INVERTER_SIGNALS,
    LOAD_SIGNALS,
    METER_SIGNALS,
    OPERATING_STATES,
    SIGN_CONVENTIONS,
    STRING_SIGNALS,
    UNITS,
    WEATHER_SIGNALS,
    is_generating,
    state_label,
    unit_for,
)

# The self-describing domain spec the machine-twin runtime consumes.
SPEC = {
    "key": "solar-pv-array",
    "label": "Enterprise Solar PV Array",
    "class_iri": "https://ontology.nextxr.io/v3/solar#PVArray",
    # The controllable input. Unlike a turbine's throttle this is a CURTAILMENT
    # setpoint: a grid operator can ask a plant to produce less, never more, because
    # the upper bound is the sun.
    "control": "curtailment",
    "physics": SolarPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_solar_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["poa"]: ("Plane-of-Array Irradiance", "W/m²"),
        SIGNALS["ghi"]: ("Global Horizontal Irradiance", "W/m²"),
        SIGNALS["module_temp"]: ("Back-of-Module Temperature", "°C"),
        SIGNALS["cell_temp"]: ("Cell Temperature", "°C"),
        SIGNALS["ambient_temp"]: ("Ambient Temperature", "°C"),
        SIGNALS["wind"]: ("Wind Speed", "m/s"),
        SIGNALS["dc_voltage"]: ("DC Bus Voltage", "V"),
        SIGNALS["dc_current"]: ("DC Current", "A"),
        SIGNALS["dc_power"]: ("DC Power", "W"),
        SIGNALS["ac_power"]: ("AC Power", "W"),
        SIGNALS["efficiency"]: ("Inverter Efficiency", "%"),
        SIGNALS["heatsink_temp"]: ("Heat-sink Temperature", "°C"),
        SIGNALS["modelled_dc"]: ("Modelled DC Power (De Soto)", "W"),
        SIGNALS["delta_p_pct"]: ("ΔP vs Model", "%"),
        SIGNALS["pr"]: ("Performance Ratio", ""),
        SIGNALS["rsh"]: ("Shunt Resistance (normalised)", "Ω"),
        SIGNALS["ff"]: ("Fill Factor", ""),
        SIGNALS["imbalance"]: ("String Current Imbalance", "%"),
    },
    # Each maps to a DIFFERENT intervention — wash, inspect, ventilate, service,
    # calibrate. A breakdown whose entries all lead to the same action is decoration.
    "subsystems": [
        ("modules", "PV Modules (soiling)"),
        ("strings", "Strings & Combiners"),
        ("cells", "Cell Degradation (PID/FF)"),
        ("inverter", "Inverter"),
        ("sensors", "Irradiance & Temperature Sensors"),
    ],
    # Diagnostics thresholds: signal -> (limit, direction).
    "checks": {
        SIGNALS["dc_voltage"]: (redlines.dc_voltage_max, "above"),
        SIGNALS["heatsink_temp"]: (redlines.heatsink_temp, "above"),
        SIGNALS["cell_temp"]: (redlines.cell_temp, "above"),
        SIGNALS["delta_p_pct"]: (redlines.delta_p_pct, "below"),
        SIGNALS["pr"]: (redlines.performance_ratio_min, "below"),
        SIGNALS["imbalance"]: (redlines.current_imbalance_pct, "above"),
        SIGNALS["ff"]: (redlines.fill_factor_min, "below"),
    },
    # §4.2's table, machine-readable, so the copilot's work-order and procurement
    # agents can act on a finding without re-deriving what it means.
    "maintenance_triggers": [
        {"signature": "Symmetric Output Drop",
         "behavior_id": "solar.soiling_symmetric_drop",
         "condition": "ΔP < -12% across zone; V_dc normal; I_dc uniformly suppressed",
         "diagnosis": "Soiling / dust build-up",
         "action": "Low-priority cleaning ticket",
         "priority": "low"},
        {"signature": "String Voltage Collapse",
         "behavior_id": "solar.string_voltage_collapse",
         "condition": "Step drop in V_dc on a single string; I_dc constant",
         "diagnosis": "Bypass diode activation / heavy local shading",
         "action": "High-priority structural inspection; isolate the string in red",
         "priority": "high"},
        {"signature": "Shunt Resistance Decay",
         "behavior_id": "solar.shunt_resistance_decay",
         "condition": "Progressive decline in R_sh over 90 days",
         "diagnosis": "Potential-induced degradation (PID) or delamination",
         "action": "Schedule preventative engineering diagnostics next quarter",
         "priority": "scheduled"},
    ],
    # §5's three operating modes, declared so the frontend can enumerate them.
    "operation_modes": [
        {"key": "command_center", "label": "Operations Command Center",
         "surface": "desktop/browser", "spec": "§5.1"},
        {"key": "field_service", "label": "Immersive Field Service (AR)",
         "surface": "mobile AR / smart glasses", "spec": "§5.2"},
        {"key": "training_lab", "label": "Multiplayer Interactive Training Lab",
         "surface": "VR headset / WebGL", "spec": "§5.3"},
    ],
}

SPECS = [SPEC]

__all__ = [
    "DERIVED_SIGNALS", "FAULTS", "G_REF", "INVERTER_SIGNALS", "LOAD_SIGNALS",
    "METER_SIGNALS", "MaxPowerPoint", "ModuleParams", "OPERATING_STATES",
    "OperatingParams", "REFERENCE_MODULE", "REFERENCE_MODULE_PMP_STC", "SIGNALS",
    "SIGN_CONVENTIONS", "SPEC", "SPECS", "STRING_SIGNALS", "SolarPhysics",
    "SolarState", "StringState", "T_REF_C", "UNITS", "WEATHER_SIGNALS",
    "build_solar_registry", "build_strings", "cell_temp_from_module",
    "component_health", "estimate_rsh_ref", "inverter_efficiency", "is_generating",
    "iv_curve", "max_power_point", "module_temp_faiman", "open_circuit_voltage",
    "predict", "redlines", "short_circuit_current", "solve_current", "state_label",
    "translate", "unit_for",
]
