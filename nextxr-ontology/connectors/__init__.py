"""
connectors — the field-protocol plugin layer.

Turns real hardware into canonical telemetry. Every adapter funnels into
`ingest.submit`, so validation, quality, idempotency, the historian, the event bus
and the behaviour registry apply identically no matter what spoke on the wire.

    base.py       the Connector contract: lifecycle, backoff, quality, deadband
    pointmap.py   declarative address -> signal maps, and the decoders
    profiles.py   built-in device profiles, one per sensor class in the spec
    modbus.py     Modbus TCP + RS-485 RTU, and SunSpec model-chain discovery
    mqtt.py       MQTT over TLS 1.3, plain JSON and Sparkplug B
    opcua.py      OPC-UA, subscriptions with a polled fallback, browsable
    manager.py    persistence, ownership leases, supervision

    HARDWARE                        THIS LAYER                  PLATFORM
    inverter    -- Modbus TCP  -->  ModbusConnector   \\
    combiner    -- Modbus RTU  -->  ModbusConnector    >-- ingest.submit()
    meter       -- Modbus TCP  -->  ModbusConnector    |        |
    pyranometer -- Modbus TCP  -->  ModbusConnector    |        +-> historian
    RTD / wind  -- Modbus TCP  -->  ModbusConnector    |        +-> event bus
    edge gw     -- MQTT/TLS    -->  MqttConnector      |        +-> behaviours
    PLC         -- OPC-UA      -->  OpcUaConnector    /              -> findings

WHY THE CLIENT LIBRARIES ARE OPTIONAL
-------------------------------------
`pymodbus`, `paho-mqtt`, `asyncua` and `protobuf` are imported lazily and each
protocol registers only if its library is present. A deployment that ingests over
HTTP alone should not have to carry three protocol stacks, and the platform must
still boot when one is missing — but a protocol that is quietly absent is a support
call, so `protocol_availability()` reports exactly which are unusable and the pip
command that fixes each.
"""

from __future__ import annotations

from .base import (
    Connector,
    ConnectorConfig,
    ConnectorHealth,
    MissingDependency,
    available_protocols,
    build_connector,
    register_connector,
)
from .manager import (
    ConnectorManager,
    delete,
    get,
    get_manager,
    list_configs,
    protocol_availability,
    save,
)
from .pointmap import (
    DeadbandFilter,
    Point,
    PointMap,
    PointMapError,
    decode_registers,
    expand_asset_template,
    extract_json_path,
    registers_to_bytes,
)
from .profiles import BUILTIN_PROFILES, build_profile, list_profiles

__all__ = [
    "BUILTIN_PROFILES", "Connector", "ConnectorConfig", "ConnectorHealth",
    "ConnectorManager", "DeadbandFilter", "MissingDependency", "Point",
    "PointMap", "PointMapError", "available_protocols", "build_connector",
    "build_profile", "decode_registers", "delete", "expand_asset_template",
    "extract_json_path", "get", "get_manager", "list_configs", "list_profiles",
    "protocol_availability", "register_connector", "registers_to_bytes", "save",
]
