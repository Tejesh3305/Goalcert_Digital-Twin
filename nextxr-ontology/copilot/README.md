# copilot — the embedded agent layer

The digital twin's own agents. **Not a connector to an external agent service** —
these run in this process, import the twin's runtime directly, and persist to the
twin's own data volume. There is no orchestrator to deploy alongside the twin and
nothing to keep in sync.

```
HTTP  →  server/copilot_routes.py  →  copilot/agents.py  →  Anthropic API
                    │
                    └─ twins/runtime.py  get_machine_engine().ensure(tenant)
                          └─ the SAME LiveTwin the 3-D scene and findings read
```

An agent answer is grounded in the same physics frame the dashboard is rendering,
because it is literally the same object in memory — not a snapshot fetched over a
network boundary that may already be stale.

## The agents

| # | Agent | Function | Endpoint |
|---|---|---|---|
| 1 | Twin Builder | `build_twin_reply` | `POST /build-twin/message` |
| 2 | Vision | `vision_to_twin_spec` | `POST /build-twin/spec` |
| 3 | Narration | `narrate_sensors` | `GET /narrate/{tenant}`, `POST /narrate` |
| 4 | Asset Status | `asset_status` | `POST /asset` |
| 5 | Predictive Alert | `predictive_alert` | `POST /predict-alert` |
| 6 | Diagnosis | `diagnosis_agent` | `POST /diagnosis` |
| 7 | Analysis | `analysis_agent` | `POST /analysis` |
| 8 | Cascade | `cascade_analysis` | `POST /cascade` |
| 9 | Work Order | `generate_work_order` | `POST /work-order` |
| 10 | Parts Procurement | `parts_procurement_agent` | `POST /procurement` |
| 11 | Incident Report | `generate_incident_report` | `POST /incident-report` |
| 12 | Maintenance Procedure | `build_procedure` | `POST /procedure` |
| 13 | AI Mechanic | `troubleshoot_chat` | `POST /troubleshoot` |
| 14 | Dashboard Copilot | `dashboard_chat` | `POST /dashboard-chat` |

All paths are under `/api/v1/copilot`.

Scenario-engine agents are deliberately absent: the twin already has a what-if
surface in `twins/runtime.py` (`project()`), reachable at
`POST /api/v1/twins/{tenant}/project`. Adding a second one would fork the physics.

### One agent, two sources

The prototype had separate live and snapshot endpoints for diagnosis, forecast,
work orders and cascade. They were the same agent twice. Here each endpoint takes
**either**:

* `{"tenant": "twin-4e9…"}` → reads the live twin's real diagnostics and, where
  applicable, its forward physics projection; or
* `{"machine": "...", "domain": "...", "latest": {...}, "findings": [...]}` →
  runs the identical agent on caller-supplied telemetry.

The response echoes `source: "live" | "snapshot"` so you always know which ran.
`/analysis` additionally reports `forecast_basis`: `physics` on a live twin (it
integrates the twin's own model forward), `qualitative` on a snapshot (there is
no state object to integrate).

## Telling a real answer from a fallback

Every agent is keyless-safe: with no `ANTHROPIC_API_KEY` it returns a
deterministic stub rather than failing. That is good for uptime and dangerous for
trust — a stubbed work order looks like a real one. So every response carries:

```json
"ai": { "backend": "claude", "model": "claude-sonnet-5", "thinking": true,
        "usage": {"input": 916, "output": 831}, "error": null }
```

`backend` is one of:

| value | meaning |
|---|---|
| `claude` | real reasoning |
| `stub` | fallback — read `error` for why (no key, API error, refusal) |
| `not_applicable` | the agent correctly had nothing to say (e.g. no limit crossing to alert on) |

`GET /api/v1/copilot/health` reports the mode up front.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables real reasoning. Unset ⇒ everything stubs. |
| `NXR_CLAUDE_MODEL` | `claude-sonnet-5` | Model override — e.g. `claude-opus-4-8` where deep-agent output quality matters more than the token bill. |
| `NXR_COPILOT_EFFORT` | `high` | Effort for the deep agents (`low`…`max`). |

### Latency, and why it varies

Thinking is **on** for the agents whose output is documentation someone acts on,
and **off** for the ones a console polls. Measured on Opus 4.8:

| Agent | Thinking | Typical |
|---|---|---|
| narration, asset status, chat | off | 3–7 s |
| diagnosis, analysis, cascade | adaptive | 12–25 s |
| work order, incident report | adaptive | 30–35 s |
| maintenance procedure | adaptive | ~70 s |

Don't put the 30 s+ agents behind a synchronous button with no progress state.
If you need them faster, drop `NXR_COPILOT_EFFORT` to `medium` before you reach
for a smaller model — effort costs less quality than a downgrade does.

## Domain normalisation

Domains are this platform's pack keys (`turbine-engine`, `railway-metro`,
`hospital-campus`, …). The prototype's names (`mrt-line`, `ev-network`,
`hospital`) are accepted and normalised — see `DOMAIN_ALIASES`.

> The seeded RAG knowledge store (fault library / compliance rules / incident
> memory) that used to ground Diagnosis, Work Order and Troubleshoot was
> **retired** from the twin. `retrieve_context` remains as a no-op so those
> agents' call sites are unchanged; they now reason from the live twin's own
> diagnostics and the domain's compliance regime rather than a similarity search.
> The `/knowledge/*` endpoints and the Resolution-Memory agent were removed with it.
