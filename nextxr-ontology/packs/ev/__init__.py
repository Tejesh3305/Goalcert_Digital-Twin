"""ev — EV / e-mobility digital-twin domain (machine-twin runtime).

Exposes two SPECs:
  * SPEC         — "ev-charging-network": the charging network (stations, chargers,
                   grid, transformer, solar, V2G) with a geo map, grid load curve
                   and V2G trading view.
  * BATTERY_SPEC — "ev-battery-pack": one battery pack at cell level (ECM + thermal
                   + degradation) with a cell heatmap.
"""
from .physics import (
    EVChargingNetworkPhysics, SIGNALS, UNITS, redlines, FAULTS,
    battery_ecm, battery_thermal, battery_degradation, charging_dynamics,
    grid_transformer_aging, ev_range_prediction, pmsm_motor, soc_ocv,
)
from .predict import component_health, predict
from .behaviors import build_ev_registry
from .battery import SPEC as BATTERY_SPEC

SPEC = {
    "key": "ev-charging-network",
    "label": "EV Charging Network",
    "class_iri": "https://ontology.nextxr.io/v3/ev#ChargingNetwork",
    "control": "demand_level",
    "physics": EVChargingNetworkPhysics,
    "predict": predict,
    "component_health": component_health,
    "build_registry": build_ev_registry,
    "signals": SIGNALS,
    "units": UNITS,
    "faults": FAULTS,
    "sensors": {
        SIGNALS["network_load"]: ("Charging Network Load", "kW"),
        SIGNALS["transformer_load"]: ("Transformer Load", "%"),
        SIGNALS["active_sessions"]: ("Active Sessions", ""),
        SIGNALS["available_chargers"]: ("Available Chargers", "%"),
        SIGNALS["avg_charge_power"]: ("Avg Charge Power", "kW"),
        SIGNALS["grid_voltage"]: ("Grid Voltage", "%"),
        SIGNALS["grid_frequency"]: ("Grid Frequency", "Hz"),
        SIGNALS["transformer_hotspot"]: ("Transformer Hot-Spot", "°C"),
        SIGNALS["transformer_aging"]: ("Transformer Aging Rate", "x"),
        SIGNALS["connector_temp"]: ("Connector Temp", "°C"),
        SIGNALS["charger_faults"]: ("Charger Faults", ""),
        SIGNALS["energy_delivered"]: ("Energy Delivered", "kWh"),
        SIGNALS["solar_output"]: ("Solar Output", "kW"),
        SIGNALS["solar_irradiance"]: ("Solar Irradiance", "W/m²"),
        SIGNALS["self_consumption"]: ("Solar Self-Consumption", "%"),
        SIGNALS["v2g_export"]: ("V2G Export", "kW"),
        SIGNALS["spot_price"]: ("Spot Price", "$/MWh"),
        SIGNALS["v2g_revenue"]: ("V2G Revenue", "$/h"),
        SIGNALS["fleet_soc"]: ("Fleet Avg SoC", "%"),
        SIGNALS["fleet_soh"]: ("Fleet Avg SoH", "%"),
    },
    "subsystems": [
        ("grid", "Grid Connection"),
        ("transformer", "Transformer"),
        ("chargers", "Chargers"),
        ("power_quality", "Power Quality"),
    ],
    "checks": {
        SIGNALS["transformer_load"]: (redlines.transformer_load_max, "above"),
        SIGNALS["transformer_hotspot"]: (redlines.transformer_hotspot_max, "above"),
        SIGNALS["transformer_aging"]: (redlines.transformer_aging_max, "above"),
        SIGNALS["grid_voltage"]: (redlines.grid_voltage_min, "below"),
        SIGNALS["connector_temp"]: (redlines.connector_temp_max, "above"),
        SIGNALS["available_chargers"]: (redlines.available_chargers_min, "below"),
        SIGNALS["charger_faults"]: (redlines.charger_faults_max, "above"),
    },
}

SPECS = [SPEC, BATTERY_SPEC]

__all__ = [
    "EVChargingNetworkPhysics", "SIGNALS", "UNITS", "redlines", "FAULTS",
    "battery_ecm", "battery_thermal", "battery_degradation", "charging_dynamics",
    "grid_transformer_aging", "ev_range_prediction", "pmsm_motor", "soc_ocv",
    "component_health", "predict", "build_ev_registry", "SPEC", "BATTERY_SPEC", "SPECS",
]
