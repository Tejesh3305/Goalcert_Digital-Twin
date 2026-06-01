# NextXR Frontend — what's real vs. placeholder

This frontend is wired to the live platform where the core supports it, and
uses **clearly-labelled placeholders** where the core doesn't exist yet. Every
placeholder panel shows an amber "Demo placeholder" banner in the UI; this file
is the engineering map of what each one needs to become real.

Backing legend (also encoded in `src/nav.js` as `backing`):
- **live** — real data from the platform backend.
- **partial** — partly real (graph-backed) + partly mocked; mocked bits are tagged.
- **mock** — presentational only; no core support yet.

---

## LIVE — fully backed by the core today

| Panel | Route | What's real |
|---|---|---|
| **Dashboard** | `/` | KPIs, findings, severity breakdown, risk score — all from `/stats` + `/findings` for the active twin. Feed start/stop is real. |
| **Twins** | `/twins` | List/create/delete real twins. **Create seeds real entities** (Site/Space/AirHandler) through the Graph Writer → SHACL gate → Neo4j → Change Log → event bus. |
| **Asset Graph** | `/assets` | Every entity + properties + relationships from the graph. **Add Asset** creates any ontology type through the validated write path; SHACL rejections are shown live. |
| **Live Ops** | `/live` | **SSE stream off the event bus** (`/bus/stream`) — every committed mutation in real time. Live feed sensors. Incidents from the graph. |
| **Change Log** | `/changelog` | The real hash-chained, tamper-evident ledger from `/changelog`. |

These exercise the whole backbone you built: ontology gate, single write path,
change log, **event bus**, tenant isolation, and the read API.

---

## PARTIAL — real spine, mocked surface

### AI Agents — `/agents`
- **Real:** the diagnosis pipeline (`behaviors/diagnosis.py`) runs server-side as
  the feed loops, writing Incident / Diagnosis / Recommendation / Action entities
  through the write path. The panel surfaces those live (the "LIVE" badges).
- **Mock:** the agent roster + on/off toggles. There is no independent agent
  runtime — diagnosis is invoked inline by the feed loop, not by autonomous agents.
- **To make real — "Agent runtime":** stand up agents as **event-bus consumers**.
  Create a Redis consumer group per agent on `nxr:events:<tenant>`; each agent
  reacts to relevant events (e.g. a Finding `create`), calls the Graph Query API
  to gather context, optionally an LLM to decide, and writes results back through
  the Graph Writer. The bus is already built for exactly this — that's why we did
  it first. Toggles would enable/disable each consumer.

### Twin Health — `/health`
- **Real:** structural coverage derived from actual entity counts per category.
- **Mock:** the weighted completeness score nuance + BIM/regulatory freshness rows.
- **To make real — "Twin health scoring":** define expected-vs-present coverage per
  pack (e.g. "an HVAC twin should have ≥1 Sensor per AirHandler"), compute from the
  graph, and track ingestion timestamps per data source.

---

## MOCK — no core support yet (illustrative UI)

### Predict — `/predict`  → "Predictive engine"
Illustrative RUL forecasts. **Real version:** a Tier-B/ML behaviour that, instead
of a z-score, fits a degradation model and writes a `FailureMode` (+ predicted
remaining-useful-life property) and a `Recommendation` through the write path.
Slots behind the existing `Behavior` interface — no new plumbing.

### Copilot — `/copilot`  → "Copilot"
Rule-based replies with live-stat grounding. **Real version:** an LLM (Claude)
with tool access to the **schema-query API** (`/api/v1/schema/*`) and the **graph
read API** (`/api/v1/entities`, `/findings`, `/changelog`). The schema service
already exists precisely so an agent can ask the ontology instead of hardcoding it.

### Compliance — `/compliance`  → "Compliance twin"
Illustrative standard coverage + gaps. **Real version:** model standards/clauses
as `Document` entities, and create `Asset →(governedBy)→ Clause →(evidencedBy)→
Document` relationships in the graph (all via the write path). Gap detection = a
behaviour/query for assets missing required clause-evidence edges.

### Simulation — `/simulation`  → "Simulation / what-if"
Scripted scenario impacts. **Real version:** fork the twin into a scenario tenant
(copy entities under a `tenant_id` like `mytwin@scenario-flood`), inject altered
conditions, replay behaviours, and diff outcomes against the base twin.

### Marketplace — `/marketplace`  → "Pack marketplace"
Mocked install buttons. **Real version:** the platform already has a real pack
mechanism (`packs/hvac` is loaded at boot). A marketplace = runtime pack
load/unload: parse a pack's TTL into the ontology graph, refresh the schema
service + Neo4j constraints, and register its behaviours. The "load HVAC pack,
unload, reload live" capability from your June-1 list lives here.

---

## Cross-cutting next steps (priority order)

1. **Agent runtime on the bus** — the highest-leverage next piece and the reason
   the event bus came first. Turns the "partial" Agents panel fully real and is
   the foundation for an agentic platform.
2. **Real telemetry adapters** — today the feed is simulated (`feed/simulate.py`).
   A real MQTT/OPC-UA/BACnet adapter publishes `TelemetrySample`s into the same
   registry; nothing downstream changes.
3. **Predictive (Tier-B/ML) behaviour** — makes Predict real, reuses the behaviour
   interface.
4. **LLM copilot** — wire Claude to the schema + graph APIs as tools.
5. **Pack marketplace (runtime load/unload)** — makes Marketplace real and
   completes the "any domain on one backbone" story.
