"""
profiles.py — built-in device profiles: one per sensor class in the specification.

These are the "plugins for these sensors". Each is a `PointMap` covering one
hardware class from §2.1/§2.2, ready to bind to a real device by setting its
address and asset ids. Commissioning a site becomes: pick a profile, point it at
an IP, name the asset. No code.

    §2.1 Smart Commercial Inverter ..... sunspec_inverter_3ph()   (Modbus)
    §2.1 String Combiner ............... string_combiner()        (Modbus)
    §2.1 Revenue-Grade Meter ........... revenue_meter()          (Modbus)
    §2.1 CT Clamp sub-panel ............ ct_clamp_panel()         (Modbus)
    §2.2 Weather station ............... weather_station()        (Modbus)
    §2.3 Edge gateway JSON over MQTT ... mqtt_gateway()           (MQTT)
    §2.1 Inverter over OPC-UA .......... opcua_inverter()         (OPC-UA)

WHY SUNSPEC IS THE INVERTER DEFAULT
-----------------------------------
§2.1 requires inverters "specified with integrated industrial communications
cards supporting Modbus TCP or RS-485". In practice every commercial inverter that
ships such a card implements SunSpec (SunSpec Alliance Modbus specification) —
it is the actual interoperability standard, mandated by California Rule 21 and
IEEE 1547-2018 for grid-support functions. Writing the default profile against
SunSpec rather than one vendor's proprietary map means a site can mix SMA, Fronius,
SolarEdge and Huawei inverters behind one profile, which is what "enterprise
scale" actually looks like on a real roof.

THE ADDRESSING CAVEAT THAT MATTERS
----------------------------------
SunSpec point offsets are RELATIVE to the start of their model's data block, and
where that block sits differs per device: the register file begins with a "SunS"
marker, then a chain of models each carrying its own id and length, and a client is
expected to WALK that chain to find the one it wants. So the offsets below are
relative and `base_address` must be discovered, not guessed —
`modbus.discover_sunspec()` does the walk and returns the value.

Guessing it is the single most likely way to bring a site up with silently wrong
data: an offset error of a few registers still decodes into plausible-looking
numbers, because neighbouring SunSpec points are all small integers with scale
factors. `sunspec_inverter_3ph()` therefore takes `base_address` as a REQUIRED
argument rather than defaulting it.
"""

from __future__ import annotations

from .pointmap import (
    FC_READ_HOLDING, Point, PointMap, expand_asset_template,
)

# ── Signal IRIs, imported so a typo cannot diverge from the catalogue ────────
from packs.solar.signals import (
    INVERTER_SIGNALS as INV,
    LOAD_SIGNALS as LOAD,
    METER_SIGNALS as MTR,
    STRING_SIGNALS as STR,
    WEATHER_SIGNALS as ENV,
)


# ── §2.1 Smart Commercial Inverter — SunSpec Model 103 (3-phase, int+SF) ────
#
# Offsets are from the first DATA register of model 103 (i.e. after its 2-register
# ID/length header). Model 103 has length 50; the layout is fixed by the SunSpec
# specification and reproduced here in full so an operator can check it against a
# vendor's register document without leaving the file.
#
#   off  point     type      meaning
#     0  A         uint16    total AC current
#     1  AphA      uint16    phase A current
#     2  AphB      uint16
#     3  AphC      uint16
#     4  A_SF      sunssf    scale factor for the four above
#     5  PPVphAB   uint16    phase-to-phase voltage
#     6  PPVphBC   uint16
#     7  PPVphCA   uint16
#     8  PhVphA    uint16    phase-to-neutral voltage
#     9  PhVphB    uint16
#    10  PhVphC    uint16
#    11  V_SF      sunssf
#    12  W         int16     AC real power
#    13  W_SF      sunssf
#    14  Hz        uint16    line frequency
#    15  Hz_SF     sunssf
#    16  VA        int16     apparent power
#    17  VA_SF     sunssf
#    18  VAr       int16     reactive power
#    19  VAr_SF    sunssf
#    20  PF        int16     power factor
#    21  PF_SF     sunssf
#    22  WH        acc32     lifetime energy (2 registers)
#    24  WH_SF     sunssf
#    25  DCA       uint16    DC current
#    26  DCA_SF    sunssf
#    27  DCV       uint16    DC voltage
#    28  DCV_SF    sunssf
#    29  DCW       int16     DC power
#    30  DCW_SF    sunssf
#    31  TmpCab    int16     cabinet temperature
#    32  TmpSnk    int16     heat-sink temperature
#    33  TmpTrns   int16     transformer temperature
#    34  TmpOt     int16     other temperature
#    35  Tmp_SF    sunssf
#    36  St        enum16    operating state
#    37  StVnd     enum16    vendor operating state
#    38  Evt1      bits32    event flags (2 registers)
SUNSPEC_103_LENGTH = 50


def sunspec_inverter_3ph(asset_id: str, base_address: int, *,
                         unit_id: int = 1, name: str = "") -> PointMap:
    """SunSpec Model 103 three-phase inverter.

    `base_address` is REQUIRED and must come from `modbus.discover_sunspec()`.
    There is deliberately no default: an offset that is wrong by a few registers
    still decodes into plausible small integers, so a guessed base produces a
    device that looks like it is working and is not.
    """
    if base_address is None:
        raise ValueError(
            "base_address is required — walk the SunSpec model chain with "
            "modbus.discover_sunspec() rather than guessing it.")

    def p(offset: int, signal: str, dtype: str, sf: int | None, label: str,
          unit: str, **kw) -> Point:
        return Point(signal=signal, asset_id=asset_id, label=label, unit=unit,
                     address=offset, data_type=dtype, scale_register=sf,
                     function_code=FC_READ_HOLDING, word_order="big",
                     byte_order="big", **kw)

    points = [
        # AC current, per phase (§2.1 AC output)
        p(1, INV["ac_current_l1"], "uint16", 4, "AC Current L1", "A",
          min_value=0, max_value=5000, deadband=0.2),
        p(2, INV["ac_current_l2"], "uint16", 4, "AC Current L2", "A",
          min_value=0, max_value=5000, deadband=0.2),
        p(3, INV["ac_current_l3"], "uint16", 4, "AC Current L3", "A",
          min_value=0, max_value=5000, deadband=0.2),
        # AC voltage, phase-to-neutral
        p(8, INV["ac_voltage_l1"], "uint16", 11, "AC Voltage L1", "V",
          min_value=0, max_value=1000, deadband=1.0),
        p(9, INV["ac_voltage_l2"], "uint16", 11, "AC Voltage L2", "V",
          min_value=0, max_value=1000, deadband=1.0),
        p(10, INV["ac_voltage_l3"], "uint16", 11, "AC Voltage L3", "V",
          min_value=0, max_value=1000, deadband=1.0),
        # AC power. Signed: an inverter can draw a little at night.
        p(12, INV["ac_power"], "int16", 13, "AC Power", "W",
          min_value=-50_000, max_value=5_000_000, deadband=50.0),
        p(14, INV["frequency"], "uint16", 15, "Frequency", "HZ",
          min_value=40, max_value=70, deadband=0.02),
        p(18, INV["reactive_power"], "int16", 19, "Reactive Power",
          "V-A_Reactive", deadband=50.0),
        p(20, INV["power_factor"], "int16", 21, "Power Factor", "",
          min_value=-1.5, max_value=1.5, deadband=0.01),
        # Lifetime energy counter
        p(22, INV["total_yield"], "acc32", 24, "Total Yield", "KIL0WATT-HR",
          min_value=0, deadband=0.1),
        # DC side — the array's own output, and the input to the §4 residual
        p(25, INV["dc_current"], "uint16", 26, "DC Current", "A",
          min_value=0, max_value=5000, deadband=0.2),
        p(27, INV["dc_voltage"], "uint16", 28, "DC Voltage", "V",
          min_value=0, max_value=1500, deadband=1.0),
        p(29, INV["dc_power"], "int16", 30, "DC Power", "W",
          min_value=-50_000, max_value=5_000_000, deadband=50.0),
        # Thermal status (§2.1 "component thermal status")
        p(31, INV["internal_temp"], "int16", 35, "Cabinet Temperature", "DEG_C",
          min_value=-40, max_value=120, deadband=0.5),
        p(32, INV["heatsink_temp"], "int16", 35, "Heat-sink Temperature",
          "DEG_C", min_value=-40, max_value=150, deadband=0.5),
        # State + faults. deadband 0 with a short timeout: a state change is an
        # EVENT and must never be filtered out, unlike a slowly drifting analogue.
        p(36, INV["operating_state"], "uint16", None, "Operating State", "",
          deadband=0.0, deadband_timeout_s=60.0),
        p(37, INV["fault_code"], "uint16", None, "Vendor State", "",
          deadband=0.0, deadband_timeout_s=60.0),
    ]
    return PointMap(
        name=name or f"sunspec-103-{asset_id}",
        protocol="modbus_tcp",
        vendor="SunSpec", model="Model 103 (3-phase inverter, int+SF)",
        description="SunSpec-compliant three-phase PV inverter. base_address must "
                    "come from the model-chain walk, not a guess.",
        unit_id=unit_id, base_address=base_address, points=points,
    )


# ── §2.1 String Combiner Monitoring Unit ────────────────────────────────────


def string_combiner(asset_template: str, string_count: int, *,
                    base_address: int = 0, unit_id: int = 1,
                    current_register: int = 0, voltage_register: int | None = None,
                    temp_register: int | None = None,
                    scale: float = 0.01, zone: int = 1, inverter: int = 1,
                    name: str = "") -> PointMap:
    """Per-string current from a Hall-effect / shunt combiner box.

    THE MOST DIAGNOSTICALLY VALUABLE PROFILE HERE. §4.2's two current-based
    signatures can only be told apart across strings: soiling suppresses current
    UNIFORMLY across a zone, while shading or a bypass-diode event hits ONE string
    while its neighbours stay normal. Aggregated inverter DC current cannot make
    that distinction, so without per-string monitoring the twin can detect that
    something is wrong and not what.

    `asset_template` follows §3 Step 1's hierarchical tagging so each string's id
    matches its node in the 3-D scene tree:

        "Zone_{zone:02d}_Inverter_{inverter:02d}_String_{index:02d}"

    Registers are assumed CONTIGUOUS per string, one register each, which is how
    every combiner the author has seen lays them out. A device that interleaves
    current and voltage per string needs `stride` — add it when a real device
    demands it rather than speculatively.
    """
    if string_count < 1:
        raise ValueError("string_count must be at least 1")

    points: list[Point] = []
    for index in range(1, string_count + 1):
        asset = expand_asset_template(asset_template, index=index, zone=zone,
                                      inverter=inverter)
        points.append(Point(
            signal=STR["string_current"], asset_id=asset,
            label=f"String {index} Current", unit="A",
            address=current_register + (index - 1), data_type="uint16",
            scale=scale, min_value=0.0, max_value=50.0, deadband=0.05,
        ))
        if voltage_register is not None:
            points.append(Point(
                signal=STR["string_voltage"], asset_id=asset,
                label=f"String {index} Voltage", unit="V",
                address=voltage_register + (index - 1), data_type="uint16",
                scale=0.1, min_value=0.0, max_value=1500.0, deadband=1.0,
            ))
    if temp_register is not None:
        combiner_asset = expand_asset_template(
            asset_template, index=0, zone=zone, inverter=inverter)
        points.append(Point(
            signal=STR["combiner_temp"], asset_id=combiner_asset,
            label="Combiner Temperature", unit="DEG_C",
            address=temp_register, data_type="int16", scale=0.1,
            min_value=-40.0, max_value=120.0, deadband=0.5,
        ))
    return PointMap(
        name=name or f"string-combiner-{string_count}ch",
        protocol="modbus_tcp",
        vendor="generic", model=f"{string_count}-channel string monitor",
        description="Per-string current (§2.1). Required to distinguish uniform "
                    "soiling from single-string shading in §4.2.",
        unit_id=unit_id, base_address=base_address, points=points,
    )


# ── §2.1 Bidirectional Revenue-Grade Smart Meter (at the MDB) ───────────────


def revenue_meter(asset_id: str, *, base_address: int = 0, unit_id: int = 1,
                  invert_power: bool = False, name: str = "") -> PointMap:
    """Bidirectional revenue-grade meter at the Main Distribution Board.

    Every power point is SIGNED (int32), which is the whole point of a
    bidirectional meter and the most common configuration error on a solar site:
    decoded as unsigned, a 5 kW export reads as +4.29 GW rather than −5 kW.
    `test_pointmap.py` pins that case.

    `invert_power` exists because the import-positive convention is a choice, not a
    measurement, and vendors differ. The platform's convention is fixed in
    `packs/solar/signals.SIGN_CONVENTIONS` — POSITIVE MEANS IMPORTING — and a meter
    that reports the opposite is corrected HERE, at the edge, so nothing
    downstream has to know. Flipping the comparison downstream instead is how one
    site ends up with inverted financial reporting that nobody can find.

    Register layout follows the common IEC 61557-12 / Schneider PM5000-style block.
    Verify against the specific meter's document before commissioning; this is a
    starting point, not a universal truth.
    """
    def p(offset: int, signal: str, label: str, unit: str, dtype: str = "int32",
          scale: float = 1.0, invert: bool = False, **kw) -> Point:
        return Point(signal=signal, asset_id=asset_id, label=label, unit=unit,
                     address=offset, data_type=dtype, scale=scale,
                     word_order="big", byte_order="big", invert=invert, **kw)

    return PointMap(
        name=name or f"revenue-meter-{asset_id}",
        protocol="modbus_tcp",
        vendor="generic", model="Bidirectional revenue-grade meter",
        description="MDB meter (§2.1): directional real/reactive power, facility "
                    "consumption, net grid export. Positive = importing.",
        unit_id=unit_id, base_address=base_address,
        points=[
            p(0, MTR["active_power"], "Active Power", "KILOW",
              scale=0.001, invert=invert_power,
              min_value=-100_000, max_value=100_000, deadband=0.05),
            p(2, MTR["reactive_power"], "Reactive Power", "KILOV-A_Reactive",
              scale=0.001, invert=invert_power, deadband=0.05),
            p(4, MTR["apparent_power"], "Apparent Power", "KILOV-A",
              scale=0.001, min_value=0, deadband=0.05),
            p(6, MTR["power_factor"], "Power Factor", "", dtype="int16",
              scale=0.001, min_value=-1.5, max_value=1.5, deadband=0.01),
            p(7, MTR["voltage"], "Voltage", "V", dtype="uint32", scale=0.1,
              min_value=0, max_value=1000, deadband=1.0),
            p(9, MTR["current"], "Current", "A", dtype="uint32", scale=0.001,
              min_value=0, deadband=0.1),
            p(11, MTR["frequency"], "Frequency", "HZ", dtype="uint16",
              scale=0.01, min_value=40, max_value=70, deadband=0.02),
            # Energy counters: unsigned accumulators, one per direction. Kept
            # separate rather than netted, because netting them loses the
            # information a tariff is calculated from.
            p(12, MTR["energy_import"], "Energy Imported", "KIL0WATT-HR",
              dtype="acc32", scale=0.01, min_value=0, deadband=0.1),
            p(14, MTR["energy_export"], "Energy Exported", "KIL0WATT-HR",
              dtype="acc32", scale=0.01, min_value=0, deadband=0.1),
        ],
    )


# ── §2.1 Split-Core CT Clamps on sub-distribution panels ────────────────────


def ct_clamp_panel(circuits: dict[str, int], *, base_address: int = 0,
                   unit_id: int = 1, scale: float = 0.001,
                   name: str = "") -> PointMap:
    """CT clamps isolating building subsystem loads (§2.1).

    `circuits` maps an asset id to its register: {"HVAC": 0, "Lighting": 2,
    "ServerRoom": 4}. §2.1 names exactly those three as examples and calls the
    purpose "deep demand-side correlation", so the circuit is an ASSET rather than
    a signal variant — adding a fourth panel is a config line, and each circuit
    gets its own history, its own trend and its own place in the asset hierarchy.

    Power is signed even on a load circuit: a sub-panel with its own generation or
    a mis-oriented clamp reads negative, and clamping that to zero would hide a
    commissioning error that is trivial to fix on day one and confusing forever
    after.
    """
    if not circuits:
        raise ValueError("at least one circuit is required")
    points = []
    for asset, register in circuits.items():
        points.append(Point(
            signal=LOAD["circuit_power"], asset_id=asset,
            label=f"{asset} Load", unit="KILOW",
            address=register, data_type="int32", scale=scale,
            min_value=-10_000, max_value=10_000, deadband=0.05,
        ))
    return PointMap(
        name=name or "ct-clamp-panel",
        protocol="modbus_tcp",
        vendor="generic", model="Split-core CT clamp sub-metering",
        description="Subsystem load isolation (§2.1) for demand-side correlation.",
        unit_id=unit_id, base_address=base_address, points=points,
    )


# ── §2.2 Micro-climate station ──────────────────────────────────────────────


def weather_station(asset_id: str, *, base_address: int = 0, unit_id: int = 1,
                    module_temp_zones: dict[str, int] | None = None,
                    name: str = "") -> PointMap:
    """Pyranometer + RTDs + ambient/wind (§2.2).

    This profile is what makes the §4 physics possible at all: without measured
    plane-of-array irradiance and cell temperature there is no baseline to compute
    a residual against, and every "underperformance" alert would just be tracking
    the weather.

    POA irradiance carries a TIGHT deadband (2 W/m²) and everything else a loose
    one. That asymmetry is deliberate: irradiance is the dominant input to the
    modelled baseline, so quantising it coarsely would inject error directly into
    every ΔP the maintenance layer computes. Wind speed, by contrast, only enters
    through the fallback module-temperature model.

    `module_temp_zones` maps a thermal zone's asset id to its RTD register. §2.2
    asks for RTDs "across distinct thermal zones" precisely because a large roof
    is not isothermal — one reference temperature applied to the whole array would
    bias the modelled output of every zone but one.
    """
    points = [
        Point(signal=ENV["poa_irradiance"], asset_id=asset_id,
              label="Plane-of-Array Irradiance", unit="W-PER-M2",
              address=base_address + 0, data_type="uint16", scale=1.0,
              min_value=0.0, max_value=1500.0, deadband=2.0),
        Point(signal=ENV["ghi"], asset_id=asset_id,
              label="Global Horizontal Irradiance", unit="W-PER-M2",
              address=base_address + 1, data_type="uint16", scale=1.0,
              min_value=0.0, max_value=1500.0, deadband=2.0),
        Point(signal=ENV["ambient_temp"], asset_id=asset_id,
              label="Ambient Temperature", unit="DEG_C",
              address=base_address + 2, data_type="int16", scale=0.1,
              min_value=-50.0, max_value=70.0, deadband=0.2),
        Point(signal=ENV["wind_speed"], asset_id=asset_id,
              label="Wind Speed", unit="M-PER-SEC",
              address=base_address + 3, data_type="uint16", scale=0.1,
              min_value=0.0, max_value=90.0, deadband=0.3),
        Point(signal=ENV["wind_direction"], asset_id=asset_id,
              label="Wind Direction", unit="DEG",
              address=base_address + 4, data_type="uint16", scale=1.0,
              min_value=0.0, max_value=360.0, deadband=5.0),
        Point(signal=ENV["humidity"], asset_id=asset_id,
              label="Relative Humidity", unit="PERCENT",
              address=base_address + 5, data_type="uint16", scale=0.1,
              min_value=0.0, max_value=100.0, deadband=1.0),
    ]
    # PT100/RTD per thermal zone. Register 6 onward by default.
    for offset, (zone_asset, register) in enumerate(
            (module_temp_zones or {asset_id: base_address + 6}).items()):
        points.append(Point(
            signal=ENV["module_temp"], asset_id=zone_asset,
            label="Back-of-Module Temperature (PT100)", unit="DEG_C",
            address=register, data_type="int16", scale=0.1,
            min_value=-40.0, max_value=110.0, deadband=0.3,
        ))
    return PointMap(
        name=name or f"weather-station-{asset_id}",
        protocol="modbus_tcp",
        vendor="generic", model="Micro-climate station (pyranometer + RTD + anemometer)",
        description="§2.2 secondary meteorological station. Supplies the baseline "
                    "inputs the De Soto model needs; without it a residual cannot "
                    "be separated from the weather.",
        unit_id=unit_id, base_address=0, points=points,
    )


# ── §2.3 Edge gateway JSON over MQTT ────────────────────────────────────────


def mqtt_gateway(topic_asset_map: dict[str, str] | None = None,
                 *, name: str = "") -> PointMap:
    """The edge gateway's own outbound format (§2.3): "structured JSON over MQTT".

    Mirrors the shape `ingest.submit_raw` already accepts, so a gateway can publish
    the platform's native payload and need no per-point map at all. The points
    below describe the RICHER case where a gateway publishes one composite JSON
    document per device and the fields must be picked out of it.
    """
    points = [
        Point(signal=INV["dc_voltage"], json_path="dc.voltage", unit="V",
              label="DC Voltage", deadband=1.0),
        Point(signal=INV["dc_current"], json_path="dc.current", unit="A",
              label="DC Current", deadband=0.2),
        Point(signal=INV["dc_power"], json_path="dc.power", unit="W",
              label="DC Power", deadband=50.0),
        Point(signal=INV["ac_power"], json_path="ac.power", unit="W",
              label="AC Power", deadband=50.0),
        Point(signal=INV["frequency"], json_path="ac.frequency", unit="HZ",
              label="Frequency", deadband=0.02),
        Point(signal=INV["heatsink_temp"], json_path="temps.heatsink",
              unit="DEG_C", label="Heat-sink Temperature", deadband=0.5),
        Point(signal=INV["operating_state"], json_path="state", unit="",
              label="Operating State", deadband=0.0, deadband_timeout_s=60.0),
        Point(signal=ENV["poa_irradiance"], json_path="weather.poa",
              unit="W-PER-M2", label="POA Irradiance", deadband=2.0),
        Point(signal=ENV["module_temp"], json_path="weather.module_temp",
              unit="DEG_C", label="Module Temperature", deadband=0.3),
    ]
    return PointMap(
        name=name or "mqtt-edge-gateway",
        protocol="mqtt",
        vendor="generic", model="Industrial IoT edge controller",
        description="§2.3 gateway publishing composite JSON per device over "
                    "MQTT/TLS. Asset comes from the topic (see mqtt.py).",
        points=points,
    )


# ── §2.1 Inverter over OPC-UA ───────────────────────────────────────────────


def opcua_inverter(asset_id: str, *, prefix: str = "ns=2;s=Inverter",
                   name: str = "") -> PointMap:
    """An inverter exposed over OPC-UA rather than Modbus (§2.3 lists both).

    Node ids are vendor-specific and this is a TEMPLATE — use
    `POST /api/v1/connectors/{id}/browse` against the live server to discover the
    real address space, then save the discovered node ids. Guessing them fails
    loudly (a bad node id is rejected by the server), which is a much safer failure
    mode than a guessed Modbus offset, and is why this profile is allowed to
    provide defaults where the SunSpec one is not.
    """
    def p(node: str, signal: str, label: str, unit: str, **kw) -> Point:
        return Point(signal=signal, asset_id=asset_id, label=label, unit=unit,
                     node_id=f"{prefix}.{node}", **kw)

    return PointMap(
        name=name or f"opcua-inverter-{asset_id}",
        protocol="opcua",
        vendor="generic", model="OPC-UA PV inverter",
        description="OPC-UA inverter template. Browse the server to replace these "
                    "node ids with the real ones.",
        points=[
            p("DCVoltage", INV["dc_voltage"], "DC Voltage", "V", deadband=1.0),
            p("DCCurrent", INV["dc_current"], "DC Current", "A", deadband=0.2),
            p("DCPower", INV["dc_power"], "DC Power", "W", deadband=50.0),
            p("ACPower", INV["ac_power"], "AC Power", "W", deadband=50.0),
            p("Frequency", INV["frequency"], "Frequency", "HZ", deadband=0.02),
            p("PowerFactor", INV["power_factor"], "Power Factor", "",
              deadband=0.01),
            p("HeatsinkTemp", INV["heatsink_temp"], "Heat-sink Temp", "DEG_C",
              deadband=0.5),
            p("State", INV["operating_state"], "Operating State", "",
              deadband=0.0, deadband_timeout_s=60.0),
        ],
    )


# ── Registry ────────────────────────────────────────────────────────────────
#
# Factories, not instances: every profile needs at least an asset id, and a shared
# mutable PointMap would let one connector's edits leak into another's.

BUILTIN_PROFILES = {
    "sunspec_inverter_3ph": {
        "factory": sunspec_inverter_3ph,
        "label": "SunSpec 3-phase PV inverter (Modbus)",
        "spec_section": "2.1 Smart Commercial Inverters",
        "protocol": "modbus_tcp",
        "required": ["asset_id", "base_address"],
    },
    "string_combiner": {
        "factory": string_combiner,
        "label": "String combiner / per-string current monitor (Modbus)",
        "spec_section": "2.1 String Combiner Monitoring Units",
        "protocol": "modbus_tcp",
        "required": ["asset_template", "string_count"],
    },
    "revenue_meter": {
        "factory": revenue_meter,
        "label": "Bidirectional revenue-grade meter (Modbus)",
        "spec_section": "2.1 Bidirectional Revenue-Grade Smart Meters",
        "protocol": "modbus_tcp",
        "required": ["asset_id"],
    },
    "ct_clamp_panel": {
        "factory": ct_clamp_panel,
        "label": "Split-core CT clamp sub-metering (Modbus)",
        "spec_section": "2.1 Split-Core Current Transformer Clamps",
        "protocol": "modbus_tcp",
        "required": ["circuits"],
    },
    "weather_station": {
        "factory": weather_station,
        "label": "Micro-climate station: pyranometer, RTD, anemometer (Modbus)",
        "spec_section": "2.2 Micro-Climate & Environmental Sensors",
        "protocol": "modbus_tcp",
        "required": ["asset_id"],
    },
    "mqtt_gateway": {
        "factory": mqtt_gateway,
        "label": "Edge gateway composite JSON (MQTT/TLS)",
        "spec_section": "2.3 Edge Ingestion Gateway",
        "protocol": "mqtt",
        "required": [],
    },
    "opcua_inverter": {
        "factory": opcua_inverter,
        "label": "PV inverter over OPC-UA",
        "spec_section": "2.1 / 2.3 OPC UA",
        "protocol": "opcua",
        "required": ["asset_id"],
    },
}


def list_profiles() -> list[dict]:
    """Catalogue for the commissioning UI."""
    return [{"key": key, "label": meta["label"],
             "spec_section": meta["spec_section"],
             "protocol": meta["protocol"], "required": meta["required"]}
            for key, meta in BUILTIN_PROFILES.items()]


def build_profile(key: str, **kwargs) -> PointMap:
    """Instantiate a built-in profile by key."""
    meta = BUILTIN_PROFILES.get(key)
    if meta is None:
        raise ValueError(
            f"unknown profile '{key}'. Known: {sorted(BUILTIN_PROFILES)}")
    missing = [r for r in meta["required"] if r not in kwargs]
    if missing:
        raise ValueError(f"profile '{key}' requires {missing}")
    return meta["factory"](**kwargs)
