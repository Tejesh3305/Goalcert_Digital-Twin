"""
test_solar_spec.py — the specification, clause by clause.

Each test names the section it verifies, so a reviewer can walk the specification and
this file side by side. The emphasis is on the claims that are easy to make and hard
to keep:

  §2.1  every hardware class has a working profile, and the signed/scaled traps
        that break revenue meters and inverters are handled
  §2.2  the micro-climate sensors are wired to the model that needs them
  §2.3  1-5 s polling, deadband filtering, TLS 1.3
  §3    asset ids follow the hierarchical tagging the 3-D scene tree needs
  §4.1  the De Soto model runs (test_desoto.py covers its correctness in depth)
  §4.2  the three signatures are DISTINGUISHABLE — not merely detectable
  §5    the three operating modes have the surfaces they need

The §4.2 tests matter most. Any monitoring product can notice that an array is
underperforming; the specification's value is in separating soiling from shading from
PID, and those three are only separable because each perturbs a different parameter of
the diode model. If a change collapsed two signatures into one, the platform would
still detect faults and would stop diagnosing them — a regression no output-threshold
test would catch.
"""

from __future__ import annotations

import pytest

from conftest import KEY_ACME, KEY_ADMIN, KEY_READER, hdr

from packs.solar import (
    FAULTS, SIGNALS, SPEC, SolarPhysics, component_health, predict as solar_predict,
)

TENANT = "acme"


@pytest.fixture(scope="module")
def physics():
    return SolarPhysics()


def _array(physics, *, fault=None, severity=0.9, ticks=60, dt=60.0,
           poa=950.0, strings=12):
    """A twin state advanced under fixed clear-noon conditions."""
    state = physics.init_state(strings=strings, modules_in_series=20,
                               inverter_rating_w=110_000)
    physics.apply_measurements(state, poa=poa, ghi=poa / 1.08,
                               ambient_temp=30.0, wind_speed=2.0)
    if fault:
        physics.inject(state, fault, severity)
    frame = None
    for _ in range(ticks):
        frame = physics.forward(state, dt=dt)
    return state, frame


# ══════════════════════════════════════════════════════════════════════════
# §2.1 — Power & telemetry extraction hardware
# ══════════════════════════════════════════════════════════════════════════


def test_every_hardware_class_in_the_spec_has_a_profile():
    """§2.1 and §2.2 name five hardware classes plus the gateway. Each must be
    commissionable without writing code."""
    from connectors.profiles import list_profiles
    sections = {p["spec_section"] for p in list_profiles()}
    for required in ("2.1 Smart Commercial Inverters",
                     "2.1 String Combiner Monitoring Units",
                     "2.1 Bidirectional Revenue-Grade Smart Meters",
                     "2.1 Split-Core Current Transformer Clamps",
                     "2.2 Micro-Climate & Environmental Sensors",
                     "2.3 Edge Ingestion Gateway"):
        assert required in sections, f"no profile covers '{required}'"


def test_every_profile_validates_against_the_signal_catalogue():
    """A profile mapping a signal the catalogue does not know would create a series
    nothing reads."""
    from connectors.profiles import build_profile
    from packs.solar.signals import ALL_SIGNAL_IRIS

    cases = [
        ("sunspec_inverter_3ph", {"asset_id": "Zone_01_Inverter_01",
                                  "base_address": 40072}),
        ("string_combiner", {"asset_template": "Z_{zone:02d}_I_{inverter:02d}_S_{index:02d}",
                             "string_count": 8}),
        ("revenue_meter", {"asset_id": "MDB"}),
        ("ct_clamp_panel", {"circuits": {"HVAC": 0, "Lighting": 2}}),
        ("weather_station", {"asset_id": "Met_01"}),
        ("mqtt_gateway", {}),
        ("opcua_inverter", {"asset_id": "Zone_01_Inverter_02"}),
    ]
    for key, kwargs in cases:
        build_profile(key, **kwargs).validate(known_signals=ALL_SIGNAL_IRIS)


def test_sunspec_profile_refuses_to_guess_its_base_address():
    """§2.1's most dangerous configuration error. A Modbus offset wrong by a few
    registers still decodes into plausible small integers, so the device looks like it
    is working and every number is wrong. The profile must therefore REQUIRE the
    discovered value."""
    from connectors.profiles import build_profile
    with pytest.raises(ValueError, match="base_address"):
        build_profile("sunspec_inverter_3ph", asset_id="inv-1")


def test_revenue_meter_power_points_are_signed():
    """A bidirectional meter that decodes export as unsigned reports +4.29 GW instead
    of −5 kW, corrupting every financial figure."""
    from connectors.profiles import revenue_meter
    from packs.solar.signals import METER_SIGNALS

    pm = revenue_meter("MDB")
    power_signals = {METER_SIGNALS["active_power"], METER_SIGNALS["reactive_power"]}
    for point in pm.points:
        if point.signal in power_signals:
            assert point.data_type.startswith("int"), \
                f"{point.signal} must be signed to represent export"


def test_meter_sign_convention_is_documented_and_correctable():
    from packs.solar.signals import METER_SIGNALS, SIGN_CONVENTIONS
    from connectors.profiles import revenue_meter

    assert "POSITIVE = importing" in SIGN_CONVENTIONS[METER_SIGNALS["active_power"]]
    inverted = revenue_meter("MDB", invert_power=True)
    active = next(p for p in inverted.points
                  if p.signal == METER_SIGNALS["active_power"])
    assert active.invert is True


def test_string_combiner_is_per_string_not_aggregate():
    """§2.1 calls per-string current "critical for isolating localized anomalies" —
    and §4.2 row 2 is literally impossible without it."""
    from connectors.profiles import string_combiner
    from packs.solar.signals import STRING_SIGNALS

    pm = string_combiner("Z_{zone:02d}_I_{inverter:02d}_S_{index:02d}",
                         string_count=24)
    current_points = [p for p in pm.points
                      if p.signal == STRING_SIGNALS["string_current"]]
    assert len(current_points) == 24
    assert len({p.asset_id for p in current_points}) == 24, \
        "each string needs its own asset id, or they collapse into one series"


def test_ct_clamps_model_each_circuit_as_its_own_asset():
    """§2.1 names HVAC, lighting and server rooms. Each gets its own history."""
    from connectors.profiles import ct_clamp_panel
    pm = ct_clamp_panel({"HVAC": 0, "Lighting": 2, "ServerRoom": 4})
    assert {p.asset_id for p in pm.points} == {"HVAC", "Lighting", "ServerRoom"}


# ══════════════════════════════════════════════════════════════════════════
# §2.2 — Micro-climate sensors
# ══════════════════════════════════════════════════════════════════════════


def test_weather_station_covers_every_sensor_the_spec_lists():
    from connectors.profiles import weather_station
    from packs.solar.signals import WEATHER_SIGNALS

    pm = weather_station("Met_01")
    mapped = {p.signal for p in pm.points}
    for key in ("poa_irradiance", "ghi", "module_temp", "ambient_temp",
                "wind_speed"):
        assert WEATHER_SIGNALS[key] in mapped, f"§2.2 sensor '{key}' unmapped"


def test_rtd_per_thermal_zone():
    """§2.2 asks for RTDs "across distinct thermal zones" because a large roof is not
    isothermal — one reference temperature would bias every zone but one."""
    from connectors.profiles import weather_station
    from packs.solar.signals import WEATHER_SIGNALS

    pm = weather_station("Met_01",
                         module_temp_zones={"Zone_01": 6, "Zone_02": 7,
                                            "Zone_03": 8})
    rtds = [p for p in pm.points if p.signal == WEATHER_SIGNALS["module_temp"]]
    assert {p.asset_id for p in rtds} == {"Zone_01", "Zone_02", "Zone_03"}


def test_irradiance_carries_a_tighter_deadband_than_wind():
    """Irradiance is the dominant input to the modelled baseline, so quantising it
    coarsely injects error into every ΔP. Wind only enters through a fallback
    module-temperature model, so it can be coarser.

    Compared as a FRACTION of each signal's plausible range, not as raw numbers:
    2 W/m² and 0.3 m/s are different units and an absolute comparison between them
    means nothing. Relative resolution is the quantity that actually determines how
    much error the filter injects.
    """
    from connectors.profiles import weather_station
    from packs.solar.signals import WEATHER_SIGNALS

    points = {p.signal: p for p in weather_station("Met_01").points}

    def relative(signal: str) -> float:
        point = points[signal]
        span = (point.max_value or 0.0) - (point.min_value or 0.0)
        assert span > 0, f"{signal} has no plausibility range to normalise against"
        return point.deadband / span

    poa = relative(WEATHER_SIGNALS["poa_irradiance"])
    wind = relative(WEATHER_SIGNALS["wind_speed"])
    assert poa < wind, (
        f"irradiance is resolved to {poa:.4%} of its range and wind to {wind:.4%}; "
        f"the dominant model input must be the finer of the two")


def test_measured_sensors_override_the_simulator(physics):
    """On a live site the pyranometer is the authority. Overwriting it with a
    clear-sky model would make every residual meaningless."""
    state = physics.init_state()
    physics.apply_measurements(state, poa=612.0, ghi=570.0, ambient_temp=27.0,
                               wind_speed=3.0)
    assert state.measured_env is True
    physics.forward(state, dt=600.0)
    assert state.poa == pytest.approx(612.0), \
        "the diurnal simulator overwrote a MEASURED irradiance"


def test_module_temp_falls_back_to_faiman_when_the_rtd_is_missing(physics):
    """Why §2.2 asks for ambient and wind at all: a zone whose RTD has died keeps a
    usable baseline instead of going blind."""
    state = physics.init_state()
    physics.apply_measurements(state, poa=900.0, ambient_temp=25.0, wind_speed=2.0)
    assert state.module_temp > 25.0
    assert state.cell_temp > state.module_temp


# ══════════════════════════════════════════════════════════════════════════
# §2.3 — Edge gateway
# ══════════════════════════════════════════════════════════════════════════


def test_poll_interval_covers_the_specified_range():
    """§2.3: 1 Hz to 0.2 Hz, i.e. 1-5 s."""
    from connectors.base import ConnectorConfig
    for interval in (1.0, 2.0, 5.0):
        config = ConnectorConfig(connector_id="c", tenant_id=TENANT,
                                 protocol="modbus_tcp",
                                 poll_interval_s=interval)
        assert config.poll_interval_s == interval


def test_tls_is_on_by_default_and_verification_is_not_disabled():
    """§2.3 requires TLS 1.3. The safe posture must be what you get by omission."""
    from connectors.base import ConnectorConfig
    config = ConnectorConfig(connector_id="c", tenant_id=TENANT, protocol="mqtt")
    assert config.use_tls is True
    assert config.tls_insecure is False


def test_connector_config_never_leaks_a_password():
    """A connector config holds a customer's PLC password. `redacted()` is
    allow-listed, so a field added later cannot be published by omission."""
    from connectors.base import ConnectorConfig
    config = ConnectorConfig(connector_id="c", tenant_id=TENANT,
                             protocol="modbus_tcp", password="s3cret-plc-pw",
                             client_key="-----BEGIN PRIVATE KEY-----")
    payload = config.redacted()
    serialised = repr(payload)
    assert "s3cret-plc-pw" not in serialised
    assert "BEGIN PRIVATE KEY" not in serialised
    assert payload["password"] == "***"
    assert payload["has_client_cert"] is False


def test_modbus_read_planning_batches_into_few_round_trips():
    """§2.3 asks for 1 Hz polling. Point-by-point reads of a 40-point inverter are 40
    round trips, which cannot meet that on an RS-485 segment."""
    from connectors.modbus import _plan_blocks
    from connectors.profiles import sunspec_inverter_3ph

    pm = sunspec_inverter_3ph("inv-1", base_address=40072)
    blocks = _plan_blocks(pm)
    assert len(blocks) <= 3, \
        f"{len(pm.points)} points planned into {len(blocks)} reads — too many"
    for _fc, _start, count in blocks:
        assert count <= 120, "a single Modbus read cannot exceed 125 registers"


def test_read_planning_never_merges_holding_and_input_registers():
    """They are SEPARATE address spaces; merging would read the wrong space and
    return a plausible wrong number."""
    from connectors.modbus import _plan_blocks
    from connectors.pointmap import Point, PointMap

    pm = PointMap(name="mixed", protocol="modbus_tcp", points=[
        Point(signal="a", address=10, function_code=3),
        Point(signal="b", address=11, function_code=4),
    ])
    blocks = _plan_blocks(pm)
    assert {fc for fc, _, _ in blocks} == {3, 4}
    assert len(blocks) == 2


# ══════════════════════════════════════════════════════════════════════════
# §3 — Asset hierarchy / hierarchical tagging
# ══════════════════════════════════════════════════════════════════════════


def test_string_asset_ids_match_the_spec_example(physics):
    """§3 Step 1: "Zone_02_Inverter_04_String_08". The 3-D scene node and the
    telemetry series must share this id or §5.1 cannot highlight the right panel."""
    from packs.solar import build_strings
    strings = build_strings(count=8, zone=2, inverter=4)
    assert strings[7].string_id == "Zone_02_Inverter_04_String_08"


def test_findings_flag_the_string_not_the_inverter(physics):
    """§4.2 row 2 isolates "the specific panel string". A finding that flagged the
    inverter would highlight the wrong node."""
    state, frame = _array(physics, fault="shading", severity=0.9)
    bypassed = [s for s in frame["_strings"] if s["bypassed"] > 0.01]
    assert bypassed, "shading injection produced no bypassed string"
    assert bypassed[0]["string_id"].startswith("Zone_")


# ══════════════════════════════════════════════════════════════════════════
# §4.2 — The three signatures must be DISTINGUISHABLE
# ══════════════════════════════════════════════════════════════════════════


def test_soiling_suppresses_current_uniformly_and_leaves_voltage(physics):
    """Row 1: "ΔP < -12%; V_dc normal; I_dc uniformly suppressed"."""
    healthy_state, healthy = _array(physics)
    dirty_state, dirty = _array(physics, fault="soiling", severity=0.9)

    assert dirty[SIGNALS["dc_current"]] < healthy[SIGNALS["dc_current"]] * 0.98, \
        "soiling must suppress current"
    assert dirty[SIGNALS["dc_voltage"]] == pytest.approx(
        healthy[SIGNALS["dc_voltage"]], rel=0.03), \
        "soiling must leave voltage NORMAL — that is row 1's discriminator"
    assert dirty[SIGNALS["imbalance"]] < 4.0, \
        "soiling must be UNIFORM across strings, or it is row 2 not row 1"


def test_soiling_is_uniform_across_every_string(physics):
    state, frame = _array(physics, fault="soiling", severity=0.9, ticks=40,
                          dt=86400.0)
    soiling = [s["soiling"] for s in frame["_strings"]]
    assert max(soiling) - min(soiling) < 0.01, \
        f"soiling is not uniform: spread {max(soiling) - min(soiling):.4f}"


def test_shading_collapses_one_strings_voltage_while_current_holds(physics):
    """Row 2: "Step-function drop in V_dc on a single string; I_dc constant"."""
    state, frame = _array(physics, fault="shading", severity=0.9)
    strings = frame["_strings"]
    affected = [s for s in strings if s["bypassed"] > 0.01]
    healthy = [s for s in strings if s["bypassed"] <= 0.01]
    assert affected and healthy

    victim = affected[0]
    peer_voltage = sum(s["voltage"] for s in healthy) / len(healthy)
    peer_current = sum(s["current"] for s in healthy) / len(healthy)

    assert victim["voltage"] < peer_voltage * 0.88, \
        "the shaded string's voltage must collapse relative to its peers"
    assert victim["current"] > peer_current * 0.80, \
        "current must stay roughly INTACT — a cloud takes current, a bypass diode does not"


def test_shading_and_soiling_are_not_confusable(physics):
    """The whole point of §4.2. If these two produced the same observable pattern the
    twin could detect a fault and never identify it."""
    _, soiled = _array(physics, fault="soiling", severity=0.9)
    _, shaded = _array(physics, fault="shading", severity=0.9)

    def voltage_spread(frame):
        voltages = [s["voltage"] for s in frame["_strings"] if s["voltage"] > 0]
        return (max(voltages) - min(voltages)) / max(voltages) if voltages else 0.0

    assert voltage_spread(soiled) < 0.05, "soiling must not spread string voltages"
    assert voltage_spread(shaded) > 0.10, "shading must spread string voltages"


def test_pid_decays_shunt_resistance_over_months(physics):
    """Row 3: "Gradual progressive decline in R_sh calculated over 90 days"."""
    readings = []
    for days in (1, 30, 90):
        _, frame = _array(physics, fault="pid", severity=0.9,
                          ticks=days, dt=86400.0)
        readings.append(frame.get(SIGNALS["rsh"]))
    assert all(r is not None for r in readings)
    assert readings[0] > readings[1] > readings[2], \
        f"R_sh must decline progressively: {readings}"
    assert readings[2] < readings[0] * 0.5, \
        "90 days of PID should roughly halve the normalised shunt resistance"


def test_pid_is_not_visible_in_an_hour(physics):
    """PID is a 90-day phenomenon. A model that showed it within an hour would not be
    exercising the trend rule that must distinguish degradation from weather."""
    _, hour = _array(physics, fault="pid", severity=0.9, ticks=60, dt=60.0)
    _, healthy = _array(physics)
    assert hour[SIGNALS["delta_p_pct"]] == pytest.approx(
        healthy[SIGNALS["delta_p_pct"]], abs=0.5)


def test_resistive_loss_is_distinguishable_from_shunt_decay(physics):
    """Both erode fill factor. R_s leaves V_oc intact; R_sh does not. Without that
    distinction a PID ticket and a connector-corrosion ticket are the same ticket."""
    _, pid = _array(physics, fault="pid", severity=0.9, ticks=90, dt=86400.0)
    _, resistive = _array(physics, fault="resistive", severity=0.9, ticks=90,
                          dt=86400.0)
    pid_rsh = pid.get(SIGNALS["rsh"])
    resistive_rsh = resistive.get(SIGNALS["rsh"])
    assert pid_rsh < resistive_rsh * 0.5, \
        (f"PID must decay R_sh far more than resistive loss does "
         f"(pid={pid_rsh}, resistive={resistive_rsh})")


def test_open_string_is_unambiguous(physics):
    state, frame = _array(physics, fault="string_open", severity=1.0)
    assert frame[SIGNALS["imbalance"]] > 50.0
    assert any(s["open"] for s in frame["_strings"])


def test_a_drifting_pyranometer_manufactures_a_false_deficit(physics):
    """The false-positive case every residual rule must survive: an inflated baseline
    looks exactly like reduced output, so a drifting sensor produces false SOILING
    tickets rather than a sensor alarm."""
    state, frame = _array(physics, fault="sensor_fault", severity=1.0, ticks=5)
    ratio = frame[SIGNALS["poa"]] / max(1.0, frame[SIGNALS["ghi"]])
    assert ratio > 1.25, \
        "the injected sensor fault should push POA/GHI well above its normal ~1.08"


def test_sensor_drift_is_caught_by_the_learned_ratio_baseline():
    """A fixed plausibility band CANNOT catch a 25 % drift — the ratio lands at 1.35,
    inside any defensible band. The learned-baseline arm is what actually detects it,
    which is why the rule has two arms."""
    from datetime import datetime, timedelta, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import SensorPlausibility

    rule = SensorPlausibility(warmup=30, baseline_ghi=500.0, drift_pct=12.0)

    class Frame:
        def __init__(self, ghi):
            self.ghi = ghi

        def get_property(self, tenant, entity, key, default=None):
            return self.ghi if key == "ghi" else default

    start = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)

    def sample(poa, i):
        return TelemetrySample(signal=SIGNALS["poa"], entity_id="met-1",
                               value=poa, unit="W-PER-M2",
                               timestamp=start + timedelta(minutes=i),
                               tenant_id=TENANT)

    # Train a baseline at the healthy ratio (950 / 880 = 1.079).
    for i in range(40):
        assert rule.evaluate(sample(950.0, i), Frame(880.0)) == []

    # Now the pyranometer reads 25 % high. Ratio 1.35 — inside the absolute band.
    findings = rule.evaluate(sample(1187.5, 41), Frame(880.0))
    assert findings, "learned-baseline arm failed to catch a 25 % drift"
    assert findings[0].evidence["detection"] == "drift"
    assert "solar.soiling_symmetric_drop" in findings[0].evidence["invalidates"]


def test_the_spec_table_is_published_machine_readable():
    """So the copilot's work-order agent can act on a diagnosis without re-parsing an
    English message."""
    triggers = SPEC["maintenance_triggers"]
    assert len(triggers) == 3
    signatures = {t["signature"] for t in triggers}
    assert signatures == {"Symmetric Output Drop", "String Voltage Collapse",
                          "Shunt Resistance Decay"}
    for trigger in triggers:
        assert trigger["behavior_id"].startswith("solar.")
        assert trigger["priority"] in ("low", "high", "scheduled")
        assert trigger["diagnosis"] and trigger["action"]


def test_the_registry_contains_every_specified_rule():
    from packs.solar import build_solar_registry
    ids = {b.behavior_id for b in build_solar_registry().all()}
    for trigger in SPEC["maintenance_triggers"]:
        assert trigger["behavior_id"] in ids


# ══════════════════════════════════════════════════════════════════════════
# Behavioural guards — the false positives that destroy operator trust
# ══════════════════════════════════════════════════════════════════════════


def test_soiling_rule_stays_quiet_below_diagnostic_irradiance():
    """A percentage residual at dawn has a tiny denominator and swings wildly. A
    cleaning ticket raised at 5 a.m. costs a truck roll and teaches operators to close
    tickets unread."""
    from datetime import datetime, timedelta, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import SymmetricOutputDrop

    rule = SymmetricOutputDrop(duration_minutes=1.0)

    class Dawn:
        def get_property(self, tenant, entity, key, default=None):
            return {"poairradiance": 40.0, "operatingstate": 1.0,
                    "currentimbalance": 0.5}.get(key, default)

    start = datetime(2026, 7, 1, 5, tzinfo=timezone.utc)
    for i in range(10):
        found = rule.evaluate(TelemetrySample(
            signal=SIGNALS["delta_p_pct"], entity_id="inv-1", value=-40.0,
            unit="PERCENT", timestamp=start + timedelta(minutes=i),
            tenant_id=TENANT), Dawn())
        assert found == [], "fired a soiling ticket before dawn"


def test_soiling_rule_stays_quiet_when_the_inverter_is_not_generating():
    from datetime import datetime, timedelta, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import SymmetricOutputDrop

    rule = SymmetricOutputDrop(duration_minutes=1.0)

    class Curtailed:
        def get_property(self, tenant, entity, key, default=None):
            # State 6 = fault, in NON_GENERATING_STATES.
            return {"poairradiance": 900.0, "operatingstate": 6.0,
                    "currentimbalance": 0.5}.get(key, default)

    start = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    for i in range(10):
        assert rule.evaluate(TelemetrySample(
            signal=SIGNALS["delta_p_pct"], entity_id="inv-1", value=-40.0,
            unit="PERCENT", timestamp=start + timedelta(minutes=i),
            tenant_id=TENANT), Curtailed()) == []


def test_soiling_rule_requires_a_sustained_deficit():
    """A passing cloud produces the soiling signature for thirty seconds. Soiling does
    not clear."""
    from datetime import datetime, timedelta, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import SymmetricOutputDrop

    rule = SymmetricOutputDrop(duration_minutes=45.0)

    class Noon:
        def get_property(self, tenant, entity, key, default=None):
            return {"poairradiance": 900.0, "operatingstate": 3.0,
                    "currentimbalance": 1.0}.get(key, default)

    start = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    # 10 minutes of deficit — a cloud. Must not fire.
    for i in range(10):
        assert rule.evaluate(TelemetrySample(
            signal=SIGNALS["delta_p_pct"], entity_id="inv-1", value=-20.0,
            unit="PERCENT", timestamp=start + timedelta(minutes=i),
            tenant_id=TENANT), Noon()) == []
    # 50 minutes in — genuinely sustained. Must fire exactly once (edge-latched).
    fired = rule.evaluate(TelemetrySample(
        signal=SIGNALS["delta_p_pct"], entity_id="inv-1", value=-20.0,
        unit="PERCENT", timestamp=start + timedelta(minutes=50),
        tenant_id=TENANT), Noon())
    assert len(fired) == 1
    assert fired[0].evidence["diagnosis"] == "soiling"
    assert fired[0].evidence["priority"] == "low"
    again = rule.evaluate(TelemetrySample(
        signal=SIGNALS["delta_p_pct"], entity_id="inv-1", value=-20.0,
        unit="PERCENT", timestamp=start + timedelta(minutes=51),
        tenant_id=TENANT), Noon())
    assert again == [], "rule is not edge-latched — it would emit one finding per tick"


def test_string_collapse_rule_needs_per_string_data():
    """§2.1 specifies combiner monitoring because this diagnosis is impossible without
    it. Staying silent is right; guessing from the aggregate is not."""
    from datetime import datetime, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import StringVoltageCollapse

    rule = StringVoltageCollapse()

    class NoStrings:
        def get_property(self, tenant, entity, key, default=None):
            return 900.0 if key in ("poairradiance", "poa") else default

    assert rule.evaluate(TelemetrySample(
        signal=SIGNALS["dc_voltage"], entity_id="inv-1", value=500.0, unit="V",
        timestamp=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        tenant_id=TENANT), NoStrings()) == []


def test_shunt_decay_rule_requires_a_long_baseline_and_a_real_trend():
    """"Gradual progressive decline": the slope measures "decline", R² measures
    "progressive". Noise with a downward slope must not raise a PID ticket."""
    from datetime import datetime, timedelta, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import ShuntResistanceDecay

    class Sunny:
        def get_property(self, tenant, entity, key, default=None):
            return 900.0 if key in ("poairradiance", "poa") else default

    start = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)

    def feed(rule, values):
        out = []
        for day, value in enumerate(values):
            out.extend(rule.evaluate(TelemetrySample(
                signal=SIGNALS["rsh"], entity_id="inv-1", value=value, unit="OHM",
                timestamp=start + timedelta(days=day), tenant_id=TENANT), Sunny()))
        return out

    # A clean 40 % linear decline over 60 days must fire.
    declining = ShuntResistanceDecay(min_samples=21, decline_pct=15.0)
    assert feed(declining, [500.0 - 3.3 * d for d in range(60)]), \
        "a clean progressive decline did not fire"

    # A flat series with the same mean must not.
    flat = ShuntResistanceDecay(min_samples=21, decline_pct=15.0)
    assert feed(flat, [500.0 + (7.0 if d % 2 else -7.0) for d in range(60)]) == [], \
        "noise fired a PID ticket"


def test_shunt_decay_rule_samples_daily_not_per_tick():
    """A 1 Hz feed over 90 days is 7.8 M points. Regressing over that in a rule
    evaluated every second would be absurd, and no more informative."""
    from datetime import datetime, timedelta, timezone

    from behaviors.registry import TelemetrySample
    from packs.solar.behaviors import ShuntResistanceDecay

    class Sunny:
        def get_property(self, tenant, entity, key, default=None):
            return 900.0 if key in ("poairradiance", "poa") else default

    rule = ShuntResistanceDecay()
    start = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)
    for minute in range(200):
        rule.evaluate(TelemetrySample(
            signal=SIGNALS["rsh"], entity_id="inv-1", value=500.0, unit="OHM",
            timestamp=start + timedelta(minutes=minute), tenant_id=TENANT), Sunny())
    assert len(rule._series["inv-1"]) == 1, \
        "the rule retained more than one sample per day"


def test_health_is_not_critical_at_night(physics):
    """An array at night is dark, not broken. A twin that shows every array critical
    every night trains its operators to ignore the health ring."""
    state = physics.init_state()
    physics.apply_measurements(state, poa=0.0, ghi=0.0, ambient_temp=18.0,
                               wind_speed=1.0)
    frame = physics.forward(state, dt=60.0)
    assert physics.health_index(frame) == 1.0
    assert frame[SIGNALS["dc_power"]] == 0.0


# ══════════════════════════════════════════════════════════════════════════
# §5 — Operating modes (API surface)
# ══════════════════════════════════════════════════════════════════════════


def test_spec_declares_the_three_operating_modes():
    keys = {m["key"] for m in SPEC["operation_modes"]}
    assert keys == {"command_center", "field_service", "training_lab"}


def test_model_evaluate_is_open_and_returns_a_curve(api):
    """§4.1's model, callable without a tenant: it carries no telemetry, so a
    technical buyer can check the platform's physics against a datasheet."""
    resp = api.post("/api/v1/solar/model/evaluate",
                    json={"irradiance": 1000.0, "cell_temp_c": 25.0,
                          "modules_in_series": 1, "points": 20},
                    headers=hdr(KEY_READER))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operating_point"]["p_mp"] == pytest.approx(550.0, rel=0.01)
    assert body["operating_point"]["v_oc"] == pytest.approx(49.9, rel=0.01)
    assert len(body["iv_curve"]) == 21
    assert "De Soto" in body["model"]["name"]


def test_model_evaluate_reflects_degradation(api):
    """The §5.3 training lab builds exercises by asking "what would X look like"."""
    def pmp(**kwargs):
        payload = {"irradiance": 1000.0, "cell_temp_c": 45.0,
                   "modules_in_series": 20, **kwargs}
        return api.post("/api/v1/solar/model/evaluate", json=payload,
                        headers=hdr(KEY_READER)).json()["operating_point"]["p_mp"]

    clean = pmp()
    assert pmp(soiling=0.8) < clean * 0.85
    assert pmp(rsh_factor=0.1) < clean
    assert pmp(rs_factor=5.0) < clean


def test_triggers_endpoint_publishes_the_spec_table(api):
    resp = api.get("/api/v1/solar/triggers", headers=hdr(KEY_READER))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["triggers"]) == 3
    assert set(body["faults_injectable"]) >= set(FAULTS)


def test_solar_endpoints_are_tenant_scoped(api):
    """A new analytics surface must not reintroduce the cross-tenant hole."""
    for path in ("/api/v1/solar/globex/residual",
                 "/api/v1/solar/globex/diagnosis",
                 "/api/v1/solar/globex/heatmap",
                 "/api/v1/solar/globex/energy",
                 "/api/v1/solar/globex/forecast",
                 "/api/v1/solar/globex/strings",
                 "/api/v1/solar/globex/measured/summary"):
        assert api.get(path, headers=hdr(KEY_ACME)).status_code == 403, path
    assert api.post("/api/v1/solar/globex/training/scenario",
                    json={"fault": "soiling"},
                    headers=hdr(KEY_ACME)).status_code == 403


def test_training_scenario_requires_write_access(api):
    """It MUTATES the twin — harmless on a training twin, emphatically not on a
    production one."""
    assert api.post(f"/api/v1/solar/{TENANT}/training/scenario",
                    json={"fault": "soiling"},
                    headers=hdr(KEY_READER)).status_code == 403


def test_non_solar_tenant_gets_a_clear_404(api):
    resp = api.get(f"/api/v1/solar/{TENANT}/residual", headers=hdr(KEY_ACME))
    assert resp.status_code == 404
    assert "solar-pv-array" in resp.json()["detail"]


# ══════════════════════════════════════════════════════════════════════════
# Prediction
# ══════════════════════════════════════════════════════════════════════════


def test_forecast_horizon_is_in_days_not_minutes(physics):
    """Soiling takes weeks and PID takes months. A two-hour projection of a PV array
    is a flat line."""
    state, _ = _array(physics, fault="pid", severity=0.8, ticks=10, dt=86400.0)
    result = solar_predict(state, points=60, physics=physics)
    assert result["horizon_days"] >= 90.0
    assert result["trajectory"]
    assert "days" in result["trajectory"][0]


def test_forecast_rul_is_time_to_a_decision_with_an_action(physics):
    """An array almost never stops working. Each RUL entry must be schedulable, which
    means it needs an action, not only a date."""
    state, _ = _array(physics, fault="pid", severity=0.9, ticks=20, dt=86400.0)
    result = solar_predict(state, points=90, physics=physics)
    assert result["rul"], "PID produced no time-to-intervention"
    for entry in result["rul"]:
        assert entry["days"] > 0 and entry["months"] > 0
    for event in result["events"]:
        assert event["action"], "an event without an action is not schedulable"
        assert event["priority"]


def test_component_health_maps_to_distinct_interventions(physics):
    state, frame = _array(physics)
    health = component_health(state, frame, physics)
    assert set(health) == {"modules", "strings", "cells", "inverter", "sensors",
                           "overall"}
    for key, value in health.items():
        assert 0.0 <= value["health"] <= 1.0
        assert value["status"] in ("ok", "warning", "critical")


def test_soiling_degrades_module_health_specifically(physics):
    """A health breakdown is only useful if a fault moves the RIGHT subsystem."""
    _, clean_frame = _array(physics)
    clean_state, _ = _array(physics)
    dirty_state, dirty_frame = _array(physics, fault="soiling", severity=0.9,
                                      ticks=30, dt=86400.0)
    clean = component_health(clean_state, clean_frame, physics)
    dirty = component_health(dirty_state, dirty_frame, physics)
    assert dirty["modules"]["health"] < clean["modules"]["health"] - 0.1
