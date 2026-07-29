"""spend.py — what the embedded agent layer costs, and the controls on it.

WHY THIS EXISTS
---------------
`copilot/config.py` already records the reason this matters: with the API open,
copilot traffic bills to our Anthropic key from anyone who finds the URL. That
was the argument for dropping the default model from Opus to Sonnet. But a
cheaper model only changes the rate — nothing here measured the spend, and
nothing capped it.

This module adds the three things that were missing, in the order they pay off:

  1. MEASURE   a per-model token + cost ledger, exposed on /copilot/health.
               You cannot tune what you cannot see, and every "optimisation"
               below is unverifiable without it.
  2. AVOID     an in-flight coalescer and a short-TTL response memo, so an
               identical call is answered without touching the API. Same input
               -> same output, so this cannot change what a user sees.
  3. CAP       a per-tenant token budget, so a runaway loop or an open endpoint
               costs a bounded amount instead of an unbounded one.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not lower the model or the effort level behind anyone's back. Those
change output quality, so they stay explicit configuration (`NXR_CLAUDE_MODEL`,
`NXR_COPILOT_EFFORT`) — see the note in config.py. The savings here come from
not paying twice for the same answer, not from buying a cheaper one.

WHERE THE MONEY ACTUALLY GOES (measured, not assumed)
-----------------------------------------------------
Counted with `client.messages.count_tokens` against a live railway twin:

    diagnosis (deep)   system 249 tok    full prompt 1791 tok
    narrate  (quick)   system 103 tok    full prompt  388 tok

Two consequences, both of which shaped this module:

  * The system prompts are 103-249 tokens, and Sonnet 5 will not cache a prefix
    under 1024. Putting `cache_control` on them would cache NOTHING and report
    no error — the classic silent no-op. So prompt caching is applied only to
    the multi-turn chat agents, where the resent conversation history really
    does grow past the minimum. See `cache_hint()`.
  * Output is billed at 5x input ($15 vs $3 per MTok on Sonnet 5), and the deep
    agents run adaptive thinking (billed as output) with max_tokens up to
    12000. Input-side caching is therefore worth single-digit percent here;
    skipping a whole call is worth 100%. Hence the memo.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any

# ── Pricing ($ per 1M tokens: input, output) ────────────────────────────
# List prices. Claude Sonnet 5 carries introductory pricing ($2/$10) through
# 2026-08-31, so a real invoice may come in BELOW what this reports — the
# ledger deliberately over-estimates rather than under-estimates.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5":     (5.00, 25.00),
    "claude-opus-4-8":   (5.00, 25.00),
    "claude-opus-4-7":   (5.00, 25.00),
    "claude-sonnet-5":   (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5":  (1.00,  5.00),
}
_FALLBACK_PRICE = (3.00, 15.00)

#: Cached input is billed at ~0.1x; writing the cache costs ~1.25x (5-minute TTL).
CACHE_READ_RATE = 0.10
CACHE_WRITE_RATE = 1.25


def price_of(model: str) -> tuple[float, float]:
    """(input, output) $/MTok. Unknown models fall back to Sonnet-tier rates so
    an unrecognised override still produces a number rather than a zero."""
    if model in PRICES:
        return PRICES[model]
    for known, p in PRICES.items():          # tolerate date-suffixed variants
        if model.startswith(known):
            return p
    return _FALLBACK_PRICE


def cost_of(model: str, usage: Any) -> float:
    """Dollar cost of one response's `usage`, cache-aware.

    `cache_read_input_tokens` and `cache_creation_input_tokens` are NOT included
    in `input_tokens` — the API reports them separately, and summing naively
    both under-counts the total prompt and prices cached tokens at full rate.
    """
    if usage is None:
        return 0.0
    inp, out = price_of(model)
    g = lambda n: float(getattr(usage, n, 0) or 0)   # noqa: E731
    return (
        g("input_tokens") * inp
        + g("cache_read_input_tokens") * inp * CACHE_READ_RATE
        + g("cache_creation_input_tokens") * inp * CACHE_WRITE_RATE
        + g("output_tokens") * out
    ) / 1_000_000.0


# ── Ledger ──────────────────────────────────────────────────────────────
class Ledger:
    """In-process running total. Per-process by design: it is an operational
    readout, not an invoice — the authoritative number is Anthropic's console.
    It resets on restart, which is why /copilot/health reports `since`."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.since = time.time()
            self.calls = 0
            self.served_from_cache = 0
            self.coalesced = 0
            self.blocked_by_budget = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self.cache_read_tokens = 0
            self.cache_write_tokens = 0
            self.cost_usd = 0.0
            self.by_agent: dict[str, dict] = {}

    def record(self, *, agent: str, model: str, usage: Any) -> float:
        c = cost_of(model, usage)
        g = lambda n: int(getattr(usage, n, 0) or 0)   # noqa: E731
        with self._lock:
            self.calls += 1
            self.input_tokens += g("input_tokens")
            self.output_tokens += g("output_tokens")
            self.cache_read_tokens += g("cache_read_input_tokens")
            self.cache_write_tokens += g("cache_creation_input_tokens")
            self.cost_usd += c
            a = self.by_agent.setdefault(
                agent, {"calls": 0, "input": 0, "output": 0, "cost_usd": 0.0})
            a["calls"] += 1
            a["input"] += g("input_tokens")
            a["output"] += g("output_tokens")
            a["cost_usd"] = round(a["cost_usd"] + c, 6)
        return c

    def note_cache_hit(self) -> None:
        with self._lock:
            self.served_from_cache += 1

    def note_coalesced(self) -> None:
        with self._lock:
            self.coalesced += 1

    def note_blocked(self) -> None:
        with self._lock:
            self.blocked_by_budget += 1

    def snapshot(self) -> dict:
        with self._lock:
            total = self.calls + self.served_from_cache + self.coalesced
            avoided = self.served_from_cache + self.coalesced
            return {
                "since": round(self.since, 3),
                "uptime_s": round(time.time() - self.since, 1),
                "api_calls": self.calls,
                "calls_avoided": avoided,
                "avoided_pct": round(100.0 * avoided / total, 1) if total else 0.0,
                "served_from_cache": self.served_from_cache,
                "coalesced": self.coalesced,
                "blocked_by_budget": self.blocked_by_budget,
                "tokens": {
                    "input": self.input_tokens,
                    "output": self.output_tokens,
                    "cache_read": self.cache_read_tokens,
                    "cache_write": self.cache_write_tokens,
                    # The number to watch when tuning prompt caching: 0 across
                    # repeated calls means no breakpoint is engaging.
                    "cache_hit_pct": round(
                        100.0 * self.cache_read_tokens
                        / (self.cache_read_tokens + self.input_tokens), 1)
                    if (self.cache_read_tokens + self.input_tokens) else 0.0,
                },
                "cost_usd": round(self.cost_usd, 4),
                "by_agent": dict(sorted(self.by_agent.items(),
                                        key=lambda kv: -kv[1]["cost_usd"])),
            }


ledger = Ledger()


# ── Response memo + in-flight coalescing ────────────────────────────────
def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def cache_enabled() -> bool:
    """On by default. NXR_COPILOT_CACHE=0 disables (useful when demoing that
    the agents are live rather than replaying)."""
    return not _truthy(os.environ.get("NXR_COPILOT_CACHE_OFF"))


def cache_ttl() -> int:
    return _int_env("NXR_COPILOT_CACHE_TTL", 120)


def cache_max_entries() -> int:
    return _int_env("NXR_COPILOT_CACHE_MAX", 256)


def request_key(payload: dict) -> str:
    """A stable digest of everything that determines the answer.

    `sort_keys=True` is load-bearing: dict iteration order is stable within a
    process but the payload is assembled from several dicts, and an unsorted
    dump would produce a different key for an identical request.

    Because the live telemetry is part of the prompt, a twin that has ticked
    produces a different key — the memo can never serve a stale reading. That
    is also why the hit rate on live twins comes from repeat/duplicate clicks
    within the TTL rather than from steady-state traffic.
    """
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class _Memo:
    """TTL + size bounded response cache with in-flight coalescing.

    The coalescer matters as much as the cache: the UI fires several agents
    concurrently (the narration strip issues narrate + predict-alert together),
    React StrictMode double-invokes effects in dev, and users double-click. Any
    of those can put two identical requests in flight at once — a plain cache
    misses both, and we pay twice for one answer.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, threading.Event] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _get(self, key: str) -> tuple[bool, Any]:
        now = time.time()
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                return False, None
            expires, value = hit
            if expires < now:
                self._entries.pop(key, None)
                return False, None
            return True, value

    def _put(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._entries) >= cache_max_entries():
                # Evict the soonest-to-expire; cheap and good enough at this size.
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (time.time() + cache_ttl(), value)

    def run(self, key: str, produce: Callable[[], Any]) -> Any:
        """Return a cached answer, wait for an identical in-flight one, or
        produce it. `produce` runs at most once per key per TTL window."""
        if not cache_enabled():
            return produce()

        ok, value = self._get(key)
        if ok:
            ledger.note_cache_hit()
            return value

        with self._lock:
            waiter = self._inflight.get(key)
            if waiter is None:
                self._inflight[key] = threading.Event()
                owner = True
            else:
                owner = False

        if not owner:
            # Someone else is already asking. Wait for them rather than paying
            # for the same answer twice. The timeout is a safety valve: if the
            # owner dies we fall through and produce it ourselves.
            if waiter.wait(timeout=180):
                ok, value = self._get(key)
                if ok:
                    ledger.note_coalesced()
                    return value
            return produce()

        try:
            value = produce()
            self._put(key, value)
            return value
        finally:
            with self._lock:
                ev = self._inflight.pop(key, None)
            if ev is not None:
                ev.set()


memo = _Memo()


# ── Prompt caching ──────────────────────────────────────────────────────
#: Below this, a `cache_control` breakpoint is silently ignored by the API.
#: Sonnet 5 / Opus 4.8 = 1024; Claude Opus 5 = 512; Opus 4.6 / Haiku 4.5 = 4096.
CACHE_MINIMUMS = {
    "claude-opus-5": 512,
    "claude-fable-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-opus-4-7": 2048,
    "claude-opus-4-6": 4096,
    "claude-haiku-4-5": 4096,
}


def cache_minimum(model: str) -> int:
    for known, n in CACHE_MINIMUMS.items():
        if model.startswith(known):
            return n
    return 1024


def _approx_tokens(*parts) -> int:
    """Rough token estimate (~4 chars/token) for deciding whether a breakpoint
    can possibly engage. Only ever used to SKIP a pointless breakpoint, never
    to size a request — so an approximation is fine and avoids a network
    round-trip on every call just to count.

    The SYSTEM prompt must be included. It renders before `messages`, so it is
    part of the prefix, and leaving it out was the first reason caching failed
    to engage here: the chat agents put a multi-KB telemetry snapshot in the
    system prompt, so a messages-only estimate said "too short" on exactly the
    turns that were in fact well over the minimum.
    """
    n = 0
    for part in parts:
        if not part:
            continue
        if isinstance(part, str):
            n += len(part)
            continue
        for m in part:
            c = m.get("content") if isinstance(m, dict) else None
            n += len(c) if isinstance(c, str) else len(json.dumps(c, default=str))
    return n // 4


def cache_hint(model: str, system, messages: list, *, index: int = -1) -> bool:
    """Whether the prefix UP TO the breakpoint is long enough to cache.

    Applied to the multi-turn agents only. A chat resends its whole history
    every turn, so once system + history clears the model's minimum, a
    breakpoint makes each subsequent turn read that prefix at 0.1x instead of
    paying full price for it again.

    Measuring the prefix rather than the whole request matters when `index=-2`:
    the volatile final turn can be most of the prompt, so a whole-request
    estimate says "big enough" while the part actually being cached is under
    the minimum — the API then accepts the breakpoint, caches nothing, and
    reports no error.

    The single-shot agents are deliberately excluded: their prompts measured
    388-1791 tokens with only a 103-249 token stable prefix, so a breakpoint
    there caches nothing and just adds the write premium on a miss.
    """
    if not cache_enabled():
        return False
    prefix = messages[:len(messages) + index + 1] if index < 0 else messages
    return _approx_tokens(system, prefix) >= cache_minimum(model)


def with_cache_breakpoint(messages: list, *, index: int = -1) -> list:
    """Copy `messages` with a cache breakpoint on one message's last block.

    `index=-1` caches everything including the final turn — right when the last
    turn is stable. Pass `index=-2` when the final turn carries volatile
    content (a fresh telemetry snapshot): the breakpoint then sits at the end
    of the last STABLE turn, so the cached prefix is system + settled history
    and the volatile part lands after it, where it invalidates nothing.

    Returns a copy — mutating the caller's list would leak `cache_control` into
    the stored chat history, which then differs from what the client holds.
    """
    if not messages or len(messages) < abs(index):
        return messages
    out = [dict(m) for m in messages]
    target = out[index]
    content = target.get("content")
    if isinstance(content, str):
        target["content"] = [{"type": "text", "text": content,
                              "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        if isinstance(blocks[-1], dict):
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
        target["content"] = blocks
    return out


# ── Budget cap ──────────────────────────────────────────────────────────
class Budget:
    """A ceiling on what the copilot layer may spend, per tenant and overall.

    Unset, this is disabled and nothing changes. Set it on any deployment whose
    API is reachable by people you do not control — that is the exposure
    config.py flags, and a cap is the only thing that bounds it. When the cap is
    hit an agent falls back to its deterministic stub, exactly as it does with
    no API key: the page keeps working, it just stops billing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._spent: dict[str, float] = {}
        self._total = 0.0

    @staticmethod
    def per_tenant_limit() -> float:
        try:
            return float(os.environ.get("NXR_COPILOT_BUDGET_USD") or 0.0)
        except ValueError:
            return 0.0

    @staticmethod
    def total_limit() -> float:
        try:
            return float(os.environ.get("NXR_COPILOT_BUDGET_TOTAL_USD") or 0.0)
        except ValueError:
            return 0.0

    def allows(self, tenant: str) -> bool:
        per, total = self.per_tenant_limit(), self.total_limit()
        if per <= 0 and total <= 0:
            return True
        with self._lock:
            if total > 0 and self._total >= total:
                return False
            if per > 0 and self._spent.get(tenant or "-", 0.0) >= per:
                return False
            return True

    def charge(self, tenant: str, usd: float) -> None:
        with self._lock:
            self._spent[tenant or "-"] = self._spent.get(tenant or "-", 0.0) + usd
            self._total += usd

    def snapshot(self) -> dict:
        per, total = self.per_tenant_limit(), self.total_limit()
        with self._lock:
            return {
                "enabled": per > 0 or total > 0,
                "per_tenant_usd": per or None,
                "total_usd": total or None,
                "spent_total_usd": round(self._total, 4),
                "tenants_charged": len(self._spent),
            }

    def reset(self) -> None:
        with self._lock:
            self._spent.clear()
            self._total = 0.0


budget = Budget()


def status() -> dict:
    """The whole spend picture, for /copilot/health."""
    return {
        "ledger": ledger.snapshot(),
        "budget": budget.snapshot(),
        "cache": {
            "enabled": cache_enabled(),
            "ttl_s": cache_ttl(),
            "max_entries": cache_max_entries(),
        },
    }
