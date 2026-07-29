"""
mqtt.py — MQTT connector with TLS 1.3 and Sparkplug B (§2.3).

This is the transport the specification names for the edge gateway's outbound leg:
"structured JSON over MQTT via TLS 1.3 to the secure endpoint cloud broker".

PUSH, NOT POLL — WHICH CHANGES THE LIFECYCLE
--------------------------------------------
Modbus and OPC-UA polling ask a question at a fixed rate. MQTT is a subscription:
the broker delivers when the gateway publishes. So `read_once` is not the driver
here — the paho client's own network thread is, and messages are queued as they
arrive and drained by the base class's poll loop.

Draining a queue rather than submitting from the paho callback is deliberate. The
callback runs on paho's network thread; doing a database write there stalls MQTT
keep-alives, and a stalled keep-alive makes the broker drop the connection, which
looks exactly like a network fault. Queue-and-drain keeps the network thread free
and gives batching for nothing: 40 points published in one burst become one
historian write.

The queue is BOUNDED and drops OLDEST on overflow. A bounded queue that drops is
the honest choice — an unbounded one turns a slow sink into an out-of-memory kill,
and dropping newest would mean a recovering system never catches up to the present.
Every drop is counted so the loss is visible rather than silent.

TLS
---
`use_tls` defaults True and `tls_insecure` defaults False, so the safe posture is
what you get by omission. `tls_insecure` exists because a site's broker may use a
private CA that has not been distributed yet during commissioning — but it disables
certificate verification, so it logs a warning every time it is used and the API
surfaces it in the connector's health.

TLS 1.3 specifically: `ssl.TLSVersion.TLSv1_3` is requested as the MINIMUM when the
Python build supports it (OpenSSL 1.1.1+). It is a minimum rather than an exact
pin so a future TLS 1.4 is not locked out, and it degrades to 1.2 with a warning
rather than failing outright when a broker cannot do 1.3 — refusing to connect
would take a site offline over a broker upgrade someone else controls.

SPARKPLUG B
-----------
Sparkplug is the IIoT profile that turns MQTT from a byte pipe into a
self-describing protocol: NBIRTH/DBIRTH announce a device's full tag list, NDATA
sends only what changed, and NDEATH is delivered by the broker's will mechanism
when a gateway dies. That last one is genuinely valuable here — it is a positive
notification of failure rather than an inferred timeout, which is the difference
between knowing a gateway is gone in milliseconds and finding out after a
staleness threshold.

Decoding it needs protobuf. Without that package the connector still runs and
handles plain JSON; Sparkplug topics are then reported as undecodable rather than
silently ignored, because silently ignoring a whole topic tree is how a site
appears connected and delivers nothing.
"""

from __future__ import annotations

import json
import logging
import queue
import ssl
import threading
import time
from typing import Any

from historian import QUALITY_BAD, QUALITY_GOOD, QUALITY_UNCERTAIN

from .base import Connector, ConnectorConfig, MissingDependency, register_connector
from .pointmap import Point, extract_json_path

log = logging.getLogger("nxr.connectors.mqtt")

# Bounded inbound queue. 20k messages is minutes of buffer for a busy site and a
# few MB of memory — enough to ride out a database failover, far short of an OOM.
MAX_QUEUE = 20_000

# Sparkplug B topic grammar: spBv1.0/<group>/<verb>/<edge_node>[/<device>]
SPARKPLUG_PREFIX = "spBv1.0"
SPARKPLUG_BIRTH_VERBS = ("NBIRTH", "DBIRTH")
SPARKPLUG_DATA_VERBS = ("NDATA", "DDATA")
SPARKPLUG_DEATH_VERBS = ("NDEATH", "DDEATH")


@register_connector
class MqttConnector(Connector):
    """MQTT/TLS subscriber. Handles plain JSON and Sparkplug B."""

    protocol = "mqtt"

    def __init__(self, config: ConnectorConfig, **kwargs):
        super().__init__(config, **kwargs)
        self._client = None
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        self._connected = threading.Event()
        self._connect_error: str | None = None
        self._dropped = 0
        self._decoded_sparkplug = 0
        self._undecodable = 0
        # Sparkplug aliases: DBIRTH carries name+alias, DDATA then sends alias only
        # to save bytes. Without retaining the map, every NDATA is unattributable.
        self._aliases: dict[str, dict[int, str]] = {}
        self._tls_version_used = ""

    # ── Transport ───────────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise MissingDependency("mqtt", "paho-mqtt>=2.0",
                                    "MQTT/Sparkplug ingest") from e

        cfg = self.config
        client_id = cfg.client_id or f"nxr-{cfg.connector_id}"

        # CallbackAPIVersion is required by paho 2.x; falling back keeps 1.x working
        # rather than making the package version a hard gate.
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id, clean_session=True)
        except (AttributeError, TypeError):
            client = mqtt.Client(client_id=client_id, clean_session=True)

        if cfg.username:
            client.username_pw_set(cfg.username, cfg.password or None)

        if cfg.use_tls:
            self._configure_tls(client)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        self._connected.clear()
        self._connect_error = None

        host = cfg.host or cfg.endpoint
        if not host:
            raise ValueError("mqtt connector requires host (broker address)")
        port = int(cfg.port or (8883 if cfg.use_tls else 1883))

        client.connect(host, port, keepalive=60)
        client.loop_start()
        self._client = client

        # Wait for CONNACK. Connecting is asynchronous in paho, so without this the
        # run loop would report "connected" before the broker had accepted (or
        # rejected) the credentials, and a bad password would look like silence.
        if not self._connected.wait(timeout=max(5.0, self.config.timeout_s * 2)):
            detail = self._connect_error or "no CONNACK within timeout"
            raise ConnectionError(f"MQTT connect to {host}:{port} failed: {detail}")

        topics = cfg.topics or ["#"]
        for topic in topics:
            # QoS 1 (at-least-once). QoS 0 would silently lose samples on a
            # reconnect and QoS 2's two extra round trips buy nothing when the
            # historian is already idempotent by primary key — a duplicate delivery
            # collides and is skipped.
            client.subscribe(topic, qos=1)
        log.info("connector %s subscribed to %s", cfg.connector_id, topics)

    def _configure_tls(self, client) -> None:
        cfg = self.config
        context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH,
            cafile=cfg.ca_cert or None)

        # Request TLS 1.3 as a MINIMUM where the build supports it, degrading with
        # a warning rather than refusing. Taking a site offline because its broker
        # has not been upgraded yet is a worse outcome than a logged downgrade.
        try:
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            self._tls_version_used = "TLSv1.3+"
        except (AttributeError, ValueError):
            try:
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                self._tls_version_used = "TLSv1.2+"
                log.warning(
                    "connector %s: TLS 1.3 unavailable in this Python/OpenSSL "
                    "build; requiring TLS 1.2 as the minimum instead. The "
                    "specification (§2.3) asks for 1.3.", cfg.connector_id)
            except Exception:
                self._tls_version_used = "default"

        if cfg.client_cert and cfg.client_key:
            context.load_cert_chain(cfg.client_cert, cfg.client_key)

        if cfg.tls_insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self._tls_version_used += " (INSECURE: verification disabled)"
            log.warning(
                "connector %s: tls_insecure is set — the broker's certificate is "
                "NOT verified, so this connection is vulnerable to interception. "
                "Acceptable while commissioning against a private CA; never leave "
                "it on.", cfg.connector_id)

        client.tls_set_context(context)

    def disconnect(self) -> None:
        client, self._client = self._client, None
        self._connected.clear()
        if client is not None:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass

    # ── paho callbacks (run on paho's network thread) ────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        if code == 0:
            self._connected.set()
        else:
            self._connect_error = f"broker refused connection (code {code})"
            log.warning("connector %s: %s", self.config.connector_id,
                        self._connect_error)

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        self._connected.clear()

    def _on_message(self, client, userdata, message):
        """Queue and return immediately.

        Nothing heavier belongs here: this runs on paho's network thread, and any
        blocking work stalls MQTT keep-alives until the broker drops the connection
        — which then presents as a network fault rather than as our own back
        pressure.
        """
        try:
            self._queue.put_nowait((message.topic, message.payload, time.time()))
        except queue.Full:
            # Drop the OLDEST so a recovering system converges on the present
            # rather than replaying an ever-growing backlog.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((message.topic, message.payload, time.time()))
            except queue.Empty:
                pass
            self._dropped += 1
            if self._dropped % 1000 == 1:
                log.warning(
                    "connector %s: inbound queue full, dropped %d message(s) — the "
                    "sink is slower than the broker is delivering.",
                    self.config.connector_id, self._dropped)

    # ── Reading (drain the queue) ───────────────────────────────────────────

    def read_once(self) -> list[tuple[Point, float | None, int]]:
        if not self._connected.is_set() and self._client is not None:
            raise ConnectionError("MQTT connection lost")

        out: list[tuple[Point, float | None, int]] = []
        drained = 0
        # Bounded drain: a huge backlog is submitted across several cycles so one
        # call cannot block the poll loop for an unbounded time.
        while drained < 5000:
            try:
                topic, payload, _received = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            try:
                out.extend(self._decode_message(topic, payload))
            except Exception as e:
                self._undecodable += 1
                log.debug("connector %s: undecodable message on %s: %s",
                          self.config.connector_id, topic, e)
        return out

    # ── Decoding ────────────────────────────────────────────────────────────

    def _decode_message(self, topic: str, payload: bytes):
        if topic.startswith(SPARKPLUG_PREFIX + "/"):
            return self._decode_sparkplug(topic, payload)
        return self._decode_json(topic, payload)

    def _asset_from_topic(self, topic: str) -> str:
        """Resolve the asset id from the topic.

        `topic_asset_pattern` supports `+` as a single-level capture:
        `site/+/inverter/+` against `site/roof-a/inverter/inv-04` yields
        `roof-a/inv-04` joined by `_`. This is how §3 Step 1's hierarchical ids come
        out of a topic tree without a per-device config line.
        """
        pattern = self.config.topic_asset_pattern
        if not pattern:
            return self.config.default_asset_id
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")
        captured = []
        for i, part in enumerate(pattern_parts):
            if i >= len(topic_parts):
                break
            if part == "+":
                captured.append(topic_parts[i])
            elif part == "#":
                captured.extend(topic_parts[i:])
                break
        return "_".join(captured) if captured else self.config.default_asset_id

    def _decode_json(self, topic: str, payload: bytes):
        """Plain JSON — the §2.3 gateway format."""
        document = json.loads(payload.decode("utf-8"))
        asset = self._asset_from_topic(topic)
        point_map = self.config.point_map
        out = []

        if point_map is None or not point_map.enabled_points():
            # No map: accept the platform's own native shape so a gateway can
            # publish directly with no per-point configuration at all.
            for row in _native_rows(document):
                out.append((
                    Point(signal=row["signal"],
                          asset_id=row.get("asset_id") or asset,
                          unit=row.get("unit", "")),
                    row.get("value"),
                    int(row.get("quality", QUALITY_GOOD)),
                ))
            return out

        for point in point_map.enabled_points():
            value = extract_json_path(document, point.json_path)
            if value is None:
                # Absent is NORMAL in a partial payload, so this is not an error and
                # emits nothing. Emitting BAD here would flood the historian with
                # bad samples for every point a gateway happens not to include.
                continue
            if isinstance(value, bool):
                value = 1.0 if value else 0.0
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                out.append((point, None, QUALITY_BAD))
                continue
            bound = Point(**{**point.__dict__,
                             "asset_id": point.asset_id or asset})
            out.append((bound, numeric, QUALITY_GOOD))
        return out

    def _decode_sparkplug(self, topic: str, payload: bytes):
        """Sparkplug B. Needs protobuf; reports clearly when it is absent."""
        parts = topic.split("/")
        if len(parts) < 4:
            return []
        verb = parts[2]
        edge_node = parts[3]
        device = parts[4] if len(parts) > 4 else ""
        node_key = f"{edge_node}/{device}" if device else edge_node

        if verb in SPARKPLUG_DEATH_VERBS:
            # A positive death notification from the broker's will message. Far
            # better than inferring a fault from a staleness timeout, so it is
            # turned into BAD samples immediately.
            log.warning("connector %s: Sparkplug %s for %s — gateway is gone",
                        self.config.connector_id, verb, node_key)
            self._aliases.pop(node_key, None)
            return self._death_readings(node_key)

        try:
            metrics = _decode_sparkplug_payload(payload)
        except MissingDependency:
            self._undecodable += 1
            if self._undecodable % 500 == 1:
                log.warning(
                    "connector %s: Sparkplug B message on %s cannot be decoded — "
                    "the 'protobuf' package is not installed. Plain-JSON topics "
                    "still work. Install with: pip install protobuf",
                    self.config.connector_id, topic)
            return []

        self._decoded_sparkplug += 1
        alias_map = self._aliases.setdefault(node_key, {})
        if verb in SPARKPLUG_BIRTH_VERBS:
            alias_map.clear()
            for metric in metrics:
                if metric.get("alias") is not None and metric.get("name"):
                    alias_map[int(metric["alias"])] = metric["name"]

        asset = self._asset_from_topic(topic) or node_key
        by_name = {p.json_path or p.signal: p
                   for p in (self.config.point_map.enabled_points()
                             if self.config.point_map else [])}

        out = []
        for metric in metrics:
            name = metric.get("name")
            if not name and metric.get("alias") is not None:
                name = alias_map.get(int(metric["alias"]))
            if not name:
                continue                       # unresolvable alias — no birth seen
            value = metric.get("value")
            if value is None:
                continue
            point = by_name.get(name)
            if point is None:
                # Unmapped metric: pass through under its own name so a Sparkplug
                # site works with no point map at all. `is_historical`/`is_null`
                # from the payload become quality rather than being discarded.
                point = Point(signal=name, asset_id=asset)
            quality = QUALITY_GOOD
            if metric.get("is_null"):
                value, quality = None, QUALITY_BAD
            elif metric.get("is_historical"):
                quality = QUALITY_UNCERTAIN
            bound = Point(**{**point.__dict__,
                             "asset_id": point.asset_id or asset})
            try:
                numeric = None if value is None else float(value)
            except (TypeError, ValueError):
                numeric, quality = None, QUALITY_BAD
            out.append((bound, numeric, quality))
        return out

    def _death_readings(self, node_key: str):
        """BAD samples for a node the broker just declared dead."""
        point_map = self.config.point_map
        if point_map is None:
            return []
        return [(p, None, QUALITY_BAD) for p in point_map.enabled_points()]

    def snapshot(self) -> dict:
        base = super().snapshot()
        base.update({
            "queue_depth": self._queue.qsize(),
            "queue_capacity": MAX_QUEUE,
            "messages_dropped": self._dropped,
            "sparkplug_decoded": self._decoded_sparkplug,
            "undecodable": self._undecodable,
            "tls": self._tls_version_used or ("disabled" if not self.config.use_tls
                                              else "unknown"),
        })
        return base


def _native_rows(document: Any) -> list[dict]:
    """Rows from the platform's own payload shape, so a gateway needs no map.

    Accepts a single object, a bare list, or `{"samples": [...]}` — the three
    shapes a gateway author actually produces.
    """
    if isinstance(document, dict):
        if isinstance(document.get("samples"), list):
            return [r for r in document["samples"] if isinstance(r, dict)]
        if "signal" in document:
            return [document]
        return []
    if isinstance(document, list):
        return [r for r in document if isinstance(r, dict) and "signal" in r]
    return []


# ── Sparkplug B payload decoding ────────────────────────────────────────────

_SPARKPLUG_SCHEMA_CACHE: dict[str, Any] = {}


def _decode_sparkplug_payload(payload: bytes) -> list[dict]:
    """Decode a Sparkplug B protobuf payload into metric dicts.

    Sparkplug's schema is a published .proto. Rather than vendoring generated
    code — which pins a protobuf runtime version and rots — the message is built at
    runtime with `proto_builder`, so it tracks whatever protobuf the environment
    has. The descriptor is cached because building it per message would dominate
    the decode cost.
    """
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    except ImportError as e:
        raise MissingDependency("mqtt", "protobuf>=4.21",
                                "Sparkplug B payload decoding") from e

    message_class = _SPARKPLUG_SCHEMA_CACHE.get("payload")
    if message_class is None:
        message_class = _build_sparkplug_schema(
            descriptor_pb2, descriptor_pool, message_factory)
        _SPARKPLUG_SCHEMA_CACHE["payload"] = message_class

    payload_message = message_class()
    payload_message.ParseFromString(bytes(payload))

    out = []
    for metric in payload_message.metrics:
        row: dict[str, Any] = {
            "name": metric.name or "",
            "alias": metric.alias if metric.HasField("alias") else None,
            "timestamp": metric.timestamp if metric.HasField("timestamp") else None,
            "is_null": metric.is_null if metric.HasField("is_null") else False,
            "is_historical": (metric.is_historical
                              if metric.HasField("is_historical") else False),
            "value": None,
        }
        for field_name in ("double_value", "float_value", "long_value",
                           "int_value", "boolean_value"):
            if metric.HasField(field_name):
                value = getattr(metric, field_name)
                row["value"] = (1.0 if value else 0.0) \
                    if field_name == "boolean_value" else value
                break
        else:
            if metric.HasField("string_value"):
                # A string metric is not a measurement. Numeric-looking strings are
                # accepted because gateways do send "23.4"; anything else is
                # skipped rather than coerced to 0, which would be a fabricated
                # reading.
                try:
                    row["value"] = float(metric.string_value)
                except (TypeError, ValueError):
                    continue
        out.append(row)
    return out


def _build_sparkplug_schema(descriptor_pb2, descriptor_pool, message_factory):
    """Construct the Sparkplug B Payload message type at runtime."""
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "nxr_sparkplug_b.proto"
    file_proto.package = "nxr.sparkplug"
    file_proto.syntax = "proto2"

    metric = file_proto.message_type.add()
    metric.name = "Metric"
    T = descriptor_pb2.FieldDescriptorProto
    for name, number, ftype in (
        ("name", 1, T.TYPE_STRING),
        ("alias", 2, T.TYPE_UINT64),
        ("timestamp", 3, T.TYPE_UINT64),
        ("datatype", 4, T.TYPE_UINT32),
        ("is_historical", 5, T.TYPE_BOOL),
        ("is_transient", 6, T.TYPE_BOOL),
        ("is_null", 7, T.TYPE_BOOL),
        ("int_value", 10, T.TYPE_UINT32),
        ("long_value", 11, T.TYPE_UINT64),
        ("float_value", 12, T.TYPE_FLOAT),
        ("double_value", 13, T.TYPE_DOUBLE),
        ("boolean_value", 14, T.TYPE_BOOL),
        ("string_value", 15, T.TYPE_STRING),
    ):
        field = metric.field.add()
        field.name = name
        field.number = number
        field.type = ftype
        field.label = T.LABEL_OPTIONAL

    payload = file_proto.message_type.add()
    payload.name = "Payload"
    for name, number, ftype in (("timestamp", 1, T.TYPE_UINT64),
                                ("seq", 3, T.TYPE_UINT64),
                                ("uuid", 4, T.TYPE_STRING)):
        field = payload.field.add()
        field.name = name
        field.number = number
        field.type = ftype
        field.label = T.LABEL_OPTIONAL
    metrics_field = payload.field.add()
    metrics_field.name = "metrics"
    metrics_field.number = 2
    metrics_field.type = T.TYPE_MESSAGE
    metrics_field.type_name = ".nxr.sparkplug.Metric"
    metrics_field.label = T.LABEL_REPEATED

    pool = descriptor_pool.Default()
    try:
        descriptor = pool.Add(file_proto)
    except Exception:
        # Already registered (a second connector, or a reload). Look it up rather
        # than failing — the pool is process-global.
        descriptor = pool.FindFileByName(file_proto.name)
    message_descriptor = descriptor.message_types_by_name["Payload"]
    return message_factory.GetMessageClass(message_descriptor)
