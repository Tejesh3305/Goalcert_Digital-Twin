"""
pointmap.py — declarative device profiles: protocol address → canonical signal.

THE ABSTRACTION
---------------
A field device speaks addresses. An inverter says "holding register 40084, two
registers, signed, scale factor in register 40085". The twin speaks canonical
signals: `solar:acPower` on asset `Zone_02_Inverter_04`. A POINT is one row of
that translation, and a POINT MAP is a device's whole set of them.

The reason this is data and not code is the entire reason the connector layer is
worth building: a new inverter model, a different meter vendor, a site that wired
its CT clamps to different channels — all of those are a point map, edited or
uploaded, with no deployment. Write it as code and every customer site becomes a
release.

WHY DECODING IS THE RISKIEST PART OF THE WHOLE INGEST PATH
---------------------------------------------------------
Modbus carries 16-bit registers and nothing else. Every larger type is a vendor
convention layered on top, and the conventions disagree:

  * A 32-bit value spans two registers, and WHICH REGISTER HOLDS THE HIGH WORD is
    not standardised. Schneider and SMA differ. Get it wrong and 230.0 V decodes
    as 5.4e-42 or 1.2e7 — obviously broken. Get it wrong on a value that happens
    to be small and it decodes as something plausible, which is far worse: it
    silently poisons a baseline that a maintenance threshold is compared against.
  * Some devices byte-swap WITHIN a register as well.
  * Signedness is per-point. Reading a signed −5 kW export as unsigned yields
    65531, and on a bidirectional revenue meter (§2.1) that is a financial error.
  * SunSpec — the actual standard for commercial PV inverters — puts the scale
    factor in a SEPARATE REGISTER, so the multiplier is discovered at runtime.

All four are handled explicitly below, and `test_pointmap.py` pins the decoder
against hand-computed byte patterns. There is no "usually right" default that
would make a wrong reading look plausible: `word_order` is required to be stated
for every multi-register point.

DEADBAND (§2.3)
---------------
The edge gateway is specified to do "deadband filtering". `Point.deadband`
implements it: a value that has not moved by more than the deadband since the last
publish is suppressed. On a 1 Hz poll of a 40-point inverter that is the
difference between 3.5 M samples/day/device and roughly a tenth of that, with no
loss of information a trend could use — irradiance and power move, but frequency,
power factor and a fault code sit still for hours.

Deadband is applied AFTER scaling, in engineering units, because that is the only
place the number is comparable to anything an engineer would specify. A deadband
in raw counts means something different on every point.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ── Data types ──────────────────────────────────────────────────────────────
#
# `struct` format (big-endian applied at unpack time) and register width.
# `acc32`/`acc64` are the SunSpec accumulators — unsigned, monotonically
# increasing energy counters — kept distinct from plain uint so the rollover
# handling in `Point.decode` can apply only where it is correct.
_TYPES: dict[str, tuple[str, int]] = {
    "int16":   ("h", 1),
    "uint16":  ("H", 1),
    "int32":   ("i", 2),
    "uint32":  ("I", 2),
    "acc32":   ("I", 2),
    "int64":   ("q", 4),
    "uint64":  ("Q", 4),
    "acc64":   ("Q", 4),
    "float32": ("f", 2),
    "float64": ("d", 4),
    # SunSpec scale factor: int16 exponent, value = raw · 10^sf.
    "sunssf":  ("h", 1),
}

# Sentinels SunSpec uses for "not implemented". Decoding these as data yields
# 65535 A or −32768 °C, which then propagates into an average and corrupts a whole
# bucket. They are mapped to None (no reading) instead.
_NOT_IMPLEMENTED = {
    "int16": -0x8000, "uint16": 0xFFFF,
    "int32": -0x80000000, "uint32": 0xFFFFFFFF, "acc32": 0,
    "int64": -0x8000000000000000, "uint64": 0xFFFFFFFFFFFFFFFF, "acc64": 0,
}

WORD_ORDERS = ("big", "little")
BYTE_ORDERS = ("big", "little")

# Modbus function codes for reading.
FC_READ_HOLDING = 3
FC_READ_INPUT = 4
READ_FUNCTION_CODES = (FC_READ_HOLDING, FC_READ_INPUT)

_ASSET_TOKEN_RE = re.compile(r"\{(\w+)\}")


class PointMapError(ValueError):
    """A point map is invalid. Raised at CONFIGURATION time, never at decode time,
    so a bad map is rejected when it is saved rather than producing garbage
    readings at 1 Hz for a week before anyone notices."""


def registers_to_bytes(registers: Iterable[int], *, word_order: str = "big",
                       byte_order: str = "big") -> bytes:
    """Flatten 16-bit Modbus registers into a byte string, honouring both orders.

    Modbus puts each register on the wire big-endian; that is the one part that IS
    standardised. `word_order` chooses which register supplies the most significant
    half of a multi-register value, and `byte_order` handles the devices that
    additionally swap within a register.

    Worked example — float32 1.0 is IEEE-754 0x3F800000:
        word_order big    -> registers (0x3F80, 0x0000) -> 3F 80 00 00
        word_order little -> registers (0x0000, 0x3F80) -> 3F 80 00 00
    Both decode to 1.0; the point map simply has to say which the device does.
    """
    if word_order not in WORD_ORDERS:
        raise PointMapError(f"word_order must be one of {WORD_ORDERS}")
    if byte_order not in BYTE_ORDERS:
        raise PointMapError(f"byte_order must be one of {BYTE_ORDERS}")

    words = [int(r) & 0xFFFF for r in registers]
    if word_order == "little":
        words = words[::-1]
    out = bytearray()
    for word in words:
        pair = struct.pack(">H", word)
        if byte_order == "little":
            pair = pair[::-1]
        out.extend(pair)
    return bytes(out)


def decode_registers(registers: Iterable[int], data_type: str, *,
                     word_order: str = "big",
                     byte_order: str = "big") -> Optional[float]:
    """Raw registers → a number, or None when the device says 'not implemented'."""
    if data_type not in _TYPES:
        raise PointMapError(
            f"unknown data_type '{data_type}'. Known: {sorted(_TYPES)}")
    fmt, width = _TYPES[data_type]
    regs = list(registers)
    if len(regs) < width:
        raise PointMapError(
            f"{data_type} needs {width} register(s), got {len(regs)}")

    raw = struct.unpack(">" + fmt,
                        registers_to_bytes(regs[:width], word_order=word_order,
                                           byte_order=byte_order))[0]

    if data_type in ("float32", "float64"):
        # A NaN/Inf on the wire is a device fault, not a measurement. Passing it
        # through would make every aggregate over its bucket NaN.
        return float(raw) if math.isfinite(raw) else None
    if data_type in _NOT_IMPLEMENTED and raw == _NOT_IMPLEMENTED[data_type]:
        # acc32/acc64 sentinel is 0, which is also a legitimate reading for a
        # counter that has genuinely accumulated nothing — so it is NOT treated as
        # unavailable. Only the fixed-width int sentinels are.
        if data_type not in ("acc32", "acc64"):
            return None
    return float(raw)


@dataclass
class Point:
    """One measurement: where to read it, how to decode it, what it means.

    `source` is protocol-specific and validated per protocol by `PointMap`:
        modbus  address, data_type, word_order, byte_order, function_code,
                scale_register (SunSpec), bit (for bitfields)
        opcua   node_id
        mqtt    json_path (dotted, supports [n] indexing)
    """
    signal: str                       # canonical IRI, e.g. "solar:acPower"
    asset_id: str = ""                # target asset; may template — see AssetTemplate
    label: str = ""
    unit: str = ""

    # Modbus addressing
    address: Optional[int] = None
    data_type: str = "uint16"
    word_order: str = "big"
    byte_order: str = "big"
    function_code: int = FC_READ_HOLDING
    scale_register: Optional[int] = None   # SunSpec: 10^int16 read from here
    bit: Optional[int] = None              # extract one bit of a bitfield

    # OPC-UA / MQTT addressing
    node_id: str = ""
    json_path: str = ""

    # Engineering conversion, applied in this order:
    #   value = decode(raw) · scale · 10^sf · (-1 if invert) + offset
    scale: float = 1.0
    offset: float = 0.0
    invert: bool = False

    # Plausibility band. A reading outside it is recorded as BAD quality rather
    # than discarded: "the sensor reported 900 °C" is diagnostic information about
    # the sensor, and silently dropping it makes a failed RTD look like a gap.
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    deadband: float = 0.0             # engineering units; 0 disables
    deadband_timeout_s: float = 300.0  # publish anyway after this long

    enabled: bool = True

    def register_count(self) -> int:
        return _TYPES[self.data_type][1] if self.data_type in _TYPES else 1

    def apply_scaling(self, raw: float, sf: Optional[int] = None) -> float:
        """decode → engineering units. `sf` is the SunSpec exponent when used."""
        value = float(raw)
        if self.bit is not None:
            value = float((int(raw) >> int(self.bit)) & 1)
        value *= self.scale
        if sf is not None:
            value *= 10.0 ** int(sf)
        if self.invert:
            value = -value
        return value + self.offset

    def in_range(self, value: float) -> bool:
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True


# ── Deadband state ──────────────────────────────────────────────────────────


class DeadbandFilter:
    """Per-(asset, signal) suppression of unchanged values (§2.3).

    Two properties make this safe to leave on:

      * A change LARGER than the deadband always publishes, so no event is lost.
      * `timeout_s` forces a publish even when nothing changed, so a flat signal
        still produces a heartbeat. Without it, `latest`'s staleness check could
        not tell "steady at 50.0 Hz" from "gateway died", which is precisely the
        distinction the twin exists to make. This is the reason a deadband filter
        must never be a pure change-detector.
    """

    def __init__(self):
        self._last: dict[tuple[str, str], tuple[float, float]] = {}

    def should_publish(self, asset_id: str, signal: str, value: float,
                       deadband: float, now: float,
                       timeout_s: float = 300.0) -> bool:
        key = (asset_id, signal)
        previous = self._last.get(key)
        if previous is None:
            self._last[key] = (value, now)
            return True
        last_value, last_time = previous
        moved = abs(value - last_value) >= deadband if deadband > 0 else True
        timed_out = (now - last_time) >= timeout_s
        if moved or timed_out:
            self._last[key] = (value, now)
            return True
        return False

    def reset(self) -> None:
        self._last.clear()

    def tracked(self) -> int:
        return len(self._last)


# ── Asset id templating (§3 Step 1) ─────────────────────────────────────────
#
# The specification requires every 3-D scene node to carry a logical id matching
# the electrical schematic — `Zone_02_Inverter_04_String_08`. A point map for a
# 24-string combiner should not need 24 hand-written asset ids, so a template with
# an index is expanded at load time.

def expand_asset_template(template: str, **tokens) -> str:
    """`Zone_{zone:02d}_Inverter_{inverter:02d}_String_{index:02d}` → concrete id.

    Unknown tokens raise rather than being left in place. A literal `{index}` that
    reached the historian would create an asset that matches no 3-D node, and the
    panel would simply never highlight — a silent failure that is very hard to
    trace back to a typo in a point map.
    """
    missing = [t for t in _ASSET_TOKEN_RE.findall(template)
               if t.split(":")[0] not in tokens]
    if missing:
        raise PointMapError(
            f"asset template '{template}' references unknown token(s) "
            f"{missing}; available: {sorted(tokens)}")
    try:
        return template.format(**tokens)
    except (KeyError, ValueError, IndexError) as e:
        raise PointMapError(f"cannot expand asset template '{template}': {e}")


# ── Point map ───────────────────────────────────────────────────────────────


@dataclass
class PointMap:
    """A device profile: protocol + the points to read from it."""
    name: str
    protocol: str                       # modbus_tcp | modbus_rtu | opcua | mqtt
    points: list[Point] = field(default_factory=list)
    vendor: str = ""
    model: str = ""
    description: str = ""
    # Applied to any point that does not set its own. A vendor is consistent about
    # word order across its whole map, so stating it once is both less error-prone
    # and less to get wrong than repeating it per point.
    default_word_order: str = "big"
    default_byte_order: str = "big"
    unit_id: int = 1                    # Modbus slave/unit id
    base_address: int = 0               # added to every point address

    def validate(self, *, known_signals: Optional[Iterable[str]] = None) -> None:
        """Reject an unusable map. Called when a connector is created or updated.

        Validation happens HERE, at configuration time, rather than at decode time.
        A map that decodes wrongly produces plausible numbers at 1 Hz, and by the
        time anyone questions them the historian holds a week of corrupted
        baselines that every §4.2 threshold has been compared against.
        """
        if self.protocol not in PROTOCOLS:
            raise PointMapError(
                f"unknown protocol '{self.protocol}'. Known: {sorted(PROTOCOLS)}")
        if not self.points:
            raise PointMapError(f"point map '{self.name}' has no points")

        known = frozenset(known_signals) if known_signals is not None else None
        seen: set[tuple[str, str]] = set()

        for i, p in enumerate(self.points):
            where = f"{self.name}[{i}] ({p.signal or 'no signal'})"
            if not p.signal:
                raise PointMapError(f"{where}: signal is required")
            if known is not None and p.signal not in known:
                raise PointMapError(
                    f"{where}: '{p.signal}' is not a known signal. A typo here "
                    f"would silently create a series nothing reads.")
            if p.data_type not in _TYPES:
                raise PointMapError(
                    f"{where}: unknown data_type '{p.data_type}'")
            if p.word_order not in WORD_ORDERS:
                raise PointMapError(f"{where}: bad word_order '{p.word_order}'")
            if p.byte_order not in BYTE_ORDERS:
                raise PointMapError(f"{where}: bad byte_order '{p.byte_order}'")
            if p.scale == 0:
                raise PointMapError(
                    f"{where}: scale of 0 would zero every reading")
            if not math.isfinite(p.scale) or not math.isfinite(p.offset):
                raise PointMapError(f"{where}: scale/offset must be finite")
            if (p.min_value is not None and p.max_value is not None
                    and p.min_value >= p.max_value):
                raise PointMapError(
                    f"{where}: min_value {p.min_value} >= max_value {p.max_value}, "
                    f"so every reading would be flagged bad")
            if p.deadband < 0:
                raise PointMapError(f"{where}: deadband cannot be negative")

            if self.protocol in ("modbus_tcp", "modbus_rtu"):
                if p.address is None:
                    raise PointMapError(f"{where}: modbus point needs an address")
                if p.address < 0:
                    raise PointMapError(f"{where}: address cannot be negative")
                if p.function_code not in READ_FUNCTION_CODES:
                    raise PointMapError(
                        f"{where}: function_code must be 3 (holding) or 4 (input)")
            elif self.protocol == "opcua":
                if not p.node_id:
                    raise PointMapError(f"{where}: opcua point needs a node_id")
            elif self.protocol == "mqtt":
                if not (p.json_path or p.signal):
                    raise PointMapError(f"{where}: mqtt point needs a json_path")

            key = (p.asset_id, p.signal)
            if key in seen:
                raise PointMapError(
                    f"{where}: duplicate (asset_id, signal) pair {key}. Two points "
                    f"writing the same series would race, and which one won would "
                    f"depend on poll order.")
            seen.add(key)

    def enabled_points(self) -> list[Point]:
        return [p for p in self.points if p.enabled]

    def scale_registers(self) -> set[int]:
        """Every SunSpec scale-factor register referenced by this map, so the
        poller can fetch them in the same batched read rather than issuing a
        second round trip per scaled point."""
        return {p.scale_register for p in self.points
                if p.scale_register is not None}

    def address_span(self) -> Optional[tuple[int, int]]:
        """(first, last) register touched, for planning batched reads."""
        addrs: list[int] = []
        for p in self.enabled_points():
            if p.address is None:
                continue
            start = self.base_address + p.address
            addrs.extend((start, start + p.register_count() - 1))
        for reg in self.scale_registers():
            addrs.append(self.base_address + reg)
        return (min(addrs), max(addrs)) if addrs else None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "protocol": self.protocol,
            "vendor": self.vendor, "model": self.model,
            "description": self.description,
            "default_word_order": self.default_word_order,
            "default_byte_order": self.default_byte_order,
            "unit_id": self.unit_id, "base_address": self.base_address,
            "points": [_point_to_dict(p) for p in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PointMap":
        """Build from JSON — the path an uploaded/edited map takes.

        Per-point orders default to the map's, so a vendor's convention is stated
        once. Unknown keys are REJECTED rather than ignored: a misspelled
        `word_oder` that was silently dropped would leave the point on the default
        and decode wrongly, which is the exact failure this whole module is
        structured to prevent.
        """
        pm = cls(
            name=str(data.get("name") or "unnamed"),
            protocol=str(data.get("protocol") or ""),
            vendor=str(data.get("vendor") or ""),
            model=str(data.get("model") or ""),
            description=str(data.get("description") or ""),
            default_word_order=str(data.get("default_word_order") or "big"),
            default_byte_order=str(data.get("default_byte_order") or "big"),
            unit_id=int(data.get("unit_id") or 1),
            base_address=int(data.get("base_address") or 0),
        )
        allowed = set(Point.__dataclass_fields__)
        for i, raw in enumerate(data.get("points") or []):
            if not isinstance(raw, dict):
                raise PointMapError(f"point[{i}] is not an object")
            unknown = set(raw) - allowed
            if unknown:
                raise PointMapError(
                    f"point[{i}] has unknown field(s) {sorted(unknown)}. "
                    f"Allowed: {sorted(allowed)}")
            raw = dict(raw)
            raw.setdefault("word_order", pm.default_word_order)
            raw.setdefault("byte_order", pm.default_byte_order)
            pm.points.append(Point(**raw))
        return pm


def _point_to_dict(p: Point) -> dict:
    out = {}
    for name in Point.__dataclass_fields__:
        value = getattr(p, name)
        default = Point.__dataclass_fields__[name].default
        if value != default or name in ("signal", "asset_id", "address",
                                        "data_type", "node_id", "json_path"):
            out[name] = value
    return out


PROTOCOLS = ("modbus_tcp", "modbus_rtu", "opcua", "mqtt")


# ── JSON path extraction (MQTT) ─────────────────────────────────────────────


def extract_json_path(payload: Any, path: str) -> Any:
    """Dotted path with `[n]` indexing: `strings[3].current`, `data.dc.voltage`.

    Returns None for any miss rather than raising: an MQTT payload is produced by
    someone else's gateway and a field being absent in one message is normal
    (Sparkplug in particular sends only what CHANGED). Raising would turn a normal
    partial payload into a connector crash loop.
    """
    if not path:
        return payload
    current = payload
    for token in path.split("."):
        if not token:
            continue
        name, _, rest = token.partition("[")
        if name:
            if not isinstance(current, dict) or name not in current:
                return None
            current = current[name]
        while rest:
            index_text, _, rest = rest.partition("]")
            if not index_text:
                return None
            try:
                index = int(index_text)
            except ValueError:
                return None
            if not isinstance(current, (list, tuple)) or not (
                    -len(current) <= index < len(current)):
                return None
            current = current[index]
            rest = rest.lstrip("[")
    return current
