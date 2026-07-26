"""
modbus.py — Modbus TCP and RTU connector (§2.1, §2.3).

Covers the inverters, string combiners, revenue meters, CT clamps and weather
stations: on a real solar site the overwhelming majority of field devices speak
Modbus and nothing else.

BATCHED READS ARE NOT AN OPTIMISATION HERE
------------------------------------------
Modbus is request/response over one connection with no pipelining, so a read costs
a full round trip. A 40-point inverter polled point-by-point is 40 round trips; at
a realistic 15 ms RTT over a site LAN that is 600 ms, and the spec asks for 1 Hz
(§2.3). Add a second inverter on the same RS-485 segment — where the round trip is
30-80 ms because the line is 9600 baud — and point-by-point polling simply cannot
meet the specified rate.

So `_plan_blocks` coalesces the point addresses into the fewest contiguous reads,
tolerating small gaps because reading four unwanted registers is far cheaper than a
second round trip. That takes the same inverter to 2-3 requests. This is why the
poller is built around an address plan rather than a list of points.

RTU AND TCP SHARE EVERYTHING ABOVE THE TRANSPORT
------------------------------------------------
§2.1 requires support for both "Modbus TCP or RS-485 (Modbus RTU)". They differ
only in how the frame is carried, so one class handles both and the config picks
the client. RTU additionally serialises per SEGMENT — several devices share one
physical pair, and two masters talking at once corrupts both frames — hence
`_SERIAL_LOCKS`.

WHAT `discover_sunspec` IS FOR
-----------------------------
SunSpec point offsets are relative to their model's data block, and its location
differs per device. Guessing it is the single most dangerous mistake available on
this path: an offset wrong by a few registers still decodes into plausible small
integers, because neighbouring SunSpec points are all scaled integers. The device
looks like it is working and every number is wrong.

`discover_sunspec` walks the model chain the way the specification says a client
must, and returns the real base address for each model found. `profiles.
sunspec_inverter_3ph` therefore REQUIRES base_address rather than defaulting it.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from historian import QUALITY_BAD, QUALITY_GOOD

from .base import Connector, ConnectorConfig, MissingDependency, register_connector
from .pointmap import Point, decode_registers

log = logging.getLogger("nxr.connectors.modbus")

# Modbus caps a single read at 125 registers (250 bytes of payload). Exceeding it
# is an illegal-data-address exception from a strict device and, worse, silent
# truncation from a lenient one.
MAX_REGISTERS_PER_READ = 120

# Registers of padding tolerated inside one batched read. Reading a few unwanted
# registers costs bytes; a second request costs a whole round trip, which on a
# 9600-baud RS-485 segment is ~30-80 ms. 16 is comfortably on the right side of
# that trade for every device layout in `profiles.py`.
MAX_GAP_REGISTERS = 16

# One lock per serial device: an RS-485 segment is a shared bus and two threads
# transmitting simultaneously corrupts both frames. Keyed by port name because
# that is what identifies the physical line.
_SERIAL_LOCKS: dict[str, threading.Lock] = {}
_SERIAL_LOCKS_GUARD = threading.Lock()


def _serial_lock(port: str) -> threading.Lock:
    with _SERIAL_LOCKS_GUARD:
        if port not in _SERIAL_LOCKS:
            _SERIAL_LOCKS[port] = threading.Lock()
        return _SERIAL_LOCKS[port]


@register_connector
class ModbusConnector(Connector):
    """Modbus TCP / RTU poller driven by a PointMap."""

    protocol = "modbus_tcp"

    def __init__(self, config: ConnectorConfig, **kwargs):
        super().__init__(config, **kwargs)
        self._client = None
        self._blocks: list[tuple[int, int, int]] = []   # (fc, start, count)
        if config.point_map is not None:
            self._blocks = _plan_blocks(config.point_map)

    # ── Transport ───────────────────────────────────────────────────────────

    def _is_rtu(self) -> bool:
        return (self.config.protocol == "modbus_rtu"
                or bool(self.config.serial_port))

    def connect(self) -> None:
        try:
            if self._is_rtu():
                from pymodbus.client import ModbusSerialClient
                if not self.config.serial_port:
                    raise ValueError(
                        "modbus_rtu requires serial_port (e.g. COM3 or /dev/ttyUSB0)")
                self._client = ModbusSerialClient(
                    port=self.config.serial_port,
                    baudrate=int(self.config.baudrate),
                    parity=self.config.parity or "N",
                    stopbits=int(self.config.stopbits),
                    bytesize=int(self.config.bytesize),
                    timeout=float(self.config.timeout_s),
                )
            else:
                from pymodbus.client import ModbusTcpClient
                if not self.config.host:
                    raise ValueError("modbus_tcp requires host")
                self._client = ModbusTcpClient(
                    host=self.config.host,
                    port=int(self.config.port or 502),
                    timeout=float(self.config.timeout_s),
                )
        except ImportError as e:
            raise MissingDependency(
                self.config.protocol, "pymodbus>=3.6",
                "Modbus TCP and RTU client") from e

        if not self._client.connect():
            target = (self.config.serial_port if self._is_rtu()
                      else f"{self.config.host}:{self.config.port or 502}")
            raise ConnectionError(f"cannot reach Modbus device at {target}")

    def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    # ── Reading ─────────────────────────────────────────────────────────────

    def _read_block(self, function_code: int, start: int, count: int) -> list[int]:
        """One batched register read, with retries.

        Retries here rather than only in the outer reconnect loop because a single
        dropped RTU frame (electrical noise on a long run is normal, not
        exceptional) should cost one retry, not a full reconnect and a backoff.
        """
        client = self._client
        if client is None:
            raise ConnectionError("not connected")

        attempts = max(1, int(self.config.request_retries) + 1)
        last_error: Optional[Exception] = None
        for _ in range(attempts):
            try:
                kwargs = {"address": start, "count": count,
                          "slave": self.config.point_map.unit_id
                          if self.config.point_map else 1}
                if function_code == 4:
                    result = client.read_input_registers(**kwargs)
                else:
                    result = client.read_holding_registers(**kwargs)
                if result is None:
                    raise IOError("no response")
                if hasattr(result, "isError") and result.isError():
                    raise IOError(f"Modbus exception: {result}")
                registers = list(getattr(result, "registers", []) or [])
                if len(registers) != count:
                    raise IOError(
                        f"short read at {start}: expected {count} registers, "
                        f"got {len(registers)}")
                return registers
            except Exception as e:                      # noqa: BLE001
                last_error = e
        raise IOError(f"read fc={function_code} @{start}+{count} failed: "
                      f"{last_error}") from last_error

    def read_once(self) -> list[tuple[Point, Optional[float], int]]:
        point_map = self.config.point_map
        if point_map is None:
            return []

        lock = (_serial_lock(self.config.serial_port) if self._is_rtu()
                else None)
        if lock is not None:
            lock.acquire()
        try:
            cache: dict[tuple[int, int], int] = {}
            failed_blocks: list[tuple[int, int, int]] = []
            for function_code, start, count in self._blocks:
                try:
                    registers = self._read_block(function_code, start, count)
                except Exception as e:
                    # A per-block failure marks only its own points BAD. One
                    # unreadable region (a vendor gap, an optional model) must not
                    # blind the rest of the device.
                    failed_blocks.append((function_code, start, count))
                    log.debug("connector %s block fc=%d @%d+%d failed: %s",
                              self.config.connector_id, function_code, start,
                              count, e)
                    continue
                for offset, value in enumerate(registers):
                    cache[(function_code, start + offset)] = value
            if len(failed_blocks) == len(self._blocks) and self._blocks:
                # Everything failed: the device is gone, not merely partly
                # unreadable. Raise so the run loop reconnects with backoff.
                raise IOError(
                    f"all {len(self._blocks)} register blocks failed — device "
                    f"unreachable")
            return self._decode(point_map, cache)
        finally:
            if lock is not None:
                lock.release()

    def _decode(self, point_map, cache: dict[tuple[int, int], int]):
        """Registers → engineering values, resolving SunSpec scale factors."""
        base = point_map.base_address
        out: list[tuple[Point, Optional[float], int]] = []

        # Resolve every scale-factor register once. They are int16 exponents and
        # several points share one, so decoding per point would be wasteful and —
        # worse — could disagree if a retry returned a different value mid-cycle.
        scale_factors: dict[int, Optional[int]] = {}
        for register in point_map.scale_registers():
            raw = cache.get((3, base + register), cache.get((4, base + register)))
            if raw is None:
                scale_factors[register] = None
                continue
            value = decode_registers([raw], "sunssf")
            scale_factors[register] = None if value is None else int(value)

        for point in point_map.enabled_points():
            if point.address is None:
                continue
            start = base + point.address
            width = point.register_count()
            registers = [cache.get((point.function_code, start + i))
                         for i in range(width)]
            if any(r is None for r in registers):
                out.append((point, None, QUALITY_BAD))
                continue

            try:
                raw = decode_registers(registers, point.data_type,
                                       word_order=point.word_order,
                                       byte_order=point.byte_order)
            except Exception as e:
                log.debug("decode failed for %s: %s", point.signal, e)
                out.append((point, None, QUALITY_BAD))
                continue
            if raw is None:                     # device said 'not implemented'
                out.append((point, None, QUALITY_BAD))
                continue

            sf = None
            if point.scale_register is not None:
                sf = scale_factors.get(point.scale_register)
                if sf is None:
                    # The multiplier is unknown, so the magnitude is unknown. A
                    # value published without its scale factor is off by a power of
                    # ten — which on DC power is the difference between a fault and
                    # a normal afternoon. BAD is the only honest answer.
                    out.append((point, None, QUALITY_BAD))
                    continue

            out.append((point, point.apply_scaling(raw, sf=sf), QUALITY_GOOD))
        return out


@register_connector
class ModbusRtuConnector(ModbusConnector):
    """RS-485 Modbus RTU. Same logic; registered so the protocol is selectable
    and `available_protocols()` reports it separately."""
    protocol = "modbus_rtu"


# ── Read planning ───────────────────────────────────────────────────────────


def _plan_blocks(point_map) -> list[tuple[int, int, int]]:
    """Coalesce point addresses into the fewest contiguous reads.

    Grouped by function code first — holding (3) and input (4) registers are
    SEPARATE address spaces, and merging them would read register 40072 of the
    wrong space and return a plausible-looking wrong number.
    """
    wanted: dict[int, set[int]] = {}
    base = point_map.base_address

    for point in point_map.enabled_points():
        if point.address is None:
            continue
        start = base + point.address
        for i in range(point.register_count()):
            wanted.setdefault(point.function_code, set()).add(start + i)
        if point.scale_register is not None:
            wanted.setdefault(point.function_code, set()).add(
                base + point.scale_register)

    blocks: list[tuple[int, int, int]] = []
    for function_code, addresses in wanted.items():
        ordered = sorted(addresses)
        if not ordered:
            continue
        run_start = previous = ordered[0]
        for address in ordered[1:]:
            gap = address - previous - 1
            span = address - run_start + 1
            if gap > MAX_GAP_REGISTERS or span > MAX_REGISTERS_PER_READ:
                blocks.append((function_code, run_start, previous - run_start + 1))
                run_start = address
            previous = address
        blocks.append((function_code, run_start, previous - run_start + 1))
    return sorted(blocks, key=lambda b: (b[0], b[1]))


# ── SunSpec discovery ───────────────────────────────────────────────────────

SUNSPEC_MARKER = 0x53756E53          # "SunS" as a 32-bit big-endian value
# The three base addresses the SunSpec specification permits, in the order a
# client is expected to try them.
SUNSPEC_BASES = (40000, 50000, 0)


def discover_sunspec(host: str, *, port: int = 502, unit_id: int = 1,
                     timeout_s: float = 3.0) -> dict:
    """Walk a device's SunSpec model chain and report where each model lives.

    This is what makes `base_address` a discovered fact instead of a guess. The
    walk follows the specification:

        1. find the "SunS" marker at 40000, 50000 or 0
        2. read the model id and length that follow it
        3. the next model's header sits at (current data start + length)
        4. stop at model id 0xFFFF (end of chain)

    Returns the concrete `data_address` for every model found — exactly the value
    `profiles.sunspec_inverter_3ph(base_address=...)` needs.

    Uses its own short-lived client rather than a running connector's, so an
    operator can probe a device during commissioning without starting one.
    """
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError as e:
        raise MissingDependency("modbus_tcp", "pymodbus>=3.6") from e

    client = ModbusTcpClient(host=host, port=int(port), timeout=float(timeout_s))
    if not client.connect():
        return {"ok": False, "error": f"cannot reach {host}:{port}"}

    def read(address: int, count: int) -> Optional[list[int]]:
        try:
            result = client.read_holding_registers(
                address=address, count=count, slave=unit_id)
            if result is None or (hasattr(result, "isError") and result.isError()):
                return None
            registers = list(getattr(result, "registers", []) or [])
            return registers if len(registers) == count else None
        except Exception:
            return None

    try:
        marker_base = None
        for candidate in SUNSPEC_BASES:
            registers = read(candidate, 2)
            if registers and ((registers[0] << 16) | registers[1]) == SUNSPEC_MARKER:
                marker_base = candidate
                break
        if marker_base is None:
            return {"ok": False,
                    "error": ("no SunSpec 'SunS' marker at 40000, 50000 or 0 — the "
                              "device is not SunSpec-compliant, so it needs a "
                              "vendor-specific point map rather than the SunSpec "
                              "profile.")}

        models = []
        cursor = marker_base + 2                  # first model header
        for _ in range(64):                       # bounded: never trust a device's chain
            header = read(cursor, 2)
            if not header:
                break
            model_id, length = header[0], header[1]
            if model_id == 0xFFFF:                # documented end of chain
                break
            data_address = cursor + 2
            models.append({
                "model_id": model_id,
                "length": length,
                "header_address": cursor,
                "data_address": data_address,
                "label": SUNSPEC_MODEL_LABELS.get(model_id, f"model {model_id}"),
            })
            cursor = data_address + length
            if length <= 0:
                break                             # malformed; stop rather than loop

        inverter = next((m for m in models if m["model_id"] in (101, 102, 103,
                                                                111, 112, 113)),
                        None)
        return {
            "ok": True, "marker_base": marker_base, "models": models,
            "inverter_model": inverter,
            "base_address": inverter["data_address"] if inverter else None,
            "hint": ("Pass base_address to profiles.sunspec_inverter_3ph(). Do not "
                     "hard-code it — it differs per device and a wrong offset "
                     "decodes into plausible but wrong values."),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


SUNSPEC_MODEL_LABELS = {
    1: "Common (manufacturer, model, serial)",
    101: "Inverter, single phase (int+SF)",
    102: "Inverter, split phase (int+SF)",
    103: "Inverter, three phase (int+SF)",
    111: "Inverter, single phase (float)",
    112: "Inverter, split phase (float)",
    113: "Inverter, three phase (float)",
    120: "Nameplate ratings",
    121: "Basic settings",
    122: "Measurements/status",
    123: "Immediate controls",
    124: "Storage",
    126: "Static volt-VAR",
    160: "Multiple MPPT inverter extension",
    201: "Meter, single phase",
    203: "Meter, three phase wye",
    204: "Meter, three phase delta",
    501: "String combiner (basic)",
    502: "String combiner (advanced)",
    701: "DER AC measurement",
    801: "Energy storage base",
}


def read_sunspec_common(host: str, *, port: int = 502, unit_id: int = 1,
                        timeout_s: float = 3.0) -> dict:
    """Manufacturer / model / serial from SunSpec Common Model (model 1).

    Worth its own call during commissioning: it confirms the device is the one the
    drawings say it is BEFORE anyone binds a point map to it. Layout of model 1:
    Mn (16 registers, string), Md (16), Opt (8), Vr (8), SN (16), DA (1).
    """
    chain = discover_sunspec(host, port=port, unit_id=unit_id, timeout_s=timeout_s)
    if not chain.get("ok"):
        return chain
    common = next((m for m in chain["models"] if m["model_id"] == 1), None)
    if common is None:
        return {"ok": False, "error": "device has no SunSpec Common Model"}

    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError as e:
        raise MissingDependency("modbus_tcp", "pymodbus>=3.6") from e

    client = ModbusTcpClient(host=host, port=int(port), timeout=float(timeout_s))
    if not client.connect():
        return {"ok": False, "error": f"cannot reach {host}:{port}"}
    try:
        result = client.read_holding_registers(
            address=common["data_address"], count=min(66, common["length"]),
            slave=unit_id)
        if result is None or (hasattr(result, "isError") and result.isError()):
            return {"ok": False, "error": "Common Model read failed"}
        registers = list(result.registers)

        def text(offset: int, length: int) -> str:
            chunk = registers[offset:offset + length]
            raw = b"".join(r.to_bytes(2, "big") for r in chunk)
            return raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()

        return {"ok": True, "manufacturer": text(0, 16), "model": text(16, 16),
                "options": text(32, 8), "version": text(40, 8),
                "serial": text(48, 16),
                "device_address": registers[64] if len(registers) > 64 else None}
    finally:
        try:
            client.close()
        except Exception:
            pass
