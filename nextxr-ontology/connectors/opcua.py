"""
opcua.py — OPC-UA connector (§2.3 lists OPC UA alongside Modbus).

WHY OPC-UA IS WORTH SUPPORTING SEPARATELY
-----------------------------------------
Modbus carries numbers with no metadata: a register holds 4231 and the point map is
the only thing that knows it means 42.31 A. OPC-UA carries the value, its data type,
its engineering unit, an ENGINEERING RANGE, a timestamp from the source, and a
status code — and it is browsable, so a client can discover the address space instead
of being told it.

Three of those change how this connector behaves relative to the Modbus one:

  * SOURCE TIMESTAMP. The server reports when IT sampled the value, which is more
    accurate than when we received it. The historian's `ts`/`received_at` split
    exists exactly so this can be used properly.
  * STATUS CODE. OPC-UA has a native quality concept, mapping cleanly onto the
    platform's GOOD/UNCERTAIN/BAD. This is the one protocol where quality does not
    have to be inferred, so inferring it anyway would be throwing away the best
    information available.
  * BROWSING. `browse()` walks the address space so an engineer can find real node
    ids rather than guessing. Guessing an OPC-UA node id fails loudly (the server
    rejects it), which is why `profiles.opcua_inverter` is allowed to ship template
    node ids where the SunSpec profile is not allowed to guess a base address.

SUBSCRIBE, DON'T POLL — WHEN THE SERVER SUPPORTS IT
---------------------------------------------------
OPC-UA monitored items let the SERVER decide when a value has changed, including a
server-side deadband. That is strictly better than polling: less traffic, and change
events arrive at the server's sampling rate rather than ours. `use_subscription`
(default on) tries that first and falls back to polled reads, because not every
server implements subscriptions correctly and a site must not go dark over it.

ASYNC LIBRARY IN A THREADED CONNECTOR
-------------------------------------
`asyncua` is asyncio-only. The base class is thread-based because pymodbus and paho
are. Rather than convert everything, this connector owns a private event loop on its
own thread and marshals calls onto it with `run_coroutine_threadsafe`. That keeps one
lifecycle model for every protocol; the alternative — an async variant of the base
class — would mean two poll loops, two health models and two places for a bug.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from historian import QUALITY_BAD, QUALITY_GOOD, QUALITY_UNCERTAIN

from .base import Connector, ConnectorConfig, MissingDependency, register_connector
from .pointmap import Point

log = logging.getLogger("nxr.connectors.opcua")

# OPC-UA StatusCode severity lives in the top two bits of the 32-bit code:
#   00 = Good, 01 = Uncertain, 10/11 = Bad
_SEVERITY_GOOD = 0b00
_SEVERITY_UNCERTAIN = 0b01


def map_status_code(code: Optional[int]) -> int:
    """OPC-UA StatusCode → the platform's quality byte.

    A None code means the server did not supply one, which is treated as GOOD: an
    absent status is not a bad status, and servers that always omit it are common.
    """
    if code is None:
        return QUALITY_GOOD
    try:
        severity = (int(code) >> 30) & 0b11
    except (TypeError, ValueError):
        return QUALITY_UNCERTAIN
    if severity == _SEVERITY_GOOD:
        return QUALITY_GOOD
    if severity == _SEVERITY_UNCERTAIN:
        return QUALITY_UNCERTAIN
    return QUALITY_BAD


@register_connector
class OpcUaConnector(Connector):
    """OPC-UA client. Subscribes where possible, polls otherwise."""

    protocol = "opcua"

    def __init__(self, config: ConnectorConfig, *, use_subscription: bool = True,
                 **kwargs):
        super().__init__(config, **kwargs)
        self._client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._use_subscription = use_subscription
        self._subscription = None
        self._subscribed = False
        # node_id -> (value, status_code). Written by subscription callbacks on the
        # loop thread, read by read_once on the poll thread — hence the lock.
        self._values: dict[str, tuple[Optional[float], Optional[int]]] = {}
        self._values_lock = threading.Lock()
        self._by_node: dict[str, Point] = {}
        if config.point_map:
            self._by_node = {p.node_id: p for p in config.point_map.enabled_points()
                             if p.node_id}

    # ── Private event loop ──────────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=runner, name=f"opcua-loop-{self.config.connector_id}",
            daemon=True)
        thread.start()
        self._loop, self._loop_thread = loop, thread
        return loop

    def _run(self, coroutine, timeout: Optional[float] = None):
        """Run a coroutine on the private loop and wait for it."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return future.result(timeout if timeout is not None
                             else max(5.0, self.config.timeout_s * 2))

    def _shutdown_loop(self) -> None:
        loop, thread = self._loop, self._loop_thread
        self._loop, self._loop_thread = None, None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        if thread is not None:
            thread.join(timeout=3.0)
        try:
            loop.close()
        except Exception:
            pass

    # ── Transport ───────────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            from asyncua import Client
        except ImportError as e:
            raise MissingDependency("opcua", "asyncua>=1.0",
                                    "OPC-UA client") from e

        endpoint = self.config.endpoint or (
            f"opc.tcp://{self.config.host}:{self.config.port or 4840}"
            if self.config.host else "")
        if not endpoint:
            raise ValueError(
                "opcua connector requires endpoint (opc.tcp://host:4840) or host")

        async def do_connect():
            client = Client(url=endpoint, timeout=float(self.config.timeout_s))
            if self.config.username:
                client.set_user(self.config.username)
                if self.config.password:
                    client.set_password(self.config.password)
            if self.config.client_cert and self.config.client_key:
                # Sign & encrypt with Basic256Sha256 — the only policy still
                # considered sound. Basic128Rsa15 and Basic256 are deprecated by the
                # OPC Foundation and would be a finding in any security review.
                await client.set_security_string(
                    f"Basic256Sha256,SignAndEncrypt,"
                    f"{self.config.client_cert},{self.config.client_key}")
            await client.connect()
            return client

        self._client = self._run(do_connect())
        self._subscribed = False

        if self._use_subscription and self._by_node:
            try:
                self._start_subscription()
                self._subscribed = True
            except Exception as e:
                # Falling back rather than failing: a server with broken
                # subscriptions must not take the site's data collection down.
                log.warning(
                    "connector %s: subscription failed (%s) — falling back to "
                    "polled reads at %.1f s", self.config.connector_id, e,
                    self.config.poll_interval_s)
                self._subscribed = False

    def _start_subscription(self) -> None:
        connector = self

        class Handler:
            """asyncua calls this on its own loop thread."""

            def datachange_notification(self, node, value, data):
                node_id = node.nodeid.to_string()
                status = None
                try:
                    status_code = data.monitored_item.Value.StatusCode
                    status = getattr(status_code, "value", None)
                except Exception:
                    pass
                numeric: Optional[float]
                try:
                    numeric = None if value is None else float(value)
                except (TypeError, ValueError):
                    numeric, status = None, 0x80000000     # Bad_TypeMismatch band
                with connector._values_lock:
                    connector._values[node_id] = (numeric, status)

            def event_notification(self, event):
                pass

        async def subscribe():
            # publishing interval in ms; a server-side deadband could be added per
            # item, but the point map's deadband already covers it uniformly across
            # protocols and doing it in one place is worth more than saving traffic
            # on the one protocol that supports it.
            interval_ms = max(100, int(self.config.poll_interval_s * 1000))
            subscription = await self._client.create_subscription(
                interval_ms, Handler())
            nodes = [self._client.get_node(node_id) for node_id in self._by_node]
            await subscription.subscribe_data_change(nodes)
            return subscription

        self._subscription = self._run(subscribe())

    def disconnect(self) -> None:
        subscription, self._subscription = self._subscription, None
        client, self._client = self._client, None

        if subscription is not None:
            try:
                self._run(subscription.delete(), timeout=3.0)
            except Exception:
                pass
        if client is not None:
            try:
                self._run(client.disconnect(), timeout=3.0)
            except Exception:
                pass
        self._shutdown_loop()
        with self._values_lock:
            self._values.clear()
        self._subscribed = False

    # ── Reading ─────────────────────────────────────────────────────────────

    def read_once(self) -> list[tuple[Point, Optional[float], int]]:
        if self._client is None:
            raise ConnectionError("not connected")
        return (self._read_from_subscription() if self._subscribed
                else self._read_polled())

    def _read_from_subscription(self):
        """Drain the values the server has pushed since the last cycle.

        Consumed destructively: a value that has not changed is not re-emitted,
        which is the whole benefit of a subscription. The point map's
        `deadband_timeout_s` still forces a periodic heartbeat, so a genuinely
        static signal does not become indistinguishable from a dead one.
        """
        with self._values_lock:
            snapshot = dict(self._values)
            self._values.clear()

        out = []
        for node_id, (value, status) in snapshot.items():
            point = self._by_node.get(node_id)
            if point is None:
                point = Point(signal=node_id,
                              asset_id=self.config.default_asset_id)
            out.append((point, value, map_status_code(status)))
        return out

    def _read_polled(self):
        """Read every mapped node in ONE request.

        `read_values` batches into a single OPC-UA service call. Reading nodes
        individually would be one round trip each, which for a 40-point device is
        the same latency problem the Modbus planner exists to solve.
        """
        node_ids = list(self._by_node)
        if not node_ids:
            return []

        async def read_all():
            nodes = [self._client.get_node(nid) for nid in node_ids]
            try:
                values = await self._client.read_values(nodes)
                return list(zip(node_ids, values, [None] * len(values)))
            except Exception:
                # Per-node fallback: one bad node id in the map should mark only
                # itself BAD rather than losing the whole cycle.
                results = []
                for node_id, node in zip(node_ids, nodes):
                    try:
                        results.append((node_id, await node.read_value(), None))
                    except Exception:
                        results.append((node_id, None, 0x80000000))
                return results

        out = []
        for node_id, value, status in self._run(read_all()):
            point = self._by_node[node_id]
            numeric: Optional[float]
            try:
                if isinstance(value, bool):
                    numeric = 1.0 if value else 0.0
                else:
                    numeric = None if value is None else float(value)
            except (TypeError, ValueError):
                numeric, status = None, 0x80000000
            out.append((point, numeric, map_status_code(status)))
        return out

    def snapshot(self) -> dict:
        base = super().snapshot()
        base.update({
            "mode": "subscription" if self._subscribed else "polled",
            "mapped_nodes": len(self._by_node),
        })
        return base

    # ── Browsing ────────────────────────────────────────────────────────────

    def browse(self, node_id: str = "", *, max_depth: int = 3,
               max_nodes: int = 500) -> list[dict]:
        """Walk the address space so real node ids can be discovered.

        This is the commissioning tool that makes OPC-UA pleasant: rather than
        transcribing node ids from a vendor PDF, an engineer browses the live server
        and picks them. Bounded in BOTH depth and node count — a large PLC's address
        space runs to tens of thousands of nodes, and an unbounded browse would hang
        the request and hammer the server.
        """
        if self._client is None:
            raise ConnectionError("not connected")

        async def walk():
            found: list[dict] = []

            async def visit(node, depth: int, path: str):
                if depth > max_depth or len(found) >= max_nodes:
                    return
                try:
                    children = await node.get_children()
                except Exception:
                    return
                for child in children:
                    if len(found) >= max_nodes:
                        return
                    try:
                        name = (await child.read_browse_name()).Name
                        node_class = await child.read_node_class()
                    except Exception:
                        continue
                    child_path = f"{path}/{name}" if path else name
                    entry = {
                        "node_id": child.nodeid.to_string(),
                        "name": name,
                        "path": child_path,
                        "node_class": str(node_class),
                    }
                    # Only a Variable holds a value worth mapping. Reading it during
                    # a browse is what lets the UI show a live number next to each
                    # candidate node, which is how an engineer confirms they picked
                    # the right one.
                    if "Variable" in str(node_class):
                        try:
                            entry["value"] = await child.read_value()
                            entry["value"] = (float(entry["value"])
                                              if isinstance(entry["value"],
                                                            (int, float))
                                              else str(entry["value"])[:80])
                        except Exception:
                            entry["value"] = None
                        try:
                            unit = await child.read_data_type_as_variant_type()
                            entry["data_type"] = str(unit)
                        except Exception:
                            pass
                        found.append(entry)
                    else:
                        found.append(entry)
                        await visit(child, depth + 1, child_path)

            root = (self._client.get_node(node_id) if node_id
                    else self._client.get_objects_node())
            await visit(root, 0, "")
            return found

        return self._run(walk(), timeout=30.0)
