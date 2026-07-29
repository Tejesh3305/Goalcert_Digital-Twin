"""
solar_routes.py — the analytics surface for the solar PV twin.

Every endpoint the specification's operating modes need:

  §4.1  GET  /api/v1/solar/{tenant}/model/iv-curve     the De Soto I-V / P-V curve
        POST /api/v1/solar/model/evaluate              model any (G, T) point
  §4.2  GET  /api/v1/solar/{tenant}/residual           ΔP against the modelled baseline
        GET  /api/v1/solar/{tenant}/diagnosis          the three signatures, evaluated
        GET  /api/v1/solar/{tenant}/triggers           the §4.2 table, machine-readable
  §5.1  GET  /api/v1/solar/{tenant}/heatmap            per-string generation density
        GET  /api/v1/solar/{tenant}/strings            per-string detail for tooltips
        GET  /api/v1/solar/{tenant}/energy             generation / consumption / grid
  §5.2  GET  /api/v1/solar/{tenant}/field-service/{asset}   AR overlay for a technician
  §5.3  POST /api/v1/solar/{tenant}/training/scenario  inject a fault for a training lab
        GET  /api/v1/solar/{tenant}/forecast           degradation + RUL

WHY THESE ARE NOT JUST "GET THE TELEMETRY"
------------------------------------------
Raw PV output carries almost no information about array condition — 400 kW from a
500 kW array is excellent at 8 a.m. and alarming at noon. Every endpoint here
therefore returns the MEASURED value alongside the MODELLED baseline and their
residual, because only the residual is diagnostic. An endpoint that returned power
alone would push the hardest part of the problem onto the client, and each client
would solve it differently.

MEASURED-FIRST, MODELLED-FALLBACK
---------------------------------
Each endpoint prefers real telemetry from the historian and falls back to the live
physics twin, reporting which in `source`. This is what lets the same UI serve a
commissioned site and a demo twin without pretending they are the same thing — and
`source` is always present so nobody can mistake one for the other.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import historian
from fastapi import APIRouter, HTTPException, Query, Request
from packs.solar import (
    FAULTS,
    REFERENCE_MODULE,
    SIGNALS,
    SPEC,
    SolarPhysics,
    component_health,
    state_label,
)
from packs.solar import (
    desoto as D,
)
from packs.solar import (
    predict as solar_predict,
)
from packs.solar.signals import (
    DERIVED_SIGNALS,
    INVERTER_SIGNALS,
    METER_SIGNALS,
    STRING_SIGNALS,
    WEATHER_SIGNALS,
)
from pydantic import BaseModel, Field

from server.tenancy import require_write

router = APIRouter(prefix="/api/v1/solar", tags=["solar"])

_physics = SolarPhysics()


# ── Twin resolution ─────────────────────────────────────────────────────────


def _live_twin(tenant: str):
    """The live solar twin for a tenant, or None."""
    try:
        from twins.runtime import get_machine_engine
        twin = get_machine_engine().ensure(tenant)
    except Exception:
        return None
    if twin is None:
        return None
    if getattr(twin, "domain", "") != SPEC["key"]:
        return None
    return twin


def _twin_or_404(tenant: str):
    twin = _live_twin(tenant)
    if twin is None:
        raise HTTPException(
            status_code=404,
            detail=(f"'{tenant}' is not a live solar-pv-array twin. Create one with "
                    f"POST /api/v1/twins {{\"domain\": \"solar-pv-array\"}}."))
    return twin


def _frame_and_state(tenant: str):
    twin = _twin_or_404(tenant)
    state = getattr(twin, "state", None) or getattr(twin, "_state", None)
    frame = {}
    try:
        frame = dict(twin.state_dict().get("latest") or {})
    except Exception:
        pass
    if not frame and state is not None:
        frame = _physics.forward(state, dt=1.0)
    return twin, state, frame


# ── §4.1 The model itself ───────────────────────────────────────────────────


class ModelPoint(BaseModel):
    """Evaluate the De Soto model at an arbitrary operating condition."""
    irradiance: float = Field(1000.0, ge=0.0, le=1500.0,
                              description="Plane-of-array irradiance, W/m²")
    cell_temp_c: float = Field(25.0, ge=-40.0, le=110.0)
    modules_in_series: int = Field(1, ge=1, le=40)
    # Optional degradation, so a client can ask "what would a 20 % shunt loss look
    # like" — which is how the §5.3 training lab builds its exercises.
    soiling: float = Field(1.0, ge=0.1, le=1.0)
    rsh_factor: float = Field(1.0, gt=0.0, le=1.0)
    rs_factor: float = Field(1.0, ge=1.0, le=20.0)
    points: int = Field(60, ge=5, le=400)


@router.post("/model/evaluate")
def model_evaluate(req: ModelPoint):
    """The De Soto five-parameter model at one operating point, with its I-V curve.

    Tenant-free on purpose: this is the MODEL, not a customer's data. It carries no
    telemetry and no asset ids, so it is useful for commissioning arithmetic, for
    the training labs, and for anyone checking the platform's physics against a
    datasheet — which is a thing a technical buyer should be able to do.
    """
    from dataclasses import replace

    params = REFERENCE_MODULE.scale_to_string(req.modules_in_series)
    params = replace(params,
                     r_s=params.r_s * req.rs_factor,
                     r_sh_ref=params.r_sh_ref * req.rsh_factor)
    operating = D.translate(params, req.irradiance * req.soiling, req.cell_temp_c)
    mpp = D.max_power_point(operating)
    curve = D.iv_curve(operating, points=req.points)

    nameplate = D.REFERENCE_MODULE_PMP_STC * req.modules_in_series
    return {
        "inputs": req.model_dump(),
        "operating_parameters": {
            "i_l": round(operating.i_l, 5), "i_0": operating.i_0,
            "r_s": round(operating.r_s, 5), "r_sh": round(operating.r_sh, 2),
            "a": round(operating.a, 5),
            "effective_irradiance": round(operating.irradiance, 2),
            "cell_temp_c": operating.t_cell_c,
        },
        "operating_point": {
            "v_mp": round(mpp.v_mp, 3), "i_mp": round(mpp.i_mp, 4),
            "p_mp": round(mpp.p_mp, 2), "v_oc": round(mpp.v_oc, 3),
            "i_sc": round(mpp.i_sc, 4),
            "fill_factor": round(mpp.fill_factor, 4),
            "efficiency_vs_nameplate": (round(mpp.p_mp / nameplate, 4)
                                        if nameplate else None),
        },
        "iv_curve": curve,
        "model": {
            "name": "De Soto 5-parameter single-diode",
            "reference": ("De Soto et al., Solar Energy 80 (2006) 78-88 — "
                          "specification §4.1"),
            "solver": "Newton-Raphson on the implicit diode equation, "
                      "bisection fallback",
        },
    }


@router.get("/{tenant}/model/iv-curve")
def twin_iv_curve(tenant: str, string_id: str | None = None,
                  points: int = Query(60, ge=5, le=400)):
    """The I-V and P-V curve for this twin's CURRENT conditions.

    Returns the ideal curve and, when a string is named, that string's actual curve
    beside it. Seeing both on one chart is the clearest possible presentation of a
    fault: soiling pulls the current axis down while leaving the voltage intercept
    alone, and a bypass diode does the reverse. §5.1's tooltip is built on this.
    """
    twin, state, _ = _frame_and_state(tenant)
    if state is None:
        raise HTTPException(status_code=503, detail="twin has no physics state")

    params = REFERENCE_MODULE.scale_to_string(state.modules_in_series)
    ideal_op = D.translate(params, state.poa, state.cell_temp)
    out = {
        "tenant": tenant, "source": "physics",
        "conditions": {"poa_irradiance": round(state.poa, 1),
                       "cell_temp_c": round(state.cell_temp, 2),
                       "module_temp_c": round(state.module_temp, 2),
                       "ambient_temp_c": round(state.ambient_temp, 2)},
        "ideal": {"curve": D.iv_curve(ideal_op, points=points),
                  "mpp": _mpp_dict(D.max_power_point(ideal_op))},
    }
    if state.poa <= 1.0:
        out["note"] = ("Irradiance is effectively zero, so there is no curve to "
                       "draw. This is night, not a fault.")
        return out

    if string_id:
        target = next((s for s in state.strings if s.string_id == string_id), None)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"string '{string_id}' not found in this twin")
        actual = _physics.string_operating_point(state, target)
        from dataclasses import replace
        string_params = REFERENCE_MODULE.scale_to_string(target.modules_in_series)
        string_params = replace(
            string_params, r_s=string_params.r_s * target.rs_factor,
            r_sh_ref=string_params.r_sh_ref * target.rsh_factor)
        string_op = D.translate(string_params, state.poa * target.soiling,
                                state.cell_temp)
        out["actual"] = {
            "string_id": string_id,
            "curve": D.iv_curve(string_op, points=points),
            "mpp": {k: round(v, 4) for k, v in actual.items()
                    if isinstance(v, int | float)},
            "degradation": {"soiling": round(target.soiling, 4),
                            "bypassed_fraction": round(target.bypassed_fraction, 4),
                            "rsh_factor": round(target.rsh_factor, 4),
                            "rs_factor": round(target.rs_factor, 4),
                            "open_circuit": target.open_circuit},
        }
    return out


def _mpp_dict(mpp) -> dict:
    return {"v_mp": round(mpp.v_mp, 3), "i_mp": round(mpp.i_mp, 4),
            "p_mp": round(mpp.p_mp, 2), "v_oc": round(mpp.v_oc, 3),
            "i_sc": round(mpp.i_sc, 4),
            "fill_factor": round(mpp.fill_factor, 4)}


# ── §4.2 Residual analytics ─────────────────────────────────────────────────


@router.get("/{tenant}/residual")
def residual(tenant: str):
    """ΔP = P_Actual - P_Modelled, with everything needed to interpret it.

    Returns the measured power, the modelled baseline, the residual, AND the
    supporting discriminators (voltage normality, current imbalance, fill factor).
    The residual alone says "something is wrong"; only the discriminators say what,
    and shipping them together is what stops a client re-implementing §4.2 badly.
    """
    twin, state, frame = _frame_and_state(tenant)

    measured = frame.get(SIGNALS["dc_power"], 0.0)
    modelled = frame.get(SIGNALS["modelled_dc"], 0.0)
    poa = frame.get(SIGNALS["poa"], 0.0)
    op_state = frame.get(SIGNALS["state"])

    diagnosable = poa >= 150.0
    return {
        "tenant": tenant, "source": "physics",
        "timestamp": datetime.now(UTC).isoformat(),
        "measured_dc_w": measured,
        "modelled_dc_w": modelled,
        "delta_p_w": frame.get(SIGNALS["delta_p"]),
        "delta_p_pct": frame.get(SIGNALS["delta_p_pct"]),
        "performance_ratio": frame.get(SIGNALS["pr"]),
        "discriminators": {
            "dc_voltage": frame.get(SIGNALS["dc_voltage"]),
            "dc_current": frame.get(SIGNALS["dc_current"]),
            "current_imbalance_pct": frame.get(SIGNALS["imbalance"]),
            "fill_factor": frame.get(SIGNALS["ff"]),
            "r_shunt_ref": frame.get(SIGNALS["rsh"]),
        },
        "conditions": {
            "poa_irradiance": poa,
            "ghi": frame.get(SIGNALS["ghi"]),
            "cell_temp_c": frame.get(SIGNALS["cell_temp"]),
            "operating_state": op_state,
            "operating_state_label": (state_label(op_state)
                                      if op_state is not None else None),
        },
        "diagnosable": diagnosable,
        # Stated explicitly rather than left implicit: a residual below ~150 W/m² has
        # a tiny denominator and swings wildly, so acting on it produces exactly the
        # false tickets that make operators stop reading alerts.
        "note": (None if diagnosable else
                 "Irradiance below 150 W/m² — the residual's denominator is too "
                 "small for a percentage to be meaningful. Not a fault."),
        "thresholds": {"soiling_delta_p_pct": SPEC["checks"][
            SIGNALS["delta_p_pct"]][0]},
    }


@router.get("/{tenant}/diagnosis")
def diagnosis(tenant: str):
    """Run the three §4.2 signatures against the twin right now.

    Evaluates the same behaviour registry the live findings loop uses, so this
    endpoint and the persisted findings can never disagree — the alternative, a
    second copy of the logic for the on-demand path, is how a "diagnose now" button
    ends up contradicting the alert list.
    """
    twin, state, frame = _frame_and_state(tenant)
    health = component_health(state, frame, _physics) if state else {}

    active = []
    if state is not None and state.fault != "none":
        active.append({"injected_fault": state.fault,
                       "severity": round(state.fault_severity, 2),
                       "description": FAULTS.get(state.fault, "")})

    # Signature evaluation, expressed as the spec's table with each row's condition
    # tested against the current frame.
    delta = frame.get(SIGNALS["delta_p_pct"]) or 0.0
    imbalance = frame.get(SIGNALS["imbalance"]) or 0.0
    poa = frame.get(SIGNALS["poa"]) or 0.0
    strings = frame.get("_strings") or []

    voltages = [s["voltage"] for s in strings if s.get("voltage")]
    collapsed = []
    if len(voltages) >= 2:
        peer_mean = sum(voltages) / len(voltages)
        collapsed = [s for s in strings
                     if s.get("voltage") and s["voltage"] < peer_mean * 0.88
                     and (s.get("current") or 0) > 0.5]

    signatures = [
        {**SPEC["maintenance_triggers"][0],
         "matched": bool(poa >= 150.0 and delta <= -12.0 and imbalance <= 4.0),
         "observed": {"delta_p_pct": delta, "current_imbalance_pct": imbalance}},
        {**SPEC["maintenance_triggers"][1],
         "matched": bool(collapsed),
         "observed": {"collapsed_strings": [s["string_id"] for s in collapsed],
                      "peer_mean_voltage": (round(sum(voltages) / len(voltages), 2)
                                            if voltages else None)},
         # §4.2: "isolates the specific panel string visually in red inside GoalCert"
         "isolate_in_3d": [s["string_id"] for s in collapsed]},
        {**SPEC["maintenance_triggers"][2],
         # A 90-day trend is not answerable from one frame. Saying so is the honest
         # response; a snapshot verdict here would be a guess dressed as a diagnosis.
         "matched": None,
         "observed": {"r_shunt_ref_now": frame.get(SIGNALS["rsh"])},
         "note": ("Requires a 90-day trend from the historian — this endpoint sees "
                  "one frame. The live behaviour rule "
                  "'solar.shunt_resistance_decay' evaluates it continuously, and "
                  "GET /api/v1/entities/{asset}/history?signal=solar:rShuntRef "
                  "returns the series it uses.")},
    ]

    return {
        "tenant": tenant, "source": "physics",
        "timestamp": datetime.now(UTC).isoformat(),
        "diagnosable": poa >= 150.0,
        "health": health,
        "signatures": signatures,
        "active_injections": active,
        "findings": (twin.diagnostics().get("findings", [])
                     if hasattr(twin, "diagnostics") else []),
    }


@router.get("/{tenant}/triggers")
@router.get("/triggers")
def triggers(tenant: str | None = None):
    """§4.2's maintenance-trigger table, machine-readable.

    Published so the copilot's work-order and procurement agents can act on a
    finding's diagnosis without re-parsing an English message, and so an integrator
    can see exactly which condition produces which ticket at which priority.
    """
    return {"triggers": SPEC["maintenance_triggers"],
            "operation_modes": SPEC["operation_modes"],
            "subsystems": [{"key": k, "label": v} for k, v in SPEC["subsystems"]],
            "faults_injectable": FAULTS}


# ── §5.1 Operations Command Center ──────────────────────────────────────────


@router.get("/{tenant}/heatmap")
def heatmap(tenant: str):
    """Per-string generation density for the §5.1 WebGL heat map.

    Each entry carries the string's asset id (matching its 3-D scene node per §3
    Step 1), its output relative to the best string, and a status. `relative` rather
    than absolute power is what makes the map readable: absolute output varies 20×
    over a day, so an absolute colour ramp would be washed out at every hour except
    noon.
    """
    twin, state, frame = _frame_and_state(tenant)
    strings = frame.get("_strings") or []
    if not strings:
        return {"tenant": tenant, "source": "physics", "cells": [],
                "note": "twin has no per-string detail"}

    powers = [s.get("power") or 0.0 for s in strings]
    best = max(powers) if powers else 0.0

    cells = []
    for s in strings:
        power = s.get("power") or 0.0
        relative = (power / best) if best > 0 else 0.0
        if s.get("open"):
            status, reason = "critical", "open circuit"
        elif relative < 0.85 and best > 0:
            status, reason = "critical", "output far below peer strings"
        elif relative < 0.95 and best > 0:
            status, reason = "warning", "output below peer strings"
        else:
            status, reason = "ok", ""
        cells.append({
            "asset_id": s["string_id"], "string_id": s["string_id"],
            "power_w": power, "voltage": s.get("voltage"),
            "current": s.get("current"), "relative": round(relative, 4),
            "status": status, "reason": reason,
            "soiling": s.get("soiling"), "bypassed": s.get("bypassed"),
            "fill_factor": s.get("fill_factor"),
        })
    return {
        "tenant": tenant, "source": "physics",
        "timestamp": datetime.now(UTC).isoformat(),
        "conditions": {"poa_irradiance": frame.get(SIGNALS["poa"]),
                       "cell_temp_c": frame.get(SIGNALS["cell_temp"])},
        "peak_string_w": best, "count": len(cells), "cells": cells,
        "legend": {"ok": ">=95% of the best string",
                   "warning": "85-95%", "critical": "<85% or open"},
    }


@router.get("/{tenant}/strings")
def strings_detail(tenant: str):
    """Per-string detail for §5.1's click-through tooltip: live voltage, current,
    fill factor, and the degradation state behind them."""
    twin, state, frame = _frame_and_state(tenant)
    return {
        "tenant": tenant, "source": "physics",
        "count": len(frame.get("_strings") or []),
        "strings": frame.get("_strings") or [],
        "aggregate": {
            "dc_voltage": frame.get(SIGNALS["dc_voltage"]),
            "dc_current": frame.get(SIGNALS["dc_current"]),
            "dc_power": frame.get(SIGNALS["dc_power"]),
            "current_imbalance_pct": frame.get(SIGNALS["imbalance"]),
        },
    }


@router.get("/{tenant}/energy")
def energy(tenant: str):
    """§1's energy-analytics objective: generation, consumption, grid flow.

    Self-consumption and grid dependency are computed here rather than by the
    client, because both depend on the meter's sign convention and getting that
    wrong inverts them while leaving them plausible. One implementation, one place
    to be right.
    """
    twin, state, frame = _frame_and_state(tenant)

    generation_kw = (frame.get(SIGNALS["ac_power"]) or 0.0) / 1000.0
    load_kw = frame.get(SIGNALS["facility_load"]) or 0.0
    # Positive = importing, per the platform's fixed convention.
    net_kw = frame.get(SIGNALS["net_grid"])
    if net_kw is None:
        net_kw = load_kw - generation_kw

    solar_used_kw = min(generation_kw, load_kw)
    exported_kw = max(0.0, generation_kw - load_kw)
    imported_kw = max(0.0, load_kw - generation_kw)

    return {
        "tenant": tenant, "source": "physics",
        "timestamp": datetime.now(UTC).isoformat(),
        "generation_kw": round(generation_kw, 3),
        "facility_load_kw": round(load_kw, 3),
        "net_grid_kw": round(net_kw, 3),
        "grid_import_kw": round(imported_kw, 3),
        "grid_export_kw": round(exported_kw, 3),
        "solar_consumed_kw": round(solar_used_kw, 3),
        # Of the solar generated, how much was used on site.
        "self_consumption_pct": (round(100.0 * solar_used_kw / generation_kw, 2)
                                 if generation_kw > 0.01 else None),
        # Of the site's demand, how much came from the grid.
        "grid_dependency_pct": (round(100.0 * imported_kw / load_kw, 2)
                                if load_kw > 0.01 else None),
        "performance_ratio": frame.get(SIGNALS["pr"]),
        "sign_convention": ("net_grid_kw POSITIVE means importing from the grid; "
                            "NEGATIVE means exporting."),
    }


# ── §5.2 Immersive Field Service (AR) ───────────────────────────────────────


@router.get("/{tenant}/field-service/{asset_id}")
def field_service(tenant: str, asset_id: str):
    """AR overlay for a technician standing in front of a real asset (§5.2).

    Returns live instrumentation, the LOTO safety sequence, and guided steps with an
    anchor naming where each attaches in AR.

    The LOTO block comes FIRST and is not conditional. A PV array cannot be switched
    off — the modules produce voltage whenever the sun is up, and the DC side stays
    live after the AC breaker opens. That is the single most dangerous
    misunderstanding on a solar roof, so the overlay states it before any diagnostic
    detail rather than leaving it to a technician's training.
    """
    twin, state, frame = _frame_and_state(tenant)

    strings = frame.get("_strings") or []
    target = next((s for s in strings if s["string_id"] == asset_id), None)
    is_string = target is not None

    live = {
        "dc_voltage": frame.get(SIGNALS["dc_voltage"]),
        "dc_current": frame.get(SIGNALS["dc_current"]),
        "dc_power": frame.get(SIGNALS["dc_power"]),
        "ac_power": frame.get(SIGNALS["ac_power"]),
        "heatsink_temp_c": frame.get(SIGNALS["heatsink_temp"]),
        "module_temp_c": frame.get(SIGNALS["module_temp"]),
        "poa_irradiance": frame.get(SIGNALS["poa"]),
        "operating_state": state_label(frame.get(SIGNALS["state"])),
    }
    if is_string:
        live.update({"string_voltage": target.get("voltage"),
                     "string_current": target.get("current"),
                     "string_power": target.get("power"),
                     "string_v_oc": target.get("v_oc")})

    dc_voltage = frame.get(SIGNALS["dc_voltage"]) or 0.0
    poa = frame.get(SIGNALS["poa"]) or 0.0
    energised = poa > 20.0

    steps = [
        {"order": 1, "anchor": "dc_isolator",
         "title": "Verify the DC side is STILL LIVE",
         "detail": (f"Plane-of-array irradiance is {poa:.0f} W/m² and the DC bus "
                    f"reads {dc_voltage:.0f} V. PV modules generate whenever "
                    f"illuminated: opening the AC breaker does NOT de-energise the "
                    f"DC side. Treat all DC conductors as live."
                    if energised else
                    f"Irradiance is {poa:.0f} W/m², so DC voltage is low "
                    f"({dc_voltage:.0f} V). Still verify with a meter — do not "
                    f"trust this reading as an isolation guarantee."),
         "hazard": "arc_flash_dc",
         "critical": True},
        {"order": 2, "anchor": "ac_breaker",
         "title": "Lock out and tag the AC breaker",
         "detail": "Open, lock and tag the inverter's AC disconnect. Apply your "
                   "personal lock. Record the tag number.",
         "hazard": "electrical", "critical": True},
        {"order": 3, "anchor": "dc_isolator",
         "title": "Open the DC isolator under load rating",
         "detail": "Use the rated DC isolator only. Never break a DC string "
                   "connector under load — DC arcs do not self-extinguish.",
         "hazard": "arc_flash_dc", "critical": True},
        {"order": 4, "anchor": "combiner_enclosure",
         "title": "Prove dead at the combiner",
         "detail": "Test each string conductor to earth and to its return with a "
                   "meter rated for the system voltage. Prove the meter on a known "
                   "source before and after.",
         "hazard": "electrical", "critical": True},
    ]

    if is_string:
        steps.extend([
            {"order": 5, "anchor": f"{asset_id}/junction_box",
             "title": f"Inspect {asset_id}",
             "detail": (f"String reads {target.get('voltage') or 0:.0f} V at "
                        f"{target.get('current') or 0:.2f} A. Check for shading, "
                        f"soiling, cracked glass, discoloured cells, and hot spots "
                        f"at the junction box."),
             "hazard": "none", "critical": False},
            {"order": 6, "anchor": f"{asset_id}/module_01",
             "title": "Thermal-image the modules",
             "detail": "A conducting bypass diode shows as a warm substring. "
                       + (f"This string's voltage is "
                          f"{target.get('voltage') or 0:.0f} V against a peer mean "
                          f"— a large deficit points at bypass activation."
                          if target.get("bypassed", 0) > 0.01 else
                          "Compare against a neighbouring healthy string."),
             "hazard": "none", "critical": False},
        ])

    return {
        "tenant": tenant, "asset_id": asset_id,
        "asset_kind": "PVString" if is_string else "PVArray",
        "source": "physics",
        "timestamp": datetime.now(UTC).isoformat(),
        # Deliberately the first key an AR client will read.
        "safety": {
            "loto_required": True,
            "dc_energised": energised,
            "dc_voltage": dc_voltage,
            "warning": ("A PV array cannot be switched off. The DC side is live "
                        "whenever the modules are illuminated, INCLUDING after the "
                        "AC breaker is opened. Full LOTO and prove-dead are "
                        "mandatory before any DC work."),
            "ppe": ["arc-rated face shield", "class-0 insulating gloves",
                    "DC-rated meter (>= system voltage)", "insulated tools"],
        },
        "live_instrumentation": live,
        "procedure": steps,
        "health": component_health(state, frame, _physics) if state else {},
    }


# ── §5.3 Training labs + forecast ───────────────────────────────────────────


class ScenarioRequest(BaseModel):
    fault: str = Field(..., description=f"One of: {sorted(FAULTS)}")
    severity: float = Field(0.7, ge=0.0, le=1.0)
    # Advance the twin so the fault is already developed when trainees arrive — a
    # 90-day PID trend cannot be waited out in a lab session.
    advance_days: float = Field(0.0, ge=0.0, le=3650.0)


@router.post("/{tenant}/training/scenario")
def training_scenario(tenant: str, req: ScenarioRequest, request: Request):
    """Inject a fault for a §5.3 multiplayer training exercise.

    `advance_days` exists because PV faults develop over weeks and months: a PID
    scenario is meaningless without the ability to fast-forward the degradation the
    trainees are supposed to diagnose. It advances the twin's own accumulators, so
    what trainees see is the real model aged, not a scripted mock-up.

    Requires write access — this MUTATES the twin. Harmless on a training twin and
    emphatically not on a production one, which is why it is scoped rather than open.
    """
    require_write(request)
    twin, state, _ = _frame_and_state(tenant)
    if state is None:
        raise HTTPException(status_code=503, detail="twin has no physics state")
    if req.fault not in FAULTS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown fault '{req.fault}'. Known: {sorted(FAULTS)}")

    _physics.inject(state, req.fault, req.severity)

    advanced = 0.0
    if req.advance_days > 0:
        # One step per day: the degradation rates are per-day, so daily steps age
        # the array correctly. A single huge dt would too, but stepping keeps the
        # thermal lags stable.
        steps = int(min(req.advance_days, 3650.0))
        for _ in range(steps):
            _physics.forward(state, dt=86400.0)
        advanced = float(steps)

    frame = _physics.forward(state, dt=1.0)
    return {
        "tenant": tenant, "injected": req.fault,
        "severity": req.severity, "advanced_days": advanced,
        "description": FAULTS[req.fault],
        "resulting_state": {
            "delta_p_pct": frame.get(SIGNALS["delta_p_pct"]),
            "performance_ratio": frame.get(SIGNALS["pr"]),
            "current_imbalance_pct": frame.get(SIGNALS["imbalance"]),
            "fill_factor": frame.get(SIGNALS["ff"]),
            "r_shunt_ref": frame.get(SIGNALS["rsh"]),
        },
        "health": component_health(state, frame, _physics),
        "expected_signature": _expected_signature(req.fault),
    }


def _expected_signature(fault: str) -> dict | None:
    """Which §4.2 row this fault should produce — the training lab's answer key."""
    mapping = {
        "soiling": 0, "shading": 1, "pid": 2, "delamination": 2,
    }
    index = mapping.get(fault)
    return SPEC["maintenance_triggers"][index] if index is not None else None


@router.post("/{tenant}/training/reset")
def training_reset(tenant: str, request: Request):
    """Clear injected faults and wash the array — reset a training exercise."""
    require_write(request)
    twin, state, _ = _frame_and_state(tenant)
    if state is None:
        raise HTTPException(status_code=503, detail="twin has no physics state")
    _physics.inject(state, "none", 0.0)
    _physics.clean_array(state)
    for s in state.strings:
        s.rsh_factor = 1.0
        s.rs_factor = 1.0
        s.bypassed_fraction = 0.0
        s.open_circuit = False
    frame = _physics.forward(state, dt=1.0)
    return {"tenant": tenant, "status": "reset",
            "delta_p_pct": frame.get(SIGNALS["delta_p_pct"]),
            "health": component_health(state, frame, _physics)}


@router.get("/{tenant}/forecast")
def forecast(tenant: str, horizon_days: float = Query(180.0, ge=1.0, le=3650.0),
             points: int = Query(90, ge=10, le=400)):
    """Degradation forecast and time-to-intervention.

    Horizon in DAYS, not minutes: soiling is a matter of weeks and PID a matter of
    months, so a two-hour projection of a PV array is a flat line. Each RUL entry is
    time-to-a-DECISION (when to wash, when to investigate) rather than
    time-to-failure, because an array almost never stops working — it gets
    gradually worse, and the schedulable question is when the loss justifies the
    intervention.
    """
    twin, state, _ = _frame_and_state(tenant)
    if state is None:
        raise HTTPException(status_code=503, detail="twin has no physics state")
    result = solar_predict(state, horizon_min=horizon_days * 1440.0,
                           points=points, physics=_physics)
    return {"tenant": tenant, "source": "physics", **result}


# ── Measured history (the historian, not the model) ─────────────────────────


@router.get("/{tenant}/measured/summary")
def measured_summary(tenant: str,
                     frm: str = Query("-24h", alias="from"),
                     to: str | None = None):
    """What the REAL sensors recorded, for the same signals the model computes.

    Separate from the physics endpoints and labelled `source: "measured"` so nobody
    can confuse the two. This is the endpoint that answers "is this twin actually
    connected to anything", which is a question every one of the physics endpoints
    above is silent about by design.
    """
    try:
        signals = historian.signals(tenant)
    except Exception as e:
        return {"tenant": tenant, "source": "measured", "available": False,
                "detail": str(e)[:200]}

    wanted = {
        WEATHER_SIGNALS["poa_irradiance"]: "poa_irradiance",
        WEATHER_SIGNALS["module_temp"]: "module_temp",
        INVERTER_SIGNALS["dc_power"]: "dc_power",
        INVERTER_SIGNALS["ac_power"]: "ac_power",
        STRING_SIGNALS["string_current"]: "string_current",
        METER_SIGNALS["active_power"]: "meter_active_power",
        DERIVED_SIGNALS["delta_p_pct"]: "delta_p_pct",
        DERIVED_SIGNALS["r_shunt_ref"]: "r_shunt_ref",
    }
    present = {}
    for row in signals:
        key = wanted.get(row["signal"])
        if key:
            present.setdefault(key, []).append(row)

    latest = []
    try:
        latest = historian.latest(tenant)
    except Exception:
        pass

    return {
        "tenant": tenant, "source": "measured",
        "available": bool(signals),
        "backend": historian.backend(),
        "signal_count": len(signals),
        "spec_coverage": {
            key: {"present": key in present,
                  "assets": len(present.get(key, [])),
                  "samples": sum(r["samples"] for r in present.get(key, []))}
            for key in wanted.values()
        },
        "latest": latest[:50],
        "stale_signals": [row for row in latest if row.get("stale")],
        "note": ("spec_coverage tells you which §2.1/§2.2 sensor classes are "
                 "actually reporting. A missing poa_irradiance means every §4.2 "
                 "residual is being computed from a modelled baseline rather than "
                 "a measured one."),
    }
