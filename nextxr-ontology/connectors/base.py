"""
base.py — the connector plugin contract.

Every protocol adapter (Modbus, OPC-UA, MQTT) implements `Connector` and gets, for
free and identically: validation, deadband filtering, quality assignment, unit
stamping, submission through the one ingest pipeline, reconnect backoff, and health
reporting. A new protocol is ~150 lines of "how do I read a value", never a second
implementation of what a reading MEANS.

That split is the whole architectural point. The alternative — each connector
writing to the historian itself — is how a platform acquires per-protocol
data-quality bugs that reproduce on exactly one customer's site because only they
speak Modbus RTU over a radio link.

QUALITY IS ASSIGNED HERE, ONCE
------------------------------
A reading arrives with a value and a story about how trustworthy it is. Both are
needed downstream, and only this layer knows both:

    GOOD (192)      decoded cleanly and inside the point's plausibility band
    UNCERTAIN (64)  decoded, but outside the band, or the protocol itself said
                    "uncertain" (OPC-UA has this natively)
    BAD (0)         the device reported not-implemented, the read failed, or the
                    connector is disconnected — value is None

The band violation is UNCERTAIN rather than dropped on purpose. "The RTD reported
900 °C" is information about the RTD; discarding it makes a failed sensor look
identical to a gap in coverage, and one of those needs a truck and the other does
not.

STALENESS IS A READING, NOT AN ABSENCE
--------------------------------------
When a connector loses its device it emits BAD-quality samples for that device's
points (`emit_bad_on_disconnect`). This matters more than it looks: without it a
dead gateway produces silence, and silence is indistinguishable from a steady
signal to anything reading "latest value". The twin would keep serving the last
good number as though it were current — confidently wrong, which is the failure
mode this platform exists to prevent.

THREADING
---------
Each connector owns one worker thread; the supervisor owns the connectors. Threads
rather than asyncio because the underlying client libraries (pymodbus sync,
paho-mqtt) are thread-based, and mixing an event loop into a WSGI-era threaded
FastAPI app buys nothing here. A poll loop that blocks on a 500 ms Modbus timeout
is exactly what a thread is for.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from historian import QUALITY_BAD, QUALITY_GOOD, QUALITY_UNCERTAIN, Measurement

from .pointmap import DeadbandFilter, Point, PointMap

log = logging.getLogger("nxr.connectors")

# Reconnect backoff: exponential from 1 s, capped at 60 s. Capped because a
# device that has been down for an hour is usually about to come back (a rebooted
# PLC, a re-terminated RS-485 run), and an uncapped backoff would leave a
# recovered site dark for another hour after the fault was fixed.
BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 60.0
BACKOFF_FACTOR = 2.0


@dataclass
class ConnectorConfig:
    """Everything needed to run one connector. Persisted as JSON."""
    connector_id: str
    tenant_id: str
    protocol: str
    name: str = ""
    enabled: bool = True

    # Transport
    host: str = ""
    port: int = 0
    # Modbus RTU serial
    serial_port: str = ""
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    # OPC-UA / MQTT
    endpoint: str = ""
    username: str = ""
    password: str = ""
    # §2.3 requires TLS 1.3 on the outbound MQTT leg.
    use_tls: bool = True
    ca_cert: str = ""
    client_cert: str = ""
    client_key: str = ""
    tls_insecure: bool = False

    # MQTT topics
    topics: list[str] = field(default_factory=list)
    topic_asset_pattern: str = ""
    client_id: str = ""

    # Polling. §2.3 specifies 1 Hz to 0.2 Hz (1-5 s).
    poll_interval_s: float = 1.0
    timeout_s: float = 3.0
    request_retries: int = 2

    point_map: PointMap | None = None
    # Fallback asset for points whose map leaves asset_id blank.
    default_asset_id: str = ""
    # Publish BAD-quality samples when the device is unreachable, so a silent
    # gateway is visible as bad data rather than as no data.
    emit_bad_on_disconnect: bool = True

    def redacted(self) -> dict:
        """Config for an API response, with every secret removed.

        Allow-listed rather than deny-listed: a future field named `api_token` that
        a deny-list forgot would be published, and a connector config is exactly
        where a customer's PLC password lives.
        """
        safe = {
            "connector_id", "tenant_id", "protocol", "name", "enabled", "host",
            "port", "serial_port", "baudrate", "parity", "stopbits", "bytesize",
            "endpoint", "username", "use_tls", "tls_insecure", "topics",
            "topic_asset_pattern", "client_id", "poll_interval_s", "timeout_s",
            "request_retries", "default_asset_id", "emit_bad_on_disconnect",
        }
        out = {k: getattr(self, k) for k in safe}
        out["password"] = "***" if self.password else ""
        out["has_client_cert"] = bool(self.client_cert)
        out["has_ca_cert"] = bool(self.ca_cert)
        if self.point_map is not None:
            out["point_map"] = self.point_map.to_dict()
        return out


@dataclass
class ConnectorHealth:
    """What an operator needs to know about one connector, without reading logs."""
    connector_id: str
    state: str = "stopped"        # stopped|connecting|connected|degraded|error
    connected: bool = False
    last_poll_at: str | None = None
    last_success_at: str | None = None
    last_error: str = ""
    last_error_at: str | None = None
    polls: int = 0
    poll_failures: int = 0
    consecutive_failures: int = 0
    samples_read: int = 0
    samples_published: int = 0
    samples_suppressed: int = 0   # deadband — proof the filter is earning its keep
    bad_quality: int = 0
    reconnects: int = 0
    next_retry_in_s: float | None = None
    points: int = 0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        total = self.samples_published + self.samples_suppressed
        d["deadband_suppression_pct"] = (
            round(100.0 * self.samples_suppressed / total, 1) if total else 0.0)
        d["poll_success_rate_pct"] = (
            round(100.0 * (self.polls - self.poll_failures) / self.polls, 1)
            if self.polls else None)
        return d


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _catalogue_unit(signal: str) -> str:
    """Canonical unit for a signal, when the point map did not state one.

    Imported lazily and guarded: the connector layer must stay usable for a domain
    that has no unit catalogue, and a missing unit is a cosmetic gap rather than a
    reason to fail a reading.
    """
    try:
        from packs.solar.signals import unit_for
        return unit_for(signal)
    except Exception:
        return ""


class Connector(ABC):
    """Base class. Subclasses implement connect/disconnect/read_once."""

    protocol = "abstract"

    def __init__(self, config: ConnectorConfig, *,
                 submit: Callable | None = None):
        self.config = config
        self.health = ConnectorHealth(connector_id=config.connector_id)
        self.health.points = len(config.point_map.enabled_points()) \
            if config.point_map else 0
        self._deadband = DeadbandFilter()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._backoff = BACKOFF_INITIAL_S
        # Injected so tests can capture submissions without a historian, and so a
        # future connector could be pointed at a different sink.
        self._submit = submit or self._default_submit

    # ── Protocol hooks ──────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> None:
        """Open the transport. Raise on failure — the run loop handles backoff."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the transport. Must not raise."""

    @abstractmethod
    def read_once(self) -> list[tuple[Point, float | None, int]]:
        """One acquisition cycle.

        Returns (point, value, quality) triples. `value` None with quality BAD
        means "could not read this point" — a per-point failure that must not
        abort the others, because one dead register on a 40-point inverter should
        not blind the other 39.
        """

    def test_connection(self) -> dict:
        """Probe the device without starting the worker.

        The single most useful endpoint during commissioning: an engineer on a roof
        needs to know whether the wiring and the addressing are right BEFORE
        committing a connector, and a failed start that only shows up in a log is
        no help at all.
        """
        started = time.monotonic()
        try:
            self.connect()
        except Exception as e:
            return {"ok": False, "stage": "connect", "error": str(e)[:300],
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
        try:
            readings = self.read_once()
            good = sum(1 for _, v, q in readings if v is not None and q >= QUALITY_GOOD)
            sample = [
                {"signal": p.signal, "asset_id": p.asset_id or self.config.default_asset_id,
                 "label": p.label, "value": v, "unit": p.unit, "quality": q}
                for p, v, q in readings[:25]
            ]
            return {"ok": True, "stage": "read",
                    "points_read": len(readings), "points_good": good,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                    "sample": sample}
        except Exception as e:
            return {"ok": False, "stage": "read", "error": str(e)[:300],
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
        finally:
            try:
                self.disconnect()
            except Exception:
                pass

    # ── Value → Measurement ─────────────────────────────────────────────────

    def _quality_for(self, point: Point, value: float | None,
                     protocol_quality: int) -> tuple[float | None, int]:
        """Final value and quality. See the module docstring for the policy."""
        if value is None:
            return None, QUALITY_BAD
        if protocol_quality < QUALITY_GOOD:
            return value, protocol_quality
        if not point.in_range(value):
            # Kept, not dropped: an out-of-band reading is evidence about the
            # sensor. Marked UNCERTAIN so rollups exclude it from statistics while
            # the data-quality view can still count it.
            return value, QUALITY_UNCERTAIN
        return value, QUALITY_GOOD

    def _to_measurements(self, readings, now_wall: float) -> list[Measurement]:
        """Apply quality, deadband and units; produce Measurements."""
        ts = datetime.now(UTC)
        out: list[Measurement] = []
        for point, raw, protocol_quality in readings:
            value, quality = self._quality_for(point, raw, protocol_quality)
            asset_id = point.asset_id or self.config.default_asset_id
            if not asset_id:
                # Refusing is right: a measurement with no asset cannot be joined
                # to the graph, so it would be history nothing can ever read.
                self.health.last_error = (
                    f"point {point.signal} has no asset_id and the connector sets "
                    f"no default_asset_id")
                continue

            with self._lock:
                self.health.samples_read += 1
                if quality < QUALITY_UNCERTAIN:
                    self.health.bad_quality += 1

            # Deadband applies only to trustworthy values. Suppressing a BAD
            # sample because it "did not change" would hide a sensor that has been
            # failed for hours.
            if quality >= QUALITY_GOOD and point.deadband > 0:
                if not self._deadband.should_publish(
                        asset_id, point.signal, value, point.deadband,
                        now_wall, point.deadband_timeout_s):
                    with self._lock:
                        self.health.samples_suppressed += 1
                    continue

            out.append(Measurement(
                tenant_id=self.config.tenant_id, asset_id=asset_id,
                signal=point.signal, ts=ts, value=value,
                # Point unit wins (a site may legitimately report kW where the
                # catalogue's canonical unit is W); the catalogue fills the gap so
                # a map that omitted the unit still produces a labelled series
                # rather than a dimensionless one.
                unit=point.unit or _catalogue_unit(point.signal),
                quality=quality, source=self.protocol,
            ))
        return out

    def _default_submit(self, measurements: list[Measurement]) -> None:
        """Hand off to the one ingest pipeline — never to the historian directly.

        Going through `ingest.submit` is what gives a connector the behaviour
        registry, the change log, the event bus and the diagnosis chain without
        knowing any of them exist.
        """
        if not measurements:
            return
        import ingest
        result = ingest.submit(self.config.tenant_id, measurements)
        with self._lock:
            self.health.samples_published += result.accepted
            if result.errors:
                self.health.last_error = result.errors[0][:300]
                self.health.last_error_at = _now_iso()

    def _emit_disconnected(self) -> None:
        """Publish BAD samples for every point, so a dead device reads as bad data
        rather than as an absence of data (see the module docstring)."""
        if not (self.config.emit_bad_on_disconnect and self.config.point_map):
            return
        ts = datetime.now(UTC)
        batch = []
        for point in self.config.point_map.enabled_points():
            asset_id = point.asset_id or self.config.default_asset_id
            if not asset_id:
                continue
            batch.append(Measurement(
                tenant_id=self.config.tenant_id, asset_id=asset_id,
                signal=point.signal, ts=ts, value=None, unit=point.unit,
                quality=QUALITY_BAD, source=self.protocol,
            ))
        if batch:
            # Behaviour evaluation is skipped: a BAD sample carries no value for a
            # rule to evaluate, and the rules already ignore them. Publishing is
            # kept so a live dashboard greys out immediately.
            try:
                import ingest
                ingest.submit(self.config.tenant_id, batch, evaluate=False)
            except Exception as e:
                log.debug("disconnect marker submit failed: %s", e)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._backoff = BACKOFF_INITIAL_S
            self._thread = threading.Thread(
                target=self._run, name=f"connector-{self.config.connector_id}",
                daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        try:
            self.disconnect()
        except Exception:
            pass
        with self._lock:
            self.health.state = "stopped"
            self.health.connected = False
            self.health.next_retry_in_s = None

    def _run(self) -> None:
        """Poll loop with reconnect backoff."""
        while not self._stop.is_set():
            try:
                with self._lock:
                    self.health.state = "connecting"
                self.connect()
                with self._lock:
                    self.health.state = "connected"
                    self.health.connected = True
                    self.health.consecutive_failures = 0
                    self.health.next_retry_in_s = None
                    self._backoff = BACKOFF_INITIAL_S
                self._poll_loop()
            except Exception as e:
                with self._lock:
                    self.health.state = "error"
                    self.health.connected = False
                    self.health.last_error = str(e)[:300]
                    self.health.last_error_at = _now_iso()
                    self.health.reconnects += 1
                    self.health.next_retry_in_s = self._backoff
                log.warning("connector %s: %s (retry in %.0fs)",
                            self.config.connector_id, e, self._backoff)
                self._emit_disconnected()
                try:
                    self.disconnect()
                except Exception:
                    pass
                # Interruptible sleep: a stop request during a 60 s backoff must not
                # take a minute to take effect, or a rolling deploy stalls.
                if self._stop.wait(self._backoff):
                    break
                self._backoff = min(BACKOFF_MAX_S, self._backoff * BACKOFF_FACTOR)

        with self._lock:
            self.health.state = "stopped"
            self.health.connected = False

    def _poll_loop(self) -> None:
        """Read at the configured interval until stopped or the transport fails.

        The interval is measured from the START of each poll, not by sleeping a
        fixed amount afterwards. Sleeping afterwards makes the real period
        interval + read_time, so a 1 s poll of a slow device silently becomes 1.4 s
        and the ΔP integral it feeds is quietly scaled wrong.
        """
        interval = max(0.05, float(self.config.poll_interval_s))
        while not self._stop.is_set():
            cycle_started = time.monotonic()
            wall = time.time()
            with self._lock:
                self.health.polls += 1
                self.health.last_poll_at = _now_iso()
            try:
                readings = self.read_once()
            except Exception:
                with self._lock:
                    self.health.poll_failures += 1
                    self.health.consecutive_failures += 1
                raise                          # let _run reconnect with backoff

            with self._lock:
                self.health.consecutive_failures = 0
                self.health.last_success_at = _now_iso()

            try:
                self._submit(self._to_measurements(readings, wall))
            except Exception as e:
                # A sink failure must not tear down a healthy device connection —
                # they are independent faults and conflating them means a database
                # blip drops every field connection on the site.
                with self._lock:
                    self.health.last_error = f"submit failed: {str(e)[:250]}"
                    self.health.last_error_at = _now_iso()
                    self.health.state = "degraded"
                log.warning("connector %s submit failed: %s",
                            self.config.connector_id, e)

            elapsed = time.monotonic() - cycle_started
            if self._stop.wait(max(0.0, interval - elapsed)):
                break

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def snapshot(self) -> dict:
        with self._lock:
            return self.health.to_dict()


# ── Registry ────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[Connector]] = {}


def register_connector(cls: type[Connector]) -> type[Connector]:
    """Decorator: make a protocol available to the supervisor and the API."""
    _REGISTRY[cls.protocol] = cls
    return cls


def available_protocols() -> list[str]:
    return sorted(_REGISTRY)


def build_connector(config: ConnectorConfig, **kwargs) -> Connector:
    cls = _REGISTRY.get(config.protocol)
    if cls is None:
        raise ValueError(
            f"no connector for protocol '{config.protocol}'. "
            f"Available: {available_protocols()}. A protocol whose client library "
            f"is not installed does not register — check the import hint on "
            f"/api/v1/connectors/protocols.")
    return cls(config, **kwargs)


class MissingDependency(RuntimeError):
    """A protocol's client library is not installed.

    Raised at CONSTRUCTION with the pip name, rather than letting an ImportError
    surface from inside a worker thread where it would appear as an opaque
    connector error and cost an operator an afternoon.
    """

    def __init__(self, protocol: str, package: str, detail: str = ""):
        super().__init__(
            f"The {protocol} connector needs the '{package}' package, which is not "
            f"installed. Install it with: pip install {package}"
            + (f" ({detail})" if detail else ""))
        self.protocol = protocol
        self.package = package
