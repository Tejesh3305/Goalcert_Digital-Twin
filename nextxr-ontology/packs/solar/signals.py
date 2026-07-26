"""
signals.py — the canonical signal catalogue for the solar PV twin.

ONE definition of every measurable quantity in the specification, so the Modbus
register map, the OPC-UA node map, the MQTT/Sparkplug topic map, the physics
model, the behaviour rules, the ontology TTL and the UI all name the same thing.
Without this the same measurement acquires four spellings — `dc_voltage`,
`DCVolt`, `Vdc`, `solar:dcVoltage` — and every join between layers becomes a
lookup table someone has to maintain by hand.

The naming convention follows the platform's other packs: `solar:<camelCase>`,
resolvable against `packs/solar/solar-classes.ttl`, where each one is declared as
a `sosa:ObservableProperty` with a QUDT unit.

COVERAGE MAP TO THE SPECIFICATION
---------------------------------
    §2.1 Smart Commercial Inverters ....... INVERTER_SIGNALS
    §2.1 String Combiner Monitoring ....... STRING_SIGNALS
    §2.1 Revenue-Grade Smart Meters ....... METER_SIGNALS
    §2.1 Split-Core CT Clamps ............. LOAD_SIGNALS
    §2.2 Pyranometer ...................... WEATHER_SIGNALS (ghi, poa)
    §2.2 Back-of-Module RTD (PT100) ....... WEATHER_SIGNALS (moduleTemp)
    §2.2 Ambient Temp & Wind Speed ........ WEATHER_SIGNALS
    §4.2 Residual analytics ............... DERIVED_SIGNALS

WHY DERIVED SIGNALS LIVE IN THE SAME NAMESPACE
----------------------------------------------
`solar:deltaP`, `solar:performanceRatio` and `solar:rShuntRef` are COMPUTED, not
measured. They are nonetheless first-class signals with units, written to the
historian alongside the measurements, because every one of the §4.2 maintenance
triggers is a condition on a derived quantity sustained over time — and a rule
cannot watch a trend that was never stored. Keeping them in one namespace also
means a chart, an alert threshold and a rollup treat measured and modelled series
identically, which is what makes "actual vs modelled" a single query.

They carry `quality` like any other signal: a residual computed from a modelled
baseline that could not be evaluated (no irradiance reading, solver did not
converge) is written as BAD rather than as zero. A zero residual means "performing
exactly to model", which is the opposite of "unknown", and conflating the two
would make a blind twin look like a perfect one.
"""

from __future__ import annotations

NS = "solar"


def _s(name: str) -> str:
    return f"{NS}:{name}"


# ── §2.1 Smart Commercial Inverters (Modbus TCP / RS-485 RTU) ───────────────
INVERTER_SIGNALS = {
    # DC side — what the array delivers into the MPP tracker
    "dc_voltage":      _s("dcVoltage"),
    "dc_current":      _s("dcCurrent"),
    "dc_power":        _s("dcPower"),
    # AC side — three-phase commercial inverter
    "ac_voltage_l1":   _s("acVoltageL1"),
    "ac_voltage_l2":   _s("acVoltageL2"),
    "ac_voltage_l3":   _s("acVoltageL3"),
    "ac_current_l1":   _s("acCurrentL1"),
    "ac_current_l2":   _s("acCurrentL2"),
    "ac_current_l3":   _s("acCurrentL3"),
    "ac_power":        _s("acPower"),
    "reactive_power":  _s("reactivePower"),
    "power_factor":    _s("powerFactor"),
    "frequency":       _s("frequency"),
    # Thermal + condition ("component thermal status" in §2.1)
    "heatsink_temp":   _s("heatsinkTemp"),
    "internal_temp":   _s("internalTemp"),
    "efficiency":      _s("inverterEfficiency"),
    # Energy counters and state
    "daily_yield":     _s("dailyYield"),
    "total_yield":     _s("totalYield"),
    "operating_state": _s("operatingState"),
    "fault_code":      _s("faultCode"),
    "mppt_count":      _s("mpptCount"),
    "isolation_res":   _s("isolationResistance"),
}

# ── §2.1 String Combiner Monitoring Units (Hall-effect / shunt) ─────────────
#
# Per-string current is the single most diagnostically valuable measurement in the
# whole array: §4.2's soiling and shading signatures are both distinguished by how
# current behaves ACROSS strings, which aggregated inverter DC current cannot see.
STRING_SIGNALS = {
    "string_current":  _s("stringCurrent"),
    "string_voltage":  _s("stringVoltage"),
    "string_power":    _s("stringPower"),
    "combiner_temp":   _s("combinerTemp"),
    "fuse_status":     _s("fuseStatus"),
    "spd_status":      _s("surgeProtectionStatus"),
}

# ── §2.1 Bidirectional Revenue-Grade Smart Meters (at the MDB) ──────────────
#
# Signed conventions are fixed HERE and nowhere else, because a sign error in a
# bidirectional meter silently inverts import and export, which corrupts every
# financial figure the twin reports. See `SIGN_CONVENTIONS` below.
METER_SIGNALS = {
    "active_power":       _s("meterActivePower"),
    "reactive_power":     _s("meterReactivePower"),
    "apparent_power":     _s("meterApparentPower"),
    "power_factor":       _s("meterPowerFactor"),
    "voltage":            _s("meterVoltage"),
    "current":            _s("meterCurrent"),
    "frequency":          _s("meterFrequency"),
    "energy_import":      _s("energyImport"),
    "energy_export":      _s("energyExport"),
    "net_power":          _s("netGridPower"),
    "facility_load":      _s("facilityLoad"),
    "self_consumption":   _s("selfConsumption"),
    "grid_dependency":    _s("gridDependency"),
}

# ── §2.1 Split-Core CT Clamps (sub-distribution subsystem loads) ────────────
#
# §2.1 names HVAC, lighting and server rooms explicitly. The signal is generic and
# the SUBSYSTEM is the asset id, so adding a fourth circuit needs no code — that is
# the point of "deep demand-side correlation" being an asset-hierarchy question
# rather than a schema question.
LOAD_SIGNALS = {
    "circuit_power":   _s("circuitPower"),
    "circuit_current": _s("circuitCurrent"),
    "circuit_energy":  _s("circuitEnergy"),
}

# ── §2.2 Micro-Climate & Environmental Sensors ──────────────────────────────
WEATHER_SIGNALS = {
    "ghi":              _s("ghi"),              # thermopile pyranometer, horizontal
    "poa_irradiance":   _s("poaIrradiance"),    # plane-of-array (tilt + azimuth)
    "module_temp":      _s("moduleTemp"),       # PT100/RTD bonded to rear surface
    "cell_temp":        _s("cellTemp"),         # derived from moduleTemp + POA
    "ambient_temp":     _s("ambientTemp"),
    "wind_speed":       _s("windSpeed"),
    "wind_direction":   _s("windDirection"),
    "humidity":         _s("humidity"),
    "soiling_ratio":    _s("soilingRatio"),
}

# ── §4 Derived / modelled quantities ────────────────────────────────────────
DERIVED_SIGNALS = {
    "modelled_dc_power":  _s("modelledDcPower"),
    "modelled_ac_power":  _s("modelledAcPower"),
    "delta_p":            _s("deltaP"),            # P_actual - P_modelled  [W]
    "delta_p_pct":        _s("deltaPPercent"),     # ΔP as % of modelled
    "performance_ratio":  _s("performanceRatio"),
    "specific_yield":     _s("specificYield"),     # kWh/kWp
    "r_shunt_ref":        _s("rShuntRef"),         # irradiance-normalised R_sh
    "fill_factor":        _s("fillFactor"),
    "current_imbalance":  _s("currentImbalance"),  # spread across strings
    "expected_isc":       _s("expectedIsc"),
    "expected_voc":       _s("expectedVoc"),
}

# Everything, flattened. Kept as one dict because the connector layer validates
# every mapped point against it — an unknown signal is a configuration error we
# want caught when the point map is saved, not when the first sample arrives.
SIGNALS: dict[str, str] = {
    **{f"inv_{k}": v for k, v in INVERTER_SIGNALS.items()},
    **{f"str_{k}": v for k, v in STRING_SIGNALS.items()},
    **{f"mtr_{k}": v for k, v in METER_SIGNALS.items()},
    **{f"load_{k}": v for k, v in LOAD_SIGNALS.items()},
    **{f"env_{k}": v for k, v in WEATHER_SIGNALS.items()},
    **{f"drv_{k}": v for k, v in DERIVED_SIGNALS.items()},
}

ALL_SIGNAL_IRIS: frozenset[str] = frozenset(SIGNALS.values())


# ── Units (QUDT vocabulary, matching the platform's unit convention) ────────
UNITS: dict[str, str] = {
    # Inverter
    INVERTER_SIGNALS["dc_voltage"]: "V",
    INVERTER_SIGNALS["dc_current"]: "A",
    INVERTER_SIGNALS["dc_power"]: "W",
    INVERTER_SIGNALS["ac_voltage_l1"]: "V",
    INVERTER_SIGNALS["ac_voltage_l2"]: "V",
    INVERTER_SIGNALS["ac_voltage_l3"]: "V",
    INVERTER_SIGNALS["ac_current_l1"]: "A",
    INVERTER_SIGNALS["ac_current_l2"]: "A",
    INVERTER_SIGNALS["ac_current_l3"]: "A",
    INVERTER_SIGNALS["ac_power"]: "W",
    INVERTER_SIGNALS["reactive_power"]: "V-A_Reactive",
    INVERTER_SIGNALS["power_factor"]: "",
    INVERTER_SIGNALS["frequency"]: "HZ",
    INVERTER_SIGNALS["heatsink_temp"]: "DEG_C",
    INVERTER_SIGNALS["internal_temp"]: "DEG_C",
    INVERTER_SIGNALS["efficiency"]: "PERCENT",
    INVERTER_SIGNALS["daily_yield"]: "KIL0WATT-HR",
    INVERTER_SIGNALS["total_yield"]: "KIL0WATT-HR",
    INVERTER_SIGNALS["operating_state"]: "",
    INVERTER_SIGNALS["fault_code"]: "",
    INVERTER_SIGNALS["mppt_count"]: "",
    INVERTER_SIGNALS["isolation_res"]: "OHM",
    # String
    STRING_SIGNALS["string_current"]: "A",
    STRING_SIGNALS["string_voltage"]: "V",
    STRING_SIGNALS["string_power"]: "W",
    STRING_SIGNALS["combiner_temp"]: "DEG_C",
    STRING_SIGNALS["fuse_status"]: "",
    STRING_SIGNALS["spd_status"]: "",
    # Meter
    METER_SIGNALS["active_power"]: "KILOW",
    METER_SIGNALS["reactive_power"]: "KILOV-A_Reactive",
    METER_SIGNALS["apparent_power"]: "KILOV-A",
    METER_SIGNALS["power_factor"]: "",
    METER_SIGNALS["voltage"]: "V",
    METER_SIGNALS["current"]: "A",
    METER_SIGNALS["frequency"]: "HZ",
    METER_SIGNALS["energy_import"]: "KIL0WATT-HR",
    METER_SIGNALS["energy_export"]: "KIL0WATT-HR",
    METER_SIGNALS["net_power"]: "KILOW",
    METER_SIGNALS["facility_load"]: "KILOW",
    METER_SIGNALS["self_consumption"]: "PERCENT",
    METER_SIGNALS["grid_dependency"]: "PERCENT",
    # Loads
    LOAD_SIGNALS["circuit_power"]: "KILOW",
    LOAD_SIGNALS["circuit_current"]: "A",
    LOAD_SIGNALS["circuit_energy"]: "KIL0WATT-HR",
    # Environment
    WEATHER_SIGNALS["ghi"]: "W-PER-M2",
    WEATHER_SIGNALS["poa_irradiance"]: "W-PER-M2",
    WEATHER_SIGNALS["module_temp"]: "DEG_C",
    WEATHER_SIGNALS["cell_temp"]: "DEG_C",
    WEATHER_SIGNALS["ambient_temp"]: "DEG_C",
    WEATHER_SIGNALS["wind_speed"]: "M-PER-SEC",
    WEATHER_SIGNALS["wind_direction"]: "DEG",
    WEATHER_SIGNALS["humidity"]: "PERCENT",
    WEATHER_SIGNALS["soiling_ratio"]: "",
    # Derived
    DERIVED_SIGNALS["modelled_dc_power"]: "W",
    DERIVED_SIGNALS["modelled_ac_power"]: "W",
    DERIVED_SIGNALS["delta_p"]: "W",
    DERIVED_SIGNALS["delta_p_pct"]: "PERCENT",
    DERIVED_SIGNALS["performance_ratio"]: "",
    DERIVED_SIGNALS["specific_yield"]: "KIL0WATT-HR-PER-KILOW",
    DERIVED_SIGNALS["r_shunt_ref"]: "OHM",
    DERIVED_SIGNALS["fill_factor"]: "",
    DERIVED_SIGNALS["current_imbalance"]: "PERCENT",
    DERIVED_SIGNALS["expected_isc"]: "A",
    DERIVED_SIGNALS["expected_voc"]: "V",
}


# ── Sign conventions ────────────────────────────────────────────────────────
#
# §2.1 requires the MDB meter to track "the precise DIRECTIONAL flow". Direction
# is a convention, not a measurement, and every meter vendor picks its own. Fixing
# it once here — and having the connector layer apply it during decode — is what
# stops a site commissioned by a different integrator from reporting inverted
# import/export, which would flip the sign of every financial number downstream
# while looking entirely plausible on a dashboard.
SIGN_CONVENTIONS = {
    METER_SIGNALS["active_power"]: (
        "POSITIVE = importing from the grid. NEGATIVE = exporting. A meter that "
        "reports the opposite must be corrected with `invert: true` in its point "
        "map, not by flipping the comparison downstream."),
    METER_SIGNALS["net_power"]: (
        "Same convention as meterActivePower: positive means net consumption."),
    METER_SIGNALS["reactive_power"]: (
        "POSITIVE = lagging (inductive) VAR drawn from the grid."),
    INVERTER_SIGNALS["ac_power"]: (
        "Always POSITIVE when generating. An inverter is a source; it does not "
        "use the meter's import-positive convention."),
}


# ── Operating-state enumeration ─────────────────────────────────────────────
#
# `solar:operatingState` is an integer on the wire (a Modbus register). The
# enumeration is here so the UI, the rules and the connector agree, and so an
# UNRECOGNISED code is preserved rather than mapped to "unknown" — a vendor-
# specific state is diagnostic information and discarding it loses the one clue
# that explains an outage.
OPERATING_STATES = {
    0: "off",
    1: "sleeping",          # irradiance below start-up threshold
    2: "starting",
    3: "mppt",              # normal generation, tracking maximum power
    4: "throttled",         # curtailed — grid operator or thermal derate
    5: "shutdown",
    6: "fault",
    7: "standby",
    8: "grid_monitoring",   # riding through a grid disturbance
}

# States in which zero output is EXPECTED. The §4.2 residual rules must not fire a
# soiling ticket because the inverter was asleep before dawn or curtailed by the
# grid operator — a false ticket costs a truck roll and teaches operators to
# distrust the alerts.
NON_GENERATING_STATES = frozenset({0, 1, 2, 5, 6, 7, 8})


def state_label(code) -> str:
    try:
        return OPERATING_STATES.get(int(code), f"vendor_{int(code)}")
    except (TypeError, ValueError):
        return "unknown"


def is_generating(code) -> bool:
    """True when the inverter should be producing, so a residual is meaningful."""
    try:
        return int(code) not in NON_GENERATING_STATES
    except (TypeError, ValueError):
        return False


def unit_for(signal: str) -> str:
    return UNITS.get(signal, "")


def is_known(signal: str) -> bool:
    return signal in ALL_SIGNAL_IRIS
