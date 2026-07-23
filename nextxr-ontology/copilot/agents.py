"""
agents.py — the embedded Claude agents.

Ported from the standalone agentic engine (collins-demo/orchestrator/claude_client.py)
and re-homed inside the twin. Scenario-engine agents are deliberately excluded —
the platform has its own what-if projection surface (twins/runtime.py `project`).

Every agent follows the same contract:

  * it takes plain dicts/lists (telemetry, diagnostics, predictions) — never a
    live twin object, so the same agent runs on live state OR a snapshot;
  * it NEVER raises: any API/network/parse failure degrades to a deterministic
    stub so the product keeps working keyless;
  * whether a call actually reached Claude is recorded on `agent_trace()`, so a
    stub answer is never silently presented as real reasoning.

Model policy: Claude Sonnet 5 (see copilot/config.py for why, and for how to
put it back on Opus) with adaptive thinking on the heavy reasoning agents
(diagnosis, analysis, cascade, work orders, procedures, reports) and thinking
explicitly disabled for the short latency-sensitive ones (narration, asset
status, alerts, chat) where a one-paragraph answer must come back fast.
"""
from __future__ import annotations

import contextvars
import json
import logging
from contextlib import contextmanager
from typing import Optional

from pydantic import BaseModel, Field

from .config import config
# Domain-name normalisation (the copilot agents' one shared helper). The
# knowledge/RAG store this used to live beside was removed; the alias map stays
# so older callers still resolve to this platform's pack keys.
DOMAIN_ALIASES = {
    "mrt-line": "railway-metro",
    "ev-network": "ev-charging-network",
    "hospital": "hospital-campus",
}


def normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return domain
    return DOMAIN_ALIASES.get(domain, domain)

logger = logging.getLogger("copilot.agents")


# ── Domain context: industry, compliance regime, and the persona to adopt ──
# Keys are this platform's machine-domain keys (packs/<domain>/SPEC["key"]).

DOMAIN_CONTEXT = {
    "turbine-engine": {
        "industry": "aerospace MRO",
        "compliance": "AS9100D / EASA Part 145 / FAA Part 43",
        "role": "aerospace MRO reliability engineer",
    },
    "edm-machine": {
        "industry": "precision machining",
        "compliance": "ISO 9001 quality management",
        "role": "manufacturing process engineer",
    },
    "railway-metro": {
        "industry": "rail transit operations (metro / MRT)",
        "compliance": "LTA Railway Safety Directive, SFSRTS 2000, EN 50126/50128/50129",
        "role": "railway operations and signalling engineer",
    },
    "railway-trainset": {
        "industry": "rail rolling-stock maintenance",
        "compliance": "EN 50126/50128/50129, EN 13749, depot maintenance standards",
        "role": "rolling-stock maintenance engineer",
    },
    "ev-charging-network": {
        "industry": "EV charging infrastructure and grid interface",
        "compliance": "IEC 61851, ISO 15118, OCPP 2.0.1, NFPA 855",
        "role": "EV charging infrastructure engineer",
    },
    "ev-battery-pack": {
        "industry": "EV battery systems and BMS",
        "compliance": "UN ECE R100, IEC 62660, NFPA 855",
        "role": "battery systems and BMS engineer",
    },
    "hospital-campus": {
        "industry": "healthcare facility management",
        "compliance": "JCI accreditation, NFPA 99, ASHRAE 170, MOH Singapore",
        "role": "hospital facilities engineer",
    },
    "defence-base": {
        "industry": "military base and C4ISR operations",
        "compliance": "MIL-STD-882E, NATO STANAGs, NIST SP 800-171",
        "role": "defence operations and readiness engineer",
    },
    "defence-warship": {
        "industry": "naval vessel operations and damage control",
        "compliance": "MIL-STD-882E, STANAG 4154, naval damage-control doctrine",
        "role": "naval engineering officer",
    },
}

_DEFAULT_CTX = {
    "industry": "industrial asset operations",
    "compliance": "ISO 55000 asset management, ISO 9001",
    "role": "senior maintenance and reliability engineer",
}


def _domain_ctx(domain: str | None) -> dict:
    """Context for a domain. Unknown domains get a generic industrial persona
    rather than being mislabelled as aerospace."""
    return DOMAIN_CONTEXT.get(normalize_domain(domain) or "", _DEFAULT_CTX)


# ── Call tracing: did this answer come from Claude, or from the stub? ──────

_trace_var: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "copilot_trace", default=None)


@contextmanager
def agent_trace():
    """Record how the agent call inside this block was served.

        with agent_trace() as t:
            report = diagnosis_agent(diag, machine, domain)
        # t == {"backend": "claude"|"stub", "model": ..., "error": ...}

    Routes attach this to the response so the caller can tell a real answer from
    a fallback.
    """
    t = {"backend": "stub", "model": None, "effort": None, "thinking": False,
         "error": None, "usage": None}
    token = _trace_var.set(t)
    try:
        yield t
    finally:
        _trace_var.reset(token)


def _note(**kw):
    t = _trace_var.get()
    if t is not None:
        t.update(kw)


# ── Anthropic client ──────────────────────────────────────────────────────

_client = None
_client_key: str | None = None


def _anthropic():
    """Lazily built client, rebuilt if the key changes underneath us."""
    global _client, _client_key
    key = config.ANTHROPIC_API_KEY
    if _client is None or _client_key != key:
        import anthropic
        _client = anthropic.Anthropic(api_key=key)
        _client_key = key
    return _client


# Effort profiles. QUICK/CHAT run without thinking so a console observation
# comes back in about a second; DEEP turns on adaptive thinking because those
# outputs are documentation a technician acts on.
#
# The non-deep path must say {"type": "disabled"} rather than omit `thinking`.
# Omitting it is not model-independent: on Opus 4.8 an absent field means no
# thinking, but on Sonnet 5 it means ADAPTIVE. Leaving it out would silently
# make the latency-sensitive agents think — the ~1s console reply becomes
# several seconds and bills thinking tokens on our highest-frequency calls —
# and _note(thinking=deep) would report False while the model was thinking.
QUICK = "low"
CHAT = "medium"

# Explicit per-call thinking config; see the note above on why "disabled" is
# spelled out instead of omitted.
_THINKING_ON = {"type": "adaptive"}
_THINKING_OFF = {"type": "disabled"}


def _text(*, system: str, messages: list, max_tokens: int,
          effort: str = QUICK, deep: bool = False, stub) -> str:
    """Free-text completion. `stub` is a zero-arg callable producing the
    deterministic fallback — required, so every agent works keyless."""
    if not config.claude_enabled:
        _note(backend="stub", error="no ANTHROPIC_API_KEY")
        return stub()
    try:
        kwargs = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": config.DEEP_EFFORT if deep else effort},
            "thinking": _THINKING_ON if deep else _THINKING_OFF,
        }
        resp = _anthropic().messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            _note(backend="stub", error="model declined the request")
            return stub()
        out = "".join(b.text for b in resp.content
                      if getattr(b, "type", "") == "text").strip()
        if not out:
            _note(backend="stub", error="empty response")
            return stub()
        _note(backend="claude", model=resp.model,
              effort=kwargs["output_config"]["effort"], thinking=deep,
              usage={"input": resp.usage.input_tokens,
                     "output": resp.usage.output_tokens})
        return out
    except Exception as e:  # noqa: BLE001 — an agent must never break the page
        logger.warning("copilot text call failed (%s); using stub", e)
        _note(backend="stub", error=str(e))
        return stub()


def _parse(*, system, messages: list, max_tokens: int, output_format,
           deep: bool = True, stub):
    """Structured completion into a Pydantic model. Same keyless contract.

    Note: `output_config` is not passed here — the SDK builds it from
    `output_format`, and supplying both risks clobbering the schema. These
    agents want the default (high) effort anyway.
    """
    if not config.claude_enabled:
        _note(backend="stub", error="no ANTHROPIC_API_KEY")
        return stub()
    try:
        kwargs = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_format": output_format,
            "thinking": _THINKING_ON if deep else _THINKING_OFF,
        }
        resp = _anthropic().messages.parse(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            _note(backend="stub", error="model declined the request")
            return stub()
        parsed = resp.parsed_output
        if parsed is None:
            _note(backend="stub", error="no parsed output")
            return stub()
        _note(backend="claude", model=resp.model, thinking=deep,
              usage={"input": resp.usage.input_tokens,
                     "output": resp.usage.output_tokens})
        return parsed
    except Exception as e:  # noqa: BLE001
        logger.warning("copilot parse call failed (%s); using stub", e)
        _note(backend="stub", error=str(e))
        return stub()


def _j(obj, limit: int = 4000) -> str:
    """Compact JSON for a prompt, truncated to a token budget."""
    return json.dumps(obj, default=str)[:limit]


# ── Knowledge retrieval — removed ─────────────────────────────────────────
# The RAG knowledge store (fault library / compliance / incident memory) was
# retired from the twin. `retrieve_context` is kept as a no-op so the agents
# that used to enrich their prompts with it (Diagnosis, Work Order, Troubleshoot)
# keep working — they now reason purely from the live twin's own physics + findings.

def retrieve_context(query: str, domain: str | None, *, faults: int = 3,
                     compliance: int = 2, incidents: int = 2) -> str:
    return ""


def _finding_query(diagnostics: dict, machine: str) -> str:
    """The retrieval query for a diagnostics blob: what is actually wrong."""
    findings = diagnostics.get("findings") or []
    text = " ".join((f.get("message") or f.get("displayName") or "")
                    for f in findings[:3]).strip()
    if text:
        return text
    bad = [s.get("name", "") for s in (diagnostics.get("sensors") or [])
           if s.get("status") in ("warning", "critical")]
    return (" ".join(bad) or machine).strip()




# ══════════════════════════════════════════════════════════════════════════
# TWIN PROVISIONING
# ══════════════════════════════════════════════════════════════════════════

# ── Agent #1: Twin Builder (conversational intake) ────────────────────────

class TwinBuilderReply(BaseModel):
    reply: str = Field(description="The agent's conversational reply to the user.")
    ready: bool = Field(description="True once the user has described the machine "
                                    "and is ready to generate/build the twin.")
    machine_name: str = Field(default="", description="A short asset name if known.")


def build_twin_reply(history: list[dict], message: str) -> TwinBuilderReply:
    """Converse to learn what the operator wants to twin, then signal readiness.
    `history` is [{role: 'user'|'assistant', content: str}]."""
    user_turns = len([h for h in history if h.get("role") == "user"]) + 1

    def _stub() -> TwinBuilderReply:
        m = (message or "").strip()
        if user_turns <= 1:
            return TwinBuilderReply(reply=(
                "Hi! I'm the Twin Builder. I turn a real machine or facility into a "
                "live digital twin. What are we twinning — and a few words about it? "
                "(e.g. 'a turbofan engine on our MRO test rig')"), ready=False)
        return TwinBuilderReply(reply=(
            f"Got it — “{m}”. Drop a 2D image of it below and I'll reconstruct "
            "it in 3D, then wire up the live sensors and physics."),
            ready=True, machine_name=(m[:40] or "Machine"))

    msgs = [{"role": h["role"], "content": h["content"]} for h in history[-8:]
            if h.get("content")]
    msgs.append({"role": "user", "content": message or "Hello"})
    system = (
        "You are the Twin Builder agent for a multi-vertical digital-twin platform "
        "covering aerospace MRO, precision machining, rail transit, EV charging and "
        "battery systems, hospital facilities, and defence. Converse warmly and "
        "briefly to learn what machine or facility the user wants to twin. Once "
        "they've named or described it, set ready=true and tell them to upload a 2D "
        "image and hit Build — you'll reconstruct the 3D model from the image and "
        "wire up live sensors and physics. Keep replies to 1-3 sentences.")
    return _parse(system=system, messages=msgs, max_tokens=1000,
                  output_format=TwinBuilderReply, deep=False, stub=_stub)


# ── Agent #2: Vision Agent (photo + description → structured twin spec) ───

# Signal keys the platform's own feeds emit. Mapping a sensor onto one of these
# means its 3D hotspot lights up from real telemetry; anything else still
# renders, just without a live binding.
KNOWN_SIGNALS = {
    "aero:exhaustGasTemp": ("Exhaust Gas Temp", "°C"),
    "aero:shaftSpeedN1": ("Shaft Speed N1", "RPM"),
    "aero:hydraulicPressure": ("Hydraulic Pressure", "PSI"),
    "aero:avionicsBayTemp": ("Avionics Bay Temp", "°C"),
    "cfp:oilTemperature": ("Oil Temperature", "°C"),
    "cfp:chillerCOP": ("Chiller COP", ""),
    "cfp:upsSoC": ("UPS State of Charge", "%"),
}


class SensorSpec(BaseModel):
    name: str = Field(description="Human label, e.g. 'Exhaust Gas Temp'")
    signal_key: str = Field(
        description="One of the known platform signal keys when applicable, else "
                    "a short custom key like 'turbine:bearingVibration'.")
    unit: str = Field(description="Measurement unit, e.g. '°C', 'RPM', 'PSI'")
    position: list[float] = Field(
        description="Approximate [x, y, z] hotspot position on the machine, each "
                    "in -1..1, origin at the machine centre.")
    normal: str = Field(default="", description="Normal operating range, free text.")
    description: str = Field(default="", description="What this sensor monitors.")


class TwinSpec(BaseModel):
    machine_type: str = Field(description="e.g. 'Turbofan turbine test rig'")
    machine_name: str = Field(description="A short asset name, e.g. 'Turbine Rig TR-01'")
    summary: str = Field(description="One-paragraph description of the machine.")
    components: list[str] = Field(default_factory=list,
                                  description="Major sub-components identified.")
    sensors: list[SensorSpec] = Field(description="Sensors mapped onto the machine.")


def _turbine_stub() -> TwinSpec:
    return TwinSpec(
        machine_type="Turbofan turbine test rig",
        machine_name="Turbine Rig TR-01",
        summary=("A turbofan engine mounted on an MRO test rig. The fan and "
                 "low-pressure compressor draw air through the inlet; the core "
                 "burns fuel to spin the turbine and exhaust hot gas. Monitored "
                 "for exhaust gas temperature, shaft speed, vibration, and oil "
                 "temperature during ground runs."),
        components=["Inlet / fan", "Compressor", "Combustor", "Turbine",
                    "Exhaust nozzle", "Accessory gearbox"],
        sensors=[
            SensorSpec(name="Exhaust Gas Temp", signal_key="aero:exhaustGasTemp",
                       unit="°C", position=[0.7, 0.1, 0.0],
                       normal="640–710 °C", description="Hot-section health."),
            SensorSpec(name="Shaft Speed N1", signal_key="aero:shaftSpeedN1",
                       unit="RPM", position=[-0.2, 0.0, 0.0],
                       normal="~5200 RPM", description="Low-pressure spool speed."),
            SensorSpec(name="Oil Temperature", signal_key="cfp:oilTemperature",
                       unit="°C", position=[0.1, -0.4, 0.2],
                       normal="< 85 °C", description="Bearing/lube oil temp."),
            SensorSpec(name="Bearing Vibration", signal_key="turbine:bearingVibration",
                       unit="mm/s", position=[0.3, 0.3, -0.2],
                       normal="< 7 mm/s", description="Rotor balance / bearing wear."),
            SensorSpec(name="Hydraulic Pressure", signal_key="aero:hydraulicPressure",
                       unit="PSI", position=[-0.6, -0.3, 0.1],
                       normal="2700–3200 PSI", description="Actuation supply."),
        ],
    )


def _image_block(image_b64: str, filename: str) -> dict:
    """An Anthropic image content block from a bare base64 string or a data URI."""
    if image_b64.startswith("data:") and "," in image_b64:
        header, _, data = image_b64.partition(",")
        media = header.split(";")[0].removeprefix("data:") or "image/png"
    else:
        data = image_b64
        media = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    return {"type": "image",
            "source": {"type": "base64", "media_type": media, "data": data}}


def vision_to_twin_spec(image_b64: Optional[str], description: str,
                        filename: str = "machine.png") -> TwinSpec:
    """Claude vision → structured twin spec. Falls back to a turbine stub."""
    def _stub() -> TwinSpec:
        spec = _turbine_stub()
        if description:
            spec.summary = f"{description.strip()} — {spec.summary}"
        return spec

    if not image_b64:
        _note(backend="stub", error="no image supplied")
        return _stub()

    signal_hint = ", ".join(KNOWN_SIGNALS.keys())
    system = (
        "You are the Vision Agent for a multi-vertical digital-twin platform "
        "(aerospace MRO, precision machining, rail transit, EV, hospital "
        "facilities, defence). Given a photo of a machine or facility and a short "
        "description, identify it and the sensors a maintenance team would "
        "monitor. Map each sensor to one of these live telemetry signal keys when "
        f"it fits ({signal_hint}); otherwise invent a short key like "
        "'turbine:bearingVibration'. Give each sensor an approximate 3D hotspot "
        "position as [x,y,z] in -1..1 with the origin at the machine centre.")
    return _parse(
        system=system,
        messages=[{"role": "user", "content": [
            _image_block(image_b64, filename),
            {"type": "text", "text":
                f"Description from the operator: {description or '(none)'}\n\n"
                "Extract the machine and its monitored sensors."}]}],
        max_tokens=8000, output_format=TwinSpec, stub=_stub)


# ══════════════════════════════════════════════════════════════════════════
# LIVE MONITORING
# ══════════════════════════════════════════════════════════════════════════

# ── Agent #3: Narration ───────────────────────────────────────────────────

def narrate_sensors(state: dict, machine: str,
                    prediction: dict | None = None) -> str:
    """A one-or-two sentence observation on the live sensor stream, the way an
    experienced operator would call it out. Domain-neutral: it reasons from
    whatever signals it is handed, so every twin gets real narration."""
    signals = state.get("latest", state.get("signals", {})) or {}
    findings = state.get("findings", []) or []
    health = state.get("health", {})
    residuals = state.get("residuals", {}) or {}
    crossings = [r for r in (prediction or {}).get("rul", [])
                 if r.get("within_horizon")]

    def _stub() -> str:
        if findings:
            return (f"Active finding: "
                    f"{findings[0].get('message', 'anomaly detected')[:90]}.")
        items = [(k.split(":")[-1], v) for k, v in list(signals.items())[:3]
                 if isinstance(v, (int, float))]
        if items:
            shown = ", ".join(f"{n} {v:.0f}" for n, v in items)
            return f"All readings nominal — {shown}. {machine} running clean."
        return "Waiting for sensor data…"

    system = (
        f"You are an experienced operations and maintenance engineer monitoring a "
        f"live {machine} on the control console. Given the current sensor readings, "
        "issue ONE concise observation (1-2 sentences, present tense). Flag "
        "anomalies, trends, or quiet-but-concerning patterns and name the specific "
        "signals. Sound experienced and calm — not alarmed unless the readings are "
        "truly critical. No preamble, no labels.")
    user = (f"Machine: {machine}\n"
            f"Sensors: {_j(signals, 2000)}\n"
            f"Residuals (measured - expected): {_j(residuals, 800)}\n"
            f"Health: {_j(health, 400)}\n"
            f"Active findings: {len(findings)}")
    if findings:
        user += f"\nLatest finding: {(findings[0].get('message') or '')[:120]}"
    if crossings:
        user += f"\nPREDICTION: {_j(crossings[:2], 600)}"
    return _text(system=system, messages=[{"role": "user", "content": user}],
                 max_tokens=300, effort=QUICK, stub=_stub)


# ── Agent #4: Asset Status (click an asset in the 3-D scene) ──────────────

def asset_status(asset: dict, machine: str, domain: str = "") -> str:
    """Telemetry-grounded status for ONE component the operator clicked."""
    name = asset.get("name") or asset.get("id") or "Component"
    atype = asset.get("type") or ""
    metrics = asset.get("metrics") or {}
    status = asset.get("status") or "ok"

    def _stub() -> str:
        sev = {"crit": "CRITICAL", "warn": "WARNING"}.get(status, "HEALTHY")
        mtxt = ", ".join(f"{k} {v}" for k, v in list(metrics.items())[:4]) \
            or "no live readings"
        if status == "crit":
            return (f"{name} — {sev}. Readings: {mtxt}. This component has crossed "
                    f"an operating limit; isolate it and raise a work order before "
                    f"continued use.")
        if status == "warn":
            return (f"{name} — {sev}. Readings: {mtxt}. Drifting out of band; trend "
                    f"it closely and schedule an inspection.")
        return f"{name} — {sev}. Readings: {mtxt}. Operating within normal limits."

    ctx = _domain_ctx(domain)
    system = (
        f"You are an experienced {ctx['role']} responsible for a {machine}. The "
        "operator clicked one component in the live 3-D twin. Give a detailed but "
        "concise status (3-5 sentences): current condition, anything concerning in "
        "the readings, the most likely cause if degraded, and the recommended "
        "action. Name the specific readings. No preamble.")
    user = (f"Machine: {machine}\nDomain: {normalize_domain(domain) or '(unknown)'}\n"
            f"Component: {name} (type: {atype}, status: {status})\n"
            f"Live readings: {_j(metrics, 1500)}")
    return _text(system=system, messages=[{"role": "user", "content": user}],
                 max_tokens=600, effort=QUICK, stub=_stub)


# ── Agent #5: Predictive Alert ────────────────────────────────────────────

def predictive_alert(prediction: dict, machine: str) -> str | None:
    """An actionable alert if any operating limit is projected to be crossed
    inside the horizon. Returns None when nothing is projected to cross."""
    crossings = [r for r in prediction.get("rul", []) if r.get("within_horizon")]
    if not crossings:
        # Nothing is projected to cross — that is a real answer, not a fallback.
        # Say so, or the caller reads "stub" and assumes the agent failed.
        _note(backend="not_applicable",
              error="no operating limit projected to be crossed in this horizon")
        return None
    # RUL entries come out in dict-iteration order, not urgency order — sort so
    # the alert is about the SOONEST crossing.
    crossings.sort(key=lambda c: c.get("time_to_limit_min")
                   if c.get("time_to_limit_min") is not None else float("inf"))
    r = crossings[0]

    def _stub() -> str:
        ttl = r.get("time_to_limit_min")
        when = f"~{ttl:.0f} minutes" if isinstance(ttl, (int, float)) else "soon"
        return (f"PREDICTIVE ALERT: {r.get('mode')} projected to be reached in "
                f"{when} at the current degradation rate. Reduce load and schedule "
                f"an inspection.")

    system = (
        f"You are a predictive maintenance alert system for a {machine}. Generate a "
        "single ACTIONABLE alert (2-3 sentences). Name the parameter, the projected "
        "time to limit crossing, and the immediate action required. Be specific and "
        "urgent but professional. No preamble.")
    return _text(system=system, messages=[{"role": "user", "content":
                 f"Machine: {machine}\nLimit crossing: {_j(r, 800)}\n"
                 f"Full prediction: {_j(prediction, 3000)}"}],
                 max_tokens=300, effort=QUICK, stub=_stub)


# ══════════════════════════════════════════════════════════════════════════
# DIAGNOSIS & PREDICTION
# ══════════════════════════════════════════════════════════════════════════

# ── Agent #6 / #9: Diagnosis (live diagnostics, or any telemetry snapshot) ─

def diagnosis_agent(diagnostics: dict, machine: str,
                    domain: str = "", *, source: str = "live") -> str:
    """Diagnose a twin: per-component health, per-sensor status, overall
    condition, likely root cause, recommended actions — grounded in the fault
    library and the applicable compliance regime.

    `source` is "live" (diagnostics read from the running twin) or "snapshot"
    (a telemetry blob supplied by the caller). The reasoning is identical; the
    label only tells the model how much to trust component-health figures.
    """
    comps = diagnostics.get("components", []) or []
    sensors = diagnostics.get("sensors", []) or []
    findings = diagnostics.get("findings", []) or []
    overall = diagnostics.get("overall_health")

    def _stub() -> str:
        lines = [f"DIAGNOSIS — {machine}"]
        if overall is not None:
            lines.append(f"Overall health: {round(overall * 100)}%.")
        bad = [c for c in comps if (c.get("health") or 1) < 0.6]
        if bad:
            lines.append("Components needing attention: " + ", ".join(
                f"{c.get('name')} ({c.get('status')}, "
                f"{round((c.get('health') or 0) * 100)}%)" for c in bad) + ".")
        elif comps:
            lines.append("All components within healthy bounds.")
        alarms = [s for s in sensors if s.get("status") in ("warning", "critical")]
        if alarms:
            lines.append("Sensors out of band: " + ", ".join(
                f"{s.get('name')}={s.get('value')} ({s.get('status')})"
                for s in alarms) + ".")
        if findings:
            lines.append("Active findings: " + "; ".join(
                (f.get("message") or "")[:80] for f in findings[:4]) + ".")
        return " ".join(lines)

    ctx = _domain_ctx(domain)
    rag = retrieve_context(_finding_query(diagnostics, machine), domain)
    system = (
        f"You are a {ctx['role']}. Given a structured snapshot of a "
        f"{ctx['industry']} digital twin (components, sensors, findings), write a "
        "clear diagnosis report: (1) overall condition, (2) a line per component "
        "with its health and what it implies, (3) any sensors out of band, (4) the "
        "most likely root cause — reference the similar known faults below if any "
        "are provided, (5) recommended actions with specific compliance references. "
        f"Reference applicable standards ({ctx['compliance']}) where relevant. "
        "Cite ONLY standards that appear in the supplied compliance rules or that "
        "you are certain apply — do not invent clause numbers. Be specific and "
        "grounded in the numbers. Short sections, no preamble.")
    user = (f"Machine: {machine}\nTelemetry source: {source}\n"
            f"Snapshot: {_j(diagnostics, 6000)}") + rag
    return _text(system=system, messages=[{"role": "user", "content": user}],
                 max_tokens=8000, deep=True, stub=_stub)


def diagnose_snapshot(machine: str, domain: str, latest: dict,
                      findings: list, components: list | None = None) -> str:
    """Diagnosis over a raw telemetry snapshot — the snapshot-source form of
    `diagnosis_agent`, for twins whose state the caller already holds."""
    return diagnosis_agent(snapshot_diagnostics(machine, latest, findings,
                                                components, domain),
                           machine, domain, source="snapshot")


def snapshot_diagnostics(machine: str, latest: dict, findings: list | None = None,
                         components: list | None = None,
                         domain: str = "") -> dict:
    """Shape a raw telemetry snapshot like the runtime's `diagnostics()` output
    so every diagnostics-consuming agent runs on snapshots too."""
    sensors = [{"name": k.split(":")[-1], "signal": k, "value": v}
               for k, v in (latest or {}).items()]
    return {"machine": machine, "domain": normalize_domain(domain) or domain,
            "components": components or [], "sensors": sensors,
            "latest": latest or {}, "findings": findings or []}


# ── Agent #7: Analysis (present state + horizon forecast) ─────────────────

def analysis_agent(diagnostics: dict, prediction: dict, machine: str,
                   horizon_label: str, domain: str = "") -> str:
    """Analyse the twin now AND over the chosen horizon, using the prediction
    engine's projected trajectory."""
    overall = diagnostics.get("overall_health")
    rul = prediction.get("rul", []) or []
    ev = prediction.get("events", []) or []
    ch_now = prediction.get("component_health_now", {}) or {}
    ch_fut = prediction.get("component_health_horizon", {}) or {}

    def _stub() -> str:
        lines = [f"ANALYSIS — {machine} (now + next {horizon_label})"]
        if overall is not None:
            lines.append(f"Present overall health: {round(overall * 100)}%.")
        crossings = [r for r in rul if r.get("within_horizon")]
        if crossings:
            for r in crossings:
                ttl = r.get("time_to_limit_min")
                lines.append(f"Projected to reach {r.get('mode')} in ~"
                             f"{ttl:.0f} min." if isinstance(ttl, (int, float))
                             else f"Projected to reach {r.get('mode')} within the horizon.")
        else:
            lines.append(f"No operating limit is projected to be crossed within "
                         f"the next {horizon_label}.")
        deltas = []
        for k in set(ch_now) | set(ch_fut):
            a = (ch_now.get(k) or {}).get("health")
            b = (ch_fut.get(k) or {}).get("health")
            if a is not None and b is not None and (a - b) > 0.05:
                deltas.append(f"{k} {round(a * 100)}%→{round(b * 100)}%")
        if deltas:
            lines.append("Degrading over the horizon: " + ", ".join(deltas) + ".")
        if ev:
            lines.append("Predicted detections: " +
                         ", ".join(e.get("behavior_id", "") for e in ev) + ".")
        return " ".join(lines)

    ctx = _domain_ctx(domain)
    system = (
        f"You are a {ctx['role']} working in {ctx['industry']}. You are given (a) a "
        f"present snapshot of a {machine} twin and (b) a physics PREDICTION of how "
        f"it evolves over the next {horizon_label}. Write an analysis covering: the "
        "present state of each sensor and component, how they are TRENDING, the "
        "predicted remaining-useful-life / time-to-limit, and the precautions to "
        "take to be ready. Be specific and grounded in the numbers. Short sections, "
        "no preamble.")
    payload = {"present": diagnostics,
               "prediction": {"horizon_min": prediction.get("horizon_min"),
                              "rul": rul, "events": ev,
                              "component_health_now": ch_now,
                              "component_health_horizon": ch_fut}}
    return _text(system=system, messages=[{"role": "user", "content":
                 f"Machine: {machine}\n{_j(payload, 8000)}"}],
                 max_tokens=8000, deep=True, stub=_stub)


# ── Agent #10: Snapshot forecast (qualitative, no physics engine) ─────────

def forecast_snapshot(machine: str, domain: str, latest: dict,
                      horizon_label: str, context: str = "") -> str:
    """Qualitative forecast from a telemetry snapshot over a horizon, for twins
    with no forward physics model to call."""
    def _stub() -> str:
        return (f"Over the next {horizon_label}, {machine} is expected to continue "
                f"near its current operating point; watch any signals close to "
                f"their limits and keep spares ready for the most-loaded assets.")

    ctx = _domain_ctx(domain)
    system = (
        f"You are a {ctx['role']} responsible for a {machine}. Given the current "
        f"telemetry, forecast how it is likely to behave over the next "
        f"{horizon_label}: which signals trend toward their limits, the main risks, "
        f"and what to watch or pre-empt. {context} Be specific and grounded in the "
        "numbers. 4-6 sentences, no preamble.")
    return _text(system=system, messages=[{"role": "user", "content":
                 f"Machine: {machine}\nDomain: {normalize_domain(domain) or domain}\n"
                 f"Signals: {_j(latest, 3000)}"}],
                 max_tokens=6000, deep=True, stub=_stub)


# ── Agent #8: Cascade analysis (cross-system failure propagation) ─────────

def cascade_analysis(diagnostics: dict, prediction: dict, machine: str,
                     domain: str = "") -> str:
    """How degradation in one subsystem is likely to propagate to another."""
    def _stub() -> str:
        comps = diagnostics.get("components", []) or []
        bad = [c for c in comps if (c.get("health") or 1) < 0.7]
        if not bad:
            return ("No cascade risks identified. All subsystems are operating "
                    "within margins.")
        names = [c.get("name") or "a subsystem" for c in bad]
        return (f"Degradation detected in {', '.join(names)}. Left uncorrected, "
                f"a degraded subsystem loads its neighbours — monitor the "
                f"downstream signals of each and consider pre-emptive intervention "
                f"before the next duty cycle.")

    ctx = _domain_ctx(domain)
    system = (
        f"You are a {ctx['role']} specialising in failure-mode cascade analysis for "
        f"{ctx['industry']}. Given live telemetry and a forward prediction, identify "
        "whether degradation in one subsystem is likely to propagate to another. "
        "Format each cascade as: [System A] degradation -> [System B] impact in ~N "
        "minutes because [physics reason]. Be specific and grounded in the data. "
        f"Reference applicable standards ({ctx['compliance']}) where relevant. If no "
        "cascades are likely, say so clearly. Max 4-5 sentences.")
    return _text(system=system, messages=[{"role": "user", "content":
                 f"Machine: {machine}\n"
                 f"{_j({'diagnostics': diagnostics, 'prediction': prediction}, 7000)}"}],
                 max_tokens=4000, deep=True, stub=_stub)


# ══════════════════════════════════════════════════════════════════════════
# MAINTENANCE & COMPLIANCE OUTPUT
# ══════════════════════════════════════════════════════════════════════════

# ── Agent #11: Work Order ─────────────────────────────────────────────────

class WorkOrderStep(BaseModel):
    step: int = Field(description="Step number")
    action: str = Field(description="What the technician does")
    criteria: str = Field(description="Acceptance/inspection criteria")
    safety: str = Field(default="", description="Safety warning if applicable")


class WorkOrder(BaseModel):
    wo_number: str = Field(description="Work order number, e.g. WO-2026-001")
    ata_chapter: str = Field(description="Chapter/system reference for the domain, "
                                         "e.g. 'ATA 72 - Engine'")
    priority: str = Field(description="AOG, Critical, Routine, or Scheduled")
    compliance_ref: str = Field(description="Regulatory reference for this domain")
    fault_description: str = Field(description="Clear description of the fault")
    root_cause: str = Field(description="Identified or suspected root cause")
    steps: list[WorkOrderStep] = Field(description="Ordered repair/verification steps")
    estimated_hours: float = Field(description="Estimated labour hours")
    parts_required: list[str] = Field(default_factory=list)
    sign_off: str = Field(default="Level II Inspector",
                          description="Required sign-off authority")


def generate_work_order(diagnostics: dict, machine: str,
                        domain: str = "") -> WorkOrder:
    """A compliant maintenance work order generated from the diagnosis."""
    findings = diagnostics.get("findings", []) or []
    ctx = _domain_ctx(domain)

    def _stub() -> WorkOrder:
        critical = any(f.get("severity") == "critical" for f in findings)
        return WorkOrder(
            wo_number="WO-DRAFT-001",
            ata_chapter="(assign on review)",
            priority="Critical" if critical else "Routine",
            compliance_ref=ctx["compliance"],
            fault_description=(findings[0].get("message") if findings
                               else "Degradation detected by the behaviour engine"),
            root_cause="Root cause not yet established — requires inspection.",
            steps=[
                WorkOrderStep(step=1, action="Isolate the asset and apply LOTO",
                              criteria="Zero-energy state verified",
                              safety="Confirm all energy sources isolated before approach"),
                WorkOrderStep(step=2, action="Inspect the flagged subsystem",
                              criteria="Fault confirmed and localised"),
                WorkOrderStep(step=3, action="Service or replace the faulted component",
                              criteria="Component within manufacturer specification"),
                WorkOrderStep(step=4, action="Return to service and verify",
                              criteria="All monitored signals within limits for 10 minutes"),
            ],
            estimated_hours=4.0,
            parts_required=["To be determined at inspection"],
            sign_off="Qualified inspector per " + ctx["compliance"])

    rag = retrieve_context(_finding_query(diagnostics, machine), domain)
    system = (
        f"You are a {ctx['industry']} maintenance documentation system. Generate a "
        "compliant maintenance work order from this fault diagnosis. Reference "
        f"applicable standards ({ctx['compliance']}) — and prefer the compliance "
        "rules supplied below over any you recall; do not invent clause numbers. "
        "Each step must include acceptance criteria, and safety warnings where "
        "applicable. Language must be precise enough for a certified technician. "
        "Estimate realistic labour hours and list the specific parts and tools "
        "needed.")
    return _parse(system=system, messages=[{"role": "user", "content":
                  f"Machine: {machine}\nDiagnostics: {_j(diagnostics, 6000)}" + rag}],
                  max_tokens=12000, output_format=WorkOrder, stub=_stub)


# ── Agent #12: Parts Procurement ──────────────────────────────────────────

class PartEntry(BaseModel):
    part_number: str = Field(description="Part number / catalog reference")
    description: str = Field(description="What the part is")
    quantity: int = Field(default=1, description="How many needed")
    estimated_cost_usd: float = Field(default=0, description="Estimated unit cost")
    lead_time: str = Field(default="", description="Expected lead time")
    source: str = Field(default="", description="Supplier or stock location")


class ProcurementList(BaseModel):
    work_order_ref: str = Field(description="Reference work order number")
    total_estimated_cost: float = Field(description="Total estimated parts cost USD")
    critical_parts_available: bool = Field(
        description="Whether all critical-path parts are available within 24 hours")
    parts: list[PartEntry] = Field(description="Parts needed for the repair")
    notes: str = Field(default="", description="Procurement notes or warnings")


def parts_procurement_agent(work_order: dict, machine: str,
                            domain: str = "") -> ProcurementList:
    """Turn a work order into specific parts, quantities, lead times and costs."""
    def _stub() -> ProcurementList:
        parts = [PartEntry(part_number="TBD", description=p, quantity=1,
                           estimated_cost_usd=0, lead_time="Confirm with supplier",
                           source="Stores")
                 for p in (work_order.get("parts_required") or
                           ["To be determined at inspection"])]
        return ProcurementList(
            work_order_ref=work_order.get("wo_number", "WO-DRAFT-001"),
            total_estimated_cost=0.0, critical_parts_available=False, parts=parts,
            notes="Estimated offline — part numbers and costs need confirmation "
                  "against the live catalogue.")

    ctx = _domain_ctx(domain)
    system = (
        f"You are a {ctx['industry']} parts procurement specialist. Given a "
        "maintenance work order, identify the parts needed: catalogue part numbers, "
        "quantities, estimated unit costs in USD, lead times, and likely sources. "
        "Mark whether all critical-path parts can be on hand within 24 hours. Be "
        "specific and realistic; where you are estimating rather than quoting a "
        "known catalogue number, say so in the notes.")
    return _parse(system=system, messages=[{"role": "user", "content":
                  f"Machine: {machine}\nDomain: {normalize_domain(domain) or domain}\n"
                  f"Work Order: {_j(work_order, 5000)}"}],
                  max_tokens=10000, output_format=ProcurementList, stub=_stub)


# ── Agent #13: Incident Report ────────────────────────────────────────────

class IncidentReport(BaseModel):
    report_id: str = Field(description="Incident report number")
    classification: str = Field(description="System/chapter reference + fault classification")
    asset: str = Field(description="Asset type and serial/rig ID")
    timestamp: str = Field(description="When the incident was detected (ISO 8601)")
    symptoms: list[str] = Field(description="Observed symptoms with values")
    physics_evidence: str = Field(description="Physics residual analysis")
    probable_cause: str = Field(description="Most likely root cause")
    corrective_action: str = Field(description="Action taken or recommended")
    regulatory_closure: str = Field(description="Regulatory references for closure")
    return_to_service: str = Field(description="Criteria for return to service")


def generate_incident_report(diagnostics: dict, findings: list, machine: str,
                             domain: str = "") -> IncidentReport:
    """A formal incident report with regulatory closure references."""
    ctx = _domain_ctx(domain)

    def _stub() -> IncidentReport:
        syms = [(f.get("message") or f.get("displayName") or "")[:140]
                for f in (findings or [])[:5]] or ["No active findings recorded."]
        return IncidentReport(
            report_id="IR-DRAFT-001",
            classification="(assign on review)",
            asset=f"{machine} — {normalize_domain(domain) or 'asset'}",
            timestamp="(detection time not recorded)",
            symptoms=syms,
            physics_evidence="Residual analysis unavailable offline.",
            probable_cause="Requires engineering review.",
            corrective_action="Raise a work order and inspect the flagged subsystem.",
            regulatory_closure=ctx["compliance"],
            return_to_service="All monitored signals within limits at a stabilised "
                              "run, with qualified inspector sign-off.")

    system = (
        f"You are a {ctx['industry']} documentation specialist generating a formal "
        "incident report. Include the appropriate classification, observed symptoms "
        "with specific sensor values, physics-based evidence, probable cause, "
        "corrective action steps, regulatory closure references "
        f"({ctx['compliance']}), and return-to-service acceptance criteria. Be "
        "precise enough for regulatory submission, and do not state a value, a "
        "timestamp or a clause number that is not supported by the data supplied.")
    return _parse(system=system, messages=[{"role": "user", "content":
                  f"Machine: {machine}\nDiagnostics: {_j(diagnostics, 5000)}\n"
                  f"Findings: {_j((findings or [])[:5], 2000)}"}],
                  max_tokens=10000, output_format=IncidentReport, stub=_stub)


# ── Agent #14: Maintenance Procedure (interactive trainer) ────────────────

class TrainStep(BaseModel):
    id: str = Field(description="Short id like 'S1','S2' in the CORRECT order.")
    title: str = Field(description="Short step name.")
    action: str = Field(description="Exactly what the technician does.")
    rationale: str = Field(description="Why this step matters / what it achieves.")
    criteria: str = Field(description="How to confirm it was done correctly.")
    safety: bool = Field(default=False,
                         description="True if this is a safety / isolation / LOTO step.")
    requires: list[str] = Field(default_factory=list,
                                description="Ids of steps that MUST be completed first.")
    skip_consequence: str = Field(description="What goes wrong if this step is skipped.")
    wrong_order_consequence: str = Field(
        description="What goes wrong if done before its prerequisites.")


class MaintenanceProcedure(BaseModel):
    title: str = Field(description="Procedure title for this fault/scenario.")
    fault: str = Field(description="The fault id being repaired, or 'none'.")
    summary: str = Field(description="1-2 sentence overview of the repair.")
    steps: list[TrainStep] = Field(description="The correctly-ordered repair steps.")
    success_criteria: str = Field(description="How to confirm full restoration.")
    common_mistakes: list[str] = Field(default_factory=list,
                                       description="Frequent trainee mistakes and why "
                                                   "they are dangerous.")


def build_procedure(machine: str, domain: str, fault: str,
                    scenario_title: str = "",
                    scenario_context: str = "") -> MaintenanceProcedure:
    """A complete ordered repair procedure where every step also carries the
    consequence of skipping it or doing it out of order — the data the
    interactive maintenance trainer is built on."""
    def _stub() -> MaintenanceProcedure:
        return MaintenanceProcedure(
            title=f"Repair: {scenario_title or fault}", fault=fault or "none",
            summary="Isolate, diagnose, repair, verify.",
            steps=[
                TrainStep(id="S1", title="Isolate & make safe",
                          action="Apply LOTO and confirm a zero-energy state.",
                          rationale="Protects the technician before any intervention.",
                          criteria="Energy isolated and verified.", safety=True,
                          requires=[],
                          skip_consequence="Live-energy hazard during the repair.",
                          wrong_order_consequence="N/A — this must be first."),
                TrainStep(id="S2", title="Diagnose",
                          action="Confirm the faulted component from the telemetry.",
                          rationale="Targets the real root cause.",
                          criteria="Root cause confirmed.", requires=["S1"],
                          skip_consequence="You may repair the wrong component.",
                          wrong_order_consequence="Diagnosing live is unsafe and inaccurate."),
                TrainStep(id="S3", title="Repair",
                          action="Service or replace the faulted component.",
                          rationale="Restores the machine.",
                          criteria="Component within spec.", requires=["S1", "S2"],
                          skip_consequence="The fault persists.",
                          wrong_order_consequence="Repairing the wrong part wastes the window."),
                TrainStep(id="S4", title="Verify & return to service",
                          action="Re-run and confirm readings nominal.",
                          rationale="Proves the fix.",
                          criteria="All signals within limits.",
                          requires=["S1", "S2", "S3"],
                          skip_consequence="Undetected residual fault.",
                          wrong_order_consequence="Cannot verify before repairing."),
            ],
            success_criteria="All signals within limits and the fault cleared.",
            common_mistakes=["Skipping isolation (safety).",
                             "Repairing before diagnosing.",
                             "Returning to service without verification."])

    ctx = _domain_ctx(domain)
    extra = (f" arising from the situation: {scenario_title}. {scenario_context}"
             if scenario_title else "")
    system = (
        f"You are a master maintenance trainer for a {machine} in {ctx['industry']}. "
        f"Produce a complete, correctly-ordered repair procedure a trainee can "
        f"follow for the fault '{fault}'{extra}. For EACH step give: a short title, "
        "the action, the rationale, the acceptance criteria, whether it is a "
        "safety/isolation step, the ids of steps that must come first (requires), "
        "the consequence of SKIPPING it, and the consequence of doing it OUT OF "
        "ORDER. Order steps safety-first, diagnose before repair, verify last. List "
        "common trainee mistakes. Be specific to this machine and fault — name real "
        "components and signals.")
    return _parse(system=system, messages=[{"role": "user", "content":
                  f"Machine: {machine}\nDomain: {normalize_domain(domain) or domain}\n"
                  f"Fault: {fault}\nScenario: {scenario_title}\n"
                  f"Context: {scenario_context}"}],
                  max_tokens=14000, output_format=MaintenanceProcedure, stub=_stub)


# ══════════════════════════════════════════════════════════════════════════
# CONVERSATIONAL
# ══════════════════════════════════════════════════════════════════════════

# ── Agent #15: AI Mechanic (multi-turn troubleshooting) ───────────────────

class TroubleshootReply(BaseModel):
    reply: str = Field(description="The mechanic's conversational response")
    hypothesis: str = Field(default="", description="Current leading fault hypothesis")
    confidence: float = Field(default=0.0, description="Confidence in the hypothesis 0-1")
    resolved: bool = Field(default=False, description="True when diagnosis is conclusive")


def troubleshoot_chat(history: list[dict], message: str, diagnostics: dict,
                      machine: str, domain: str = "") -> TroubleshootReply:
    """Multi-turn diagnostic chat — asks clarifying questions like an experienced
    mechanic and narrows the fault hypothesis with each answer."""
    def _stub() -> TroubleshootReply:
        turn = len([h for h in history if h.get("role") == "user"]) + 1
        if turn <= 1:
            return TroubleshootReply(
                reply="Let's work through it. What's the primary symptom you're "
                      "seeing — temperature, vibration, pressure, or something else?",
                hypothesis="", confidence=0.0, resolved=False)
        if turn == 2:
            return TroubleshootReply(
                reply="Got it. When did this start — was it sudden or a gradual "
                      "trend? And did the duty cycle change recently?",
                hypothesis="Progressive degradation", confidence=0.3, resolved=False)
        return TroubleshootReply(
            reply="Based on what you've described and the current readings, I'd "
                  "inspect the flagged subsystem before the next run. Raise a work "
                  "order so the finding is tracked.",
            hypothesis="Subsystem degradation", confidence=0.5, resolved=False)

    ctx = _domain_ctx(domain)
    rag = retrieve_context(f"{message} {_finding_query(diagnostics, machine)}", domain)
    msgs = [{"role": h["role"], "content": h["content"]} for h in history[-10:]
            if h.get("content")]
    msgs.append({"role": "user", "content": message or "Where should I start?"})
    system = (
        f"You are an experienced {ctx['role']} helping a technician troubleshoot a "
        f"live {machine}. You can see the current sensor readings and physics "
        "residuals. Ask pointed diagnostic questions (1-2 per turn) to narrow down "
        "the fault. Be conversational but technical. When you have enough "
        "information, declare your hypothesis with a confidence. Reference specific "
        "sensor values and the relevant standard from the reference material below "
        f"({ctx['compliance']}) — do not invent clause numbers.\n"
        f"Current diagnostics: {_j(diagnostics, 4000)}{rag}")
    return _parse(system=system, messages=msgs, max_tokens=4000,
                  output_format=TroubleshootReply, deep=False, stub=_stub)


# ── Agent #16: Dashboard Copilot (live-state Q&A) ─────────────────────────

def dashboard_chat(messages: list, snapshot: dict, machine: str,
                   domain: str = "") -> str:
    """Live operations assistant — answers questions about the CURRENT machine
    status from its telemetry and findings."""
    def _stub() -> str:
        last = next((m.get("content", "") for m in reversed(messages or [])
                     if m.get("role") == "user"), "")
        return ("The dashboard copilot is running without an ANTHROPIC_API_KEY, so "
                f"I can't reason over the live state yet. You asked: {last[:140]}")

    ctx = _domain_ctx(domain)
    norm = [{"role": ("assistant" if m.get("role") == "assistant" else "user"),
             "content": str(m.get("content", ""))}
            for m in (messages or []) if m.get("content")]
    if not norm:
        norm = [{"role": "user", "content": "What's the current status?"}]
    system = (
        f"You are the live operations assistant for a {machine} ({ctx['industry']}). "
        "Answer the operator's questions about the CURRENT status using the "
        "telemetry and findings below. Be concise and specific, name the signals and "
        "their values, and flag anything concerning with a clear next action. Do not "
        "speculate beyond what the snapshot supports.\n"
        f"Current snapshot: {_j(snapshot, 5000)}")
    return _text(system=system, messages=norm, max_tokens=1500, effort=CHAT,
                 stub=_stub)
