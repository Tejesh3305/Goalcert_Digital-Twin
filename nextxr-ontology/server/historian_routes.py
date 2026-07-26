"""
historian_routes.py — reading telemetry history.

    GET /api/v1/entities/{node_id}/history   one signal over time
    GET /api/v1/entities/{node_id}/latest    newest value per signal + staleness
    GET /api/v1/twins/{tenant}/signals       signal inventory (tag discovery)
    GET /api/v1/twins/{tenant}/trends        several signals at once, aligned
    GET /api/v1/twins/{tenant}/history/stats row counts + time span

RELATIONSHIP TO /entities/{id}/telemetry
----------------------------------------
That endpoint already exists and does something different: it returns SIMULATED
values from a per-tenant physics engine advanced by wall clock. It is not a
history, it is a live guess, and it is what the equipment panel reads today.

These endpoints return MEASURED values from the historian. Both are kept, and
every response here carries `source: "measured"` so a caller can never mistake
one for the other. Once real ingestion is wired for a customer, the UI reads
these; the simulated endpoint remains for demo twins with no instrumentation,
which is a legitimate product state and not a fallback to hide.

TIME PARAMETERS
---------------
`from`/`to` accept ISO-8601 (with or without Z) or a relative shorthand — `-24h`,
`-7d`, `-30m`, `now`. The shorthand exists because every chart control in the UI
and every curl in a runbook wants "the last day" and converting that to an
absolute timestamp client-side is both tedious and a source of timezone bugs.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException, Query, Request

import historian

router = APIRouter(prefix="/api/v1", tags=["historian"])

_REL_RE = re.compile(r"^-(\d+(?:\.\d+)?)\s*([smhdw])$", re.IGNORECASE)
_REL_UNITS = {"s": "seconds", "m": "minutes", "h": "hours",
              "d": "days", "w": "weeks"}

MAX_TREND_SIGNALS = 24


def _parse_time(value: Optional[str], *, default: datetime) -> datetime:
    """ISO-8601, a relative offset like '-24h', or 'now'."""
    if value is None or not str(value).strip():
        return default
    text = str(value).strip().lower()
    if text in ("now", "0"):
        return datetime.now(timezone.utc)
    m = _REL_RE.match(text)
    if m:
        amount, unit = float(m.group(1)), m.group(2).lower()
        return datetime.now(timezone.utc) - timedelta(
            **{_REL_UNITS[unit]: amount})
    try:
        parsed = datetime.fromisoformat(
            text.replace("z", "+00:00") if text.endswith("z") else text)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot parse time '{value}'. Use ISO-8601 "
                   f"(2026-07-26T10:00:00Z), a relative offset (-24h, -7d), "
                   f"or 'now'.")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _window(frm: Optional[str], to: Optional[str],
            default_span: timedelta = timedelta(hours=1)):
    end = _parse_time(to, default=datetime.now(timezone.utc))
    start = _parse_time(frm, default=end - default_span)
    if start > end:
        start, end = end, start
    return start, end


def _unavailable(detail: str) -> dict:
    """The historian's degraded shape.

    Deliberately a 200 with `available: false` rather than a 503, matching how
    every other read surface here behaves (see query_api's `degraded` flag): a
    chart panel should render "no history yet" instead of the whole page erroring,
    and a twin with no instrumentation is a normal state, not a fault.
    """
    return {"available": False, "source": "measured", "points": [],
            "count": 0, "detail": detail}


# ── Per-entity ─────────────────────────────────────────────────────────────


@router.get("/entities/{node_id}/history")
def entity_history(
    node_id: str,
    tenant: str,
    signal: str = Query(..., description="Canonical signal, e.g. turbine:oilTemp"),
    frm: Optional[str] = Query(None, alias="from",
                               description="ISO-8601, '-24h', or 'now'"),
    to: Optional[str] = Query(None),
    agg: str = Query("auto", description="auto | raw | 1m | 1h | 1d"),
    limit: int = Query(historian.DEFAULT_MAX_POINTS, ge=1,
                       le=historian.HARD_MAX_POINTS),
):
    """One signal's measured history.

    `agg=auto` picks the resolution from the span and REPORTS what it chose in
    `agg` plus a line in `notes`. Silent downsampling is a trap: an operator
    comparing a one-hour view against a one-month view would otherwise see
    different numbers for the same period with nothing to explain why.
    """
    start, end = _window(frm, to)
    try:
        series = historian.history(tenant, node_id, signal, start=start,
                                   end=end, agg=agg, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {**_unavailable(str(e)[:200]), "signal": signal,
                "asset_id": node_id, "tenant": tenant}
    return {"available": True, "source": "measured",
            "from": start.isoformat(), "to": end.isoformat(),
            **series.to_dict()}


@router.get("/entities/{node_id}/latest")
def entity_latest(node_id: str, tenant: str,
                  stale_after_s: int = Query(300, ge=1)):
    """Newest measured value per signal, with age and a staleness flag.

    `age_s`/`stale` are the reason this exists separately from the simulated
    telemetry endpoint. A panel showing "22.4 °C" with no age cannot distinguish
    a live reading from one four days old, and that distinction is the whole
    difference between a working twin and a decorative one.
    """
    try:
        rows = historian.latest(tenant, node_id, stale_after_s=stale_after_s)
    except Exception as e:
        return {**_unavailable(str(e)[:200]), "asset_id": node_id,
                "tenant": tenant, "signals": []}
    return {"available": True, "source": "measured", "asset_id": node_id,
            "tenant": tenant, "count": len(rows), "signals": rows}


# ── Per-twin ───────────────────────────────────────────────────────────────


@router.get("/twins/{tenant}/signals")
def twin_signals(tenant: str, asset_id: Optional[str] = None):
    """Signal inventory: every signal this twin has received, with sample counts
    and first/last timestamps.

    This is the query the tag-mapping UI is built on. A customer arrives with tens
    of thousands of opaque historian tags, and the first thing anyone needs is a
    list of what is actually flowing versus what was merely configured.
    """
    try:
        rows = historian.signals(tenant, asset_id)
    except Exception as e:
        return {**_unavailable(str(e)[:200]), "tenant": tenant, "signals": []}
    return {"available": True, "source": "measured", "tenant": tenant,
            "count": len(rows), "signals": rows}


@router.get("/twins/{tenant}/trends")
def twin_trends(
    tenant: str,
    signals: str = Query(..., description="Comma-separated asset_id:signal pairs"),
    frm: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    agg: str = Query("auto"),
    limit: int = Query(historian.DEFAULT_MAX_POINTS, ge=1,
                       le=historian.HARD_MAX_POINTS),
):
    """Several signals over one shared window.

    Every series is resolved at the SAME aggregation tier, chosen once for the
    window rather than per series. Mixed resolutions on one chart is a subtle way
    to mislead: two lines whose points represent different time spans look
    comparable and are not.

    Each entry is `asset_id:signal`; a signal containing a colon (canonical IRIs
    do — `turbine:oilTemp`) is split on the FIRST colon only.
    """
    pairs: list[tuple[str, str]] = []
    for token in signals.split(","):
        token = token.strip()
        if not token:
            continue
        asset, sep, sig = token.partition(":")
        if not sep or not sig:
            raise HTTPException(
                status_code=400,
                detail=f"'{token}' must be 'asset_id:signal' "
                       f"(e.g. ahu-01:hvac:AirTemperature)")
        pairs.append((asset.strip(), sig.strip()))

    if not pairs:
        raise HTTPException(status_code=400, detail="'signals' is empty")
    if len(pairs) > MAX_TREND_SIGNALS:
        raise HTTPException(
            status_code=400,
            detail=f"{len(pairs)} signals exceeds the {MAX_TREND_SIGNALS} "
                   f"per-request limit — one query per chart, not per dashboard.")

    start, end = _window(frm, to)
    # Resolve the tier ONCE so every series is directly comparable.
    tier = historian.choose_agg(start, end, limit) if agg == "auto" else agg

    out = []
    errors = []
    for asset, sig in pairs:
        try:
            series = historian.history(tenant, asset, sig, start=start, end=end,
                                       agg=tier, limit=limit)
            out.append(series.to_dict())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            errors.append(f"{asset}:{sig}: {str(e)[:120]}")

    return {"available": not errors or bool(out), "source": "measured",
            "tenant": tenant, "agg": tier,
            "from": start.isoformat(), "to": end.isoformat(),
            "count": len(out), "series": out, "errors": errors}


@router.get("/twins/{tenant}/history/stats")
def twin_history_stats(tenant: str):
    """Row count, asset/signal counts and time span for this twin's history."""
    try:
        stats = historian.tenant_stats(tenant)
    except Exception as e:
        return {"available": False, "tenant": tenant, "detail": str(e)[:200]}
    return {"available": True, "tenant": tenant, "backend": historian.backend(),
            **stats}
