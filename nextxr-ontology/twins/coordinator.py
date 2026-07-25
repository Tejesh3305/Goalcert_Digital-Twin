"""coordinator.py — one owner per twin, so live physics has a single source.

THE PROBLEM
-----------
`MachineEngine` runs a 1 Hz ticker that ADVANCES each live twin's physics state
and, on every frame, evaluates the behaviour registry and PERSISTS findings
through the Graph Writer. That state is a stateful integrator: wear accumulates,
heat soaks, an injected fault stays injected. It is not a cache of something
durable — it is the source of truth for the twin's live values.

Run that in two ECS tasks and each task keeps its own copy:

  * every threshold breach is written TWICE — duplicate :Finding nodes and
    duplicate change-log events, for one real event;
  * POST /simulate with a fault mutates ONLY the task that received it, so a
    user injects a fault, refreshes, and it is gone;
  * the two copies drift apart, so sensor values change depending on which task
    the load balancer picked.

The duplicated writes are the serious one: that is data, not display.

THE FIX
-------
Exactly one process owns each tenant's simulation at a time, chosen by a short
Redis lease. Ownership is per TENANT, not global, so the work still spreads
across tasks and there is no singleton service to keep alive.

    owner   ticks the physics, evaluates + persists findings ONCE, and
            publishes the authoritative state to Redis every tick
    readers never tick and never persist; they sync from the published state,
            and forward control actions (throttle / fault / start / stop) to the
            owner through a per-tenant command queue

Failover is automatic: the lease expires a few seconds after an owner dies and
another task adopts the published state, continuing from where it left off
rather than restarting the simulation.

WITHOUT REDIS
-------------
`enabled` is False, `try_own()` always returns True and publish/fetch are no-ops
— i.e. this process owns everything, which is exactly the current single-process
behaviour. Local dev needs no Redis, and `NXR_REQUIRE_REDIS=1` is what stops a
multi-task deploy from silently landing here (AWS_DEPLOYMENT.md §9).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import uuid
from typing import Any, Optional

log = logging.getLogger("nxr.twins.coordinator")

LEASE_KEY = "nxr:twin:lease:"
STATE_KEY = "nxr:twin:state:"
CMD_KEY = "nxr:twin:cmd:"

#: How long a lease survives without a refresh. The ticker refreshes every ~1s,
#: so this tolerates several missed ticks (a GC pause, a slow graph write) before
#: another task may take over — long enough not to flap, short enough that
#: failover is a few seconds rather than minutes.
LEASE_TTL_MS = 8_000

#: Published state expiry. Longer than the lease so a reader always has
#: something to serve during a hand-over, short enough that a permanently dead
#: owner's frame does not sit there looking live forever.
STATE_TTL_S = 60

#: Cap on the command backlog, so a tenant whose owner has gone away cannot grow
#: an unbounded list of queued fault injections.
CMD_MAX = 64

# Refresh only if we still hold the lease. Without the compare this would let a
# task that already lost ownership stamp its id back on top of the new owner's.
_REFRESH_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


# ── state (de)serialisation ─────────────────────────────────────────────
_JSONABLE = (str, int, float, bool, type(None))


def _coerce(v: Any) -> Any:
    """Normalise a field value to something JSON can hold."""
    return sorted(v) if isinstance(v, (set, frozenset)) else v


def _is_jsonable(v: Any) -> bool:
    if isinstance(v, _JSONABLE):
        return True
    if isinstance(v, (list, tuple)):
        return all(_is_jsonable(x) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _is_jsonable(x) for k, x in v.items())
    return False


def state_to_dict(state: Any) -> dict:
    """A physics state dataclass as JSON-safe primitives.

    Two kinds of field are deliberately dropped:

      * anything whose name starts with `_` — internal, per-process machinery,
        not part of the twin's condition. `_rng` is the live `random.Random`
        several packs cache on the state; it is noise, and a new owner
        regenerates it from the `seed` field, which IS published.
      * anything that is not JSON-representable.

    The check is on the CURRENT value, per call, rather than cached per class:
    `_rng` is declared with a `None` default and only becomes a `Random` after
    the first step, so a cache built from a fresh state would happily include it
    and then fail on every publish afterwards. That failure is invisible from
    the owner's side — it just means no reader ever syncs — so this is worth the
    handful of isinstance checks per second.

    Sets (railway's blocked-section set) become sorted lists and are restored as
    sets by `apply_state_dict`.
    """
    out: dict[str, Any] = {}
    for f in dataclasses.fields(state):
        if f.name.startswith("_"):
            continue
        v = _coerce(getattr(state, f.name))
        if _is_jsonable(v):
            out[f.name] = v
    return out


def apply_state_dict(state: Any, data: dict) -> None:
    """Overwrite a local physics state from a published one, in place.

    Coerces using the CURRENT attribute's type rather than a marker in the
    payload: every task builds the same dataclass, so the local value is a
    reliable guide and a set survives the JSON round-trip as a set.
    """
    for f in dataclasses.fields(state):
        if f.name not in data:
            continue
        cur = getattr(state, f.name)
        val = data[f.name]
        if isinstance(cur, (set, frozenset)) and isinstance(val, list):
            val = type(cur)(val)
        setattr(state, f.name, val)


class TwinCoordinator:
    """Per-tenant ownership leases, state publication and a command queue."""

    def __init__(self):
        self.owner_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._r = None
        self._checked = False
        self._lock = threading.Lock()
        self._owned: set[str] = set()
        self._publish_warned = False

    # ── connection ──────────────────────────────────────────────────────
    @property
    def redis(self):
        """A client, or None when Redis is unavailable. Probed once."""
        if self._checked:
            return self._r
        with self._lock:
            if self._checked:
                return self._r
            self._checked = True
            try:
                import redis as _redis
                from bus import redis_url
                client = _redis.Redis.from_url(
                    redis_url(), decode_responses=True,
                    socket_connect_timeout=1.0, socket_timeout=1.0)
                client.ping()
                self._r = client
                log.info("twin coordinator: leases via Redis (owner %s)",
                         self.owner_id)
            except Exception as e:
                log.info("twin coordinator: no Redis (%s) -> this process owns "
                         "every twin", e)
                self._r = None
            return self._r

    @property
    def enabled(self) -> bool:
        return self.redis is not None

    # ── ownership ───────────────────────────────────────────────────────
    def owns(self, tenant: str) -> bool:
        """Whether we believe we own this tenant, without touching Redis."""
        return not self.enabled or tenant in self._owned

    def try_own(self, tenant: str) -> bool:
        """Acquire or refresh the lease. True if we own the tenant afterwards.

        Returns True unconditionally when Redis is absent — a single process is
        trivially the only owner.
        """
        r = self.redis
        if r is None:
            return True
        key = LEASE_KEY + tenant
        try:
            if tenant in self._owned:
                if r.eval(_REFRESH_LUA, 1, key, self.owner_id, LEASE_TTL_MS):
                    return True
                self._owned.discard(tenant)   # lost it (expired, or taken over)
            if r.set(key, self.owner_id, nx=True, px=LEASE_TTL_MS):
                self._owned.add(tenant)
                return True
            return False
        except Exception as e:
            # Redis blip: keep whatever we had. Claiming ownership we cannot
            # verify is how you get two owners writing findings.
            log.debug("lease check failed for %s: %s", tenant, e)
            return tenant in self._owned

    def release(self, tenant: str) -> None:
        """Give up the lease so another task can pick the twin up immediately."""
        self._owned.discard(tenant)
        r = self.redis
        if r is None:
            return
        try:
            r.eval(_RELEASE_LUA, 1, LEASE_KEY + tenant, self.owner_id)
        except Exception:
            pass

    def release_all(self) -> None:
        for tenant in list(self._owned):
            self.release(tenant)

    # ── published state ─────────────────────────────────────────────────
    def publish(self, tenant: str, payload: dict) -> None:
        r = self.redis
        if r is None:
            return
        try:
            r.set(STATE_KEY + tenant, json.dumps(payload), ex=STATE_TTL_S)
        except Exception as e:
            # Loud the first time, then quiet. A publish that fails every tick
            # leaves every reader serving its own divergent copy while the owner
            # looks perfectly healthy — the failure has to be visible somewhere.
            if not self._publish_warned:
                self._publish_warned = True
                log.warning("twin state publish FAILED for %s (%s) — readers "
                            "will not see this twin's live state", tenant, e)
            else:
                log.debug("publish failed for %s: %s", tenant, e)

    def fetch(self, tenant: str) -> Optional[dict]:
        r = self.redis
        if r is None:
            return None
        try:
            raw = r.get(STATE_KEY + tenant)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    # ── commands (reader -> owner) ──────────────────────────────────────
    def push_command(self, tenant: str, cmd: dict) -> bool:
        """Queue a control action for whichever task owns this tenant."""
        r = self.redis
        if r is None:
            return False
        try:
            key = CMD_KEY + tenant
            pipe = r.pipeline()
            pipe.rpush(key, json.dumps(cmd))
            pipe.ltrim(key, -CMD_MAX, -1)
            pipe.expire(key, STATE_TTL_S)
            pipe.execute()
            return True
        except Exception as e:
            log.debug("command push failed for %s: %s", tenant, e)
            return False

    def drain_commands(self, tenant: str, limit: int = CMD_MAX) -> list[dict]:
        """Take everything queued for this tenant (owner side)."""
        r = self.redis
        if r is None:
            return []
        try:
            key = CMD_KEY + tenant
            pipe = r.pipeline()
            pipe.lrange(key, 0, limit - 1)
            pipe.ltrim(key, limit, -1)
            raw, _ = pipe.execute()
            out = []
            for item in raw or []:
                try:
                    out.append(json.loads(item))
                except Exception:
                    pass
            return out
        except Exception:
            return []

    def stats(self) -> dict:
        return {"enabled": self.enabled, "owner_id": self.owner_id,
                "owned": sorted(self._owned)}


_coordinator: Optional[TwinCoordinator] = None
_coord_lock = threading.Lock()


def get_coordinator() -> TwinCoordinator:
    global _coordinator
    if _coordinator is None:
        with _coord_lock:
            if _coordinator is None:
                _coordinator = TwinCoordinator()
    return _coordinator
