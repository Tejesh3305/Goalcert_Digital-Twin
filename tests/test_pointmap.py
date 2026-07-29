"""
test_pointmap.py — pins the register decoders and the point-map validator.

WHY THIS IS THE RISKIEST CODE IN THE INGEST PATH
------------------------------------------------
Modbus carries 16-bit registers and nothing else. Every larger type is a vendor
convention layered on top, and the conventions disagree — word order, byte order,
signedness, and SunSpec's separate scale-factor register.

Get one wrong and the failure is often NOT obvious. A word-order error on a large
float gives 5.4e-42 (visible immediately); on a small value it gives something
plausible, which silently poisons a baseline every §4.2 threshold is compared
against. Reading a signed −5 kW export as unsigned gives 4.29e9, which on a
bidirectional revenue meter is a financial error.

Every assertion below is against a HAND-COMPUTED byte pattern, so the test is
independent of the implementation rather than a restatement of it.
"""

from __future__ import annotations

import pytest
from connectors.pointmap import (
    DeadbandFilter,
    Point,
    PointMap,
    PointMapError,
    decode_registers,
    expand_asset_template,
    extract_json_path,
    registers_to_bytes,
)

# ── Byte assembly ───────────────────────────────────────────────────────────


def test_registers_to_bytes_word_order():
    """IEEE-754 float32 1.0 is 0x3F800000. Which register holds the high word is a
    vendor choice, and both must assemble to the same bytes."""
    assert registers_to_bytes([0x3F80, 0x0000], word_order="big").hex() == "3f800000"
    assert registers_to_bytes([0x0000, 0x3F80], word_order="little").hex() == "3f800000"


def test_registers_to_bytes_byte_order():
    """Some devices additionally byte-swap WITHIN each register."""
    assert registers_to_bytes([0x803F, 0x0000],
                              byte_order="little").hex() == "3f800000"


def test_bad_orders_are_rejected():
    with pytest.raises(PointMapError):
        registers_to_bytes([0x0000], word_order="middle")
    with pytest.raises(PointMapError):
        registers_to_bytes([0x0000], byte_order="sideways")


# ── Type decoding ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("registers,dtype,expected", [
    ([0x3F80, 0x0000], "float32", 1.0),
    ([0x4248, 0x0000], "float32", 50.0),
    ([0x0000, 0x0001], "uint32", 1.0),
    ([0x0001, 0x0000], "uint32", 65536.0),
    ([0xFE70], "int16", -400.0),
    ([0x0190], "int16", 400.0),
    ([0x0190], "uint16", 400.0),
    ([0xFFFF, 0xEC78], "int32", -5000.0),
    ([0x0000, 0x1388], "int32", 5000.0),
    ([0xFFFF], "sunssf", -1.0),
    ([0x0002], "sunssf", 2.0),
])
def test_decode_types(registers, dtype, expected):
    assert decode_registers(registers, dtype) == pytest.approx(expected)


def test_signed_export_read_as_unsigned_is_catastrophically_wrong():
    """THE bidirectional-meter trap, pinned so nobody 'simplifies' the type away.

    5 kW of solar export is −5000 W. Decoded as unsigned it is 4,294,962,296 — and a
    revenue meter reading 4.29 GW export would corrupt every financial figure the
    twin reports.
    """
    registers = [0xFFFF, 0xEC78]
    assert decode_registers(registers, "int32") == -5000.0
    assert decode_registers(registers, "uint32") == 4294962296.0


def test_not_implemented_sentinels_decode_to_none():
    """SunSpec's 'not implemented' markers. Decoded as data they become 65535 A or
    −32768 °C, which then pollutes an average and corrupts a whole rollup bucket."""
    assert decode_registers([0xFFFF], "uint16") is None
    assert decode_registers([0x8000], "int16") is None
    assert decode_registers([0xFFFF, 0xFFFF], "uint32") is None
    assert decode_registers([0x8000, 0x0000], "int32") is None


def test_accumulator_zero_is_real_data_not_a_sentinel():
    """acc32's sentinel is 0, which is ALSO a legitimate reading for a counter that
    has genuinely accumulated nothing — so it must not be treated as unavailable."""
    assert decode_registers([0x0000, 0x0000], "acc32") == 0.0


def test_nan_and_inf_decode_to_none():
    """A NaN on the wire is a device fault, not a measurement. Passed through, one
    NaN makes sum(), avg(), min() and max() NaN for its entire bucket."""
    assert decode_registers([0x7FC0, 0x0000], "float32") is None   # NaN
    assert decode_registers([0x7F80, 0x0000], "float32") is None   # +Inf
    assert decode_registers([0xFF80, 0x0000], "float32") is None   # -Inf


def test_unknown_type_and_short_read_are_rejected():
    with pytest.raises(PointMapError):
        decode_registers([0x0000], "float128")
    with pytest.raises(PointMapError):
        decode_registers([0x0000], "float32")      # needs 2 registers


# ── Scaling ─────────────────────────────────────────────────────────────────


def test_scale_and_offset():
    point = Point(signal="x", scale=0.1)
    assert point.apply_scaling(-400.0) == pytest.approx(-40.0)
    assert Point(signal="x", scale=0.1, offset=5.0).apply_scaling(
        400.0) == pytest.approx(45.0)


def test_sunspec_scale_factor_is_a_power_of_ten():
    """SunSpec puts the multiplier in a separate register as a base-10 exponent."""
    point = Point(signal="x")
    assert point.apply_scaling(5432, sf=-1) == pytest.approx(543.2)
    assert point.apply_scaling(5432, sf=0) == pytest.approx(5432.0)
    assert point.apply_scaling(543, sf=2) == pytest.approx(54300.0)


def test_invert_applies_the_platform_sign_convention():
    """A meter wired backwards is corrected at the edge, not downstream."""
    assert Point(signal="x", invert=True).apply_scaling(-5000.0) == 5000.0


def test_bit_extraction_for_bitfields():
    point = Point(signal="x", bit=3)
    assert point.apply_scaling(0b1000) == 1.0
    assert point.apply_scaling(0b0111) == 0.0


def test_range_check():
    point = Point(signal="x", min_value=0.0, max_value=100.0)
    assert point.in_range(50.0)
    assert not point.in_range(-1.0)
    assert not point.in_range(101.0)
    assert Point(signal="x").in_range(-1e9)        # unbounded when unset


def test_register_count_matches_type_width():
    assert Point(signal="x", data_type="uint16").register_count() == 1
    assert Point(signal="x", data_type="float32").register_count() == 2
    assert Point(signal="x", data_type="float64").register_count() == 4


# ── Deadband ────────────────────────────────────────────────────────────────


def test_deadband_suppresses_small_changes_and_passes_large_ones():
    f = DeadbandFilter()
    assert f.should_publish("a", "s", 50.0, 0.5, now=0.0)       # first always
    assert not f.should_publish("a", "s", 50.2, 0.5, now=1.0)   # inside band
    assert f.should_publish("a", "s", 50.9, 0.5, now=2.0)       # moved enough


def test_deadband_timeout_forces_a_heartbeat():
    """The property that makes a deadband safe to leave on.

    Without it, a flat signal produces silence — and silence is indistinguishable
    from a dead gateway to anything reading 'latest value'. That is exactly the
    distinction the twin exists to make, so a pure change-detector would be a bug.
    """
    f = DeadbandFilter()
    assert f.should_publish("a", "s", 50.0, 1.0, now=0.0, timeout_s=300.0)
    assert not f.should_publish("a", "s", 50.0, 1.0, now=100.0, timeout_s=300.0)
    assert f.should_publish("a", "s", 50.0, 1.0, now=301.0, timeout_s=300.0)


def test_deadband_is_per_asset_and_per_signal():
    f = DeadbandFilter()
    f.should_publish("inv-1", "solar:dcPower", 100.0, 10.0, now=0.0)
    # A different asset must not inherit the first one's baseline.
    assert f.should_publish("inv-2", "solar:dcPower", 100.0, 10.0, now=0.0)
    assert f.should_publish("inv-1", "solar:acPower", 100.0, 10.0, now=0.0)


def test_zero_deadband_publishes_everything():
    """State and fault codes carry deadband 0 — a state change is an EVENT and must
    never be filtered out."""
    f = DeadbandFilter()
    for i in range(5):
        assert f.should_publish("a", "state", 3.0, 0.0, now=float(i))


# ── Asset templating (§3 Step 1) ─────────────────────────────────────────────


def test_asset_template_expansion():
    assert expand_asset_template(
        "Zone_{zone:02d}_Inverter_{inverter:02d}_String_{index:02d}",
        zone=2, inverter=4, index=8) == "Zone_02_Inverter_04_String_08"


def test_asset_template_rejects_unknown_tokens():
    """A literal `{index}` reaching the historian would create an asset matching no
    3-D node, and the panel would simply never highlight — a silent failure that is
    very hard to trace back to a typo."""
    with pytest.raises(PointMapError):
        expand_asset_template("Zone_{missing}", zone=1)


# ── JSON path (MQTT) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    ("dc.voltage", 712.4),
    ("strings[0].current", 8.1),
    ("strings[2].current", 0.2),
    ("strings[9].current", None),
    ("missing.key", None),
    ("dc.missing", None),
])
def test_json_path(path, expected):
    payload = {"strings": [{"current": 8.1}, {"current": 7.9}, {"current": 0.2}],
               "dc": {"voltage": 712.4}}
    assert extract_json_path(payload, path) == expected


def test_json_path_never_raises_on_a_partial_payload():
    """Sparkplug sends only what CHANGED, so an absent field is normal. Raising would
    turn a normal partial payload into a connector crash loop."""
    for payload in ({}, [], None, 5, "text", {"a": None}):
        assert extract_json_path(payload, "a.b[0].c") is None


# ── Validation ──────────────────────────────────────────────────────────────


def _map(**overrides) -> PointMap:
    point = Point(signal="solar:dcPower", asset_id="inv-1", address=10,
                  data_type="int32", **overrides)
    return PointMap(name="t", protocol="modbus_tcp", points=[point])


def test_valid_map_passes():
    _map().validate()


def test_validation_rejects_unknown_signal_when_a_catalogue_is_given():
    """A typo here would silently create a series nothing reads."""
    pm = PointMap(name="t", protocol="modbus_tcp",
                  points=[Point(signal="solar:dcPowr", asset_id="a", address=1)])
    with pytest.raises(PointMapError, match="not a known signal"):
        pm.validate(known_signals={"solar:dcPower"})


def test_validation_rejects_duplicate_asset_signal_pairs():
    """Two points writing the same series would race, and which won would depend on
    poll order."""
    pm = PointMap(name="t", protocol="modbus_tcp", points=[
        Point(signal="solar:dcPower", asset_id="inv-1", address=1),
        Point(signal="solar:dcPower", asset_id="inv-1", address=99),
    ])
    with pytest.raises(PointMapError, match="duplicate"):
        pm.validate()


def test_validation_rejects_zero_scale():
    with pytest.raises(PointMapError, match="scale of 0"):
        _map(scale=0.0).validate()


def test_validation_rejects_inverted_range():
    with pytest.raises(PointMapError, match="min_value"):
        _map(min_value=100.0, max_value=10.0).validate()


def test_validation_rejects_bad_function_code():
    with pytest.raises(PointMapError, match="function_code"):
        _map(function_code=6).validate()          # 6 is a WRITE


def test_validation_requires_an_address_for_modbus():
    pm = PointMap(name="t", protocol="modbus_tcp",
                  points=[Point(signal="solar:dcPower", asset_id="a")])
    with pytest.raises(PointMapError, match="address"):
        pm.validate()


def test_validation_requires_a_node_id_for_opcua():
    pm = PointMap(name="t", protocol="opcua",
                  points=[Point(signal="solar:dcPower", asset_id="a")])
    with pytest.raises(PointMapError, match="node_id"):
        pm.validate()


def test_validation_rejects_an_empty_map_and_unknown_protocol():
    with pytest.raises(PointMapError, match="no points"):
        PointMap(name="t", protocol="modbus_tcp").validate()
    with pytest.raises(PointMapError, match="unknown protocol"):
        PointMap(name="t", protocol="carrier_pigeon",
                 points=[Point(signal="x", address=1)]).validate()


# ── Round trip ──────────────────────────────────────────────────────────────


def test_round_trip_through_dict():
    original = _map(word_order="little", scale=0.01, deadband=0.5)
    restored = PointMap.from_dict(original.to_dict())
    assert restored.protocol == original.protocol
    assert len(restored.points) == 1
    point = restored.points[0]
    assert point.signal == "solar:dcPower"
    assert point.word_order == "little"
    assert point.scale == 0.01
    assert point.deadband == 0.5


def test_from_dict_rejects_unknown_point_fields():
    """A misspelled `word_oder` that was silently dropped would leave the point on the
    default and decode wrongly — the exact failure this module is built to prevent."""
    with pytest.raises(PointMapError, match="unknown field"):
        PointMap.from_dict({
            "name": "t", "protocol": "modbus_tcp",
            "points": [{"signal": "x", "address": 1, "word_oder": "little"}],
        })


def test_from_dict_applies_map_level_order_defaults():
    """A vendor is consistent about word order across its whole map, so stating it
    once is both less to type and less to get wrong."""
    pm = PointMap.from_dict({
        "name": "t", "protocol": "modbus_tcp", "default_word_order": "little",
        "points": [{"signal": "x", "address": 1}],
    })
    assert pm.points[0].word_order == "little"
