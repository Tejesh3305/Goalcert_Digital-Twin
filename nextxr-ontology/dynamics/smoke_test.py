"""
smoke_test.py — validate the dynamics engine + coupling WITHOUT Neo4j.

Builds a tiny in-memory topology (UtilityFeed -> Transformer -> UPS -> Chiller ->
AHU -> Zone; Server located in Zone) by populating the engine's internals directly,
then runs ticks and prints the coupled signals. Proves producer->consumer ordering,
spatial coupling, and the cyclic zone<->server feedback (1-tick lag).

    python -m dynamics.smoke_test
"""
from __future__ import annotations

import random
from dynamics.registry_build import build_dynamics_registry
from dynamics.engine import DynamicsEngine

CFP = "https://ontology.nextxr.io/v3/cfp#"


def main():
    reg = build_dynamics_registry()
    eng = DynamicsEngine("test-tenant", reg, query=None, speed=60.0)

    # ---- hand-build a topology (bypass Neo4j load_topology) ----
    nodes = {
        "util": {"canonicalType": CFP + "UtilityFeed", "props": {}},
        "tx":   {"canonicalType": CFP + "Transformer", "props": {"ratedCapacity": 1000}},
        "ups":  {"canonicalType": CFP + "UPS", "props": {"batteryEnergyKWh": 40}},
        "chiller": {"canonicalType": CFP + "Chiller", "props": {"ratedCapacity": 300}},
        "ahu":  {"canonicalType": CFP + "AirHandlingUnit", "props": {"setpoint": 22.0}},
        "zone": {"canonicalType": CFP + "Zone", "props": {"areaM2": 200, "occupancyCap": 20}},
        "srv":  {"canonicalType": CFP + "Server", "props": {"maxPowerKW": 5.0}},
    }
    # feed edges (src feeds tgt): util->tx->ups->chiller; chiller->ahu
    feeds = [("util", "tx"), ("tx", "ups"), ("ups", "chiller"), ("chiller", "ahu")]
    # ahu suppliesAirTo zone (AIR); srv locatedAt zone (SPATIAL)
    air = [("ahu", "zone")]
    spatial = [("srv", "zone")]

    eng._nodes = nodes
    eng._inbound = {n: {} for n in nodes}
    eng._outbound = {n: {} for n in nodes}
    eng._space_of = {}
    eng._contained = {n: [] for n in nodes}
    eng._rngs = {n: random.Random(hash(n) & 0xFFFFFFFF) for n in nodes}

    for src, tgt in feeds:
        eng._inbound[tgt].setdefault("nxr:feeds", []).append(src)
        eng._outbound[src].setdefault("nxr:feeds", []).append(tgt)
    for src, tgt in air:
        eng._inbound[tgt].setdefault("cfp:suppliesAirTo", []).append(src)
        eng._outbound[src].setdefault("cfp:suppliesAirTo", []).append(tgt)
    for src, tgt in spatial:
        eng._space_of[src] = tgt
        eng._contained[tgt].append(src)

    # producers before consumers; zone/server resolved by 1-tick lag
    eng._order = ["util", "tx", "ups", "chiller", "ahu", "zone", "srv"]
    eng._loaded = True

    print(f"{'tick':>4} {'zoneT':>7} {'supplyT':>8} {'chwT':>6} {'COP':>5} "
          f"{'chlrkW':>7} {'srvkW':>6} {'srvLoad':>8} {'oilT':>6} {'soc':>5}")
    dt = 60.0  # 1 sim-minute per tick
    for i in range(40):
        eng.tick(dt)
        s = eng._states
        def g(n, k): return s[n].signals.get(k, float("nan"))
        print(f"{i:>4} "
              f"{g('zone', CFP+'temperature'):>7.2f} "
              f"{g('ahu', CFP+'supplyAirTemp'):>8.2f} "
              f"{g('chiller', CFP+'chwSupplyTemp'):>6.2f} "
              f"{g('chiller', CFP+'chillerCOP'):>5.2f} "
              f"{g('chiller', CFP+'activePower'):>7.2f} "
              f"{g('srv', CFP+'activePower'):>6.2f} "
              f"{g('srv', CFP+'cpuLoad'):>8.3f} "
              f"{g('tx', CFP+'oilTemperature'):>6.2f} "
              f"{g('ups', CFP+'upsSoC'):>5.1f}")


if __name__ == "__main__":
    main()
