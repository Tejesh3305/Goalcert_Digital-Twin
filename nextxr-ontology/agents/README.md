# NextXR Agents — the agentic core

Six MVP agents across two flows, one closed loop. Built on an in-house,
LangGraph-compatible engine so the code reads like the spec and can swap to real
LangGraph by changing one import.

## Layout

```
agents/
  state.py          TwinBuildState + BundleAuthorState (the typed contracts)
  engine.py         StateGraph / END / SqliteSaver — the LangGraph-compatible
                    executor + checkpointer (resumable, human-in-the-loop)
  gateway.py        LLMGateway — OpenAI when OPENAI_API_KEY is set, else a
                    deterministic stub. tenant_id-threaded, per-session call cap.
  registry.py       BundleRegistry — query/load/publish capability bundles
                    (built-in HVAC + bundles the Bundle Author publishes)
  twin_agents.py    the 5 twin-building agents + routers
  twin_graph.py     the twin-building StateGraph (Concierge→…→Graph Writer)
  bundle_agents.py  the Bundle Author's 5 nodes + human gate
  bundle_graph.py   the Bundle Author sub-graph
  loop_test.py      end-to-end proof of the closed loop (no UI)
```

## The two flows

**Twin-building** (entry: `twin_graph.app`)
```
concierge ──ask──▶ END (yield to human)
   │ classify
classifier ──low──▶ concierge
   │ ok
composer ──▶ validator ──fail──▶ concierge
                  │ ok
              graph_writer ──▶ END
```

**Bundle Author** (entry: `bundle_graph.app`)
```
interviewer ──interview──▶ END (yield to expert)
   │ draft
drafter ──▶ rule_author ──▶ linter ──fail──▶ END (yield to refine)
                              │ ok
                          approval_gate ──wait──▶ END (yield for approval)
                              │ publish (approved=true)
                          publisher ──▶ END
```

## The closed loop

1. Bundle Author publishes a new vertical (with a Tier-C rule) → registry **and**
   the live ontology (fragment registered into `packs/published/`).
2. Concierge → Classifier → Composer: the Composer loads **that** bundle (the
   Classifier knows published domains, so a just-authored vertical is routable).
3. Validator → Graph Writer: a live twin is committed; Change Log + event bus fill.
4. Dashboard shows the twin; the feed can run its Tier-C rule.

Prove it: `python -m agents.loop_test` (needs `docker compose up -d neo4j redis`).

## Five disciplines (enforced)

| Discipline | Where |
|---|---|
| State, not calls | agents are pure `(state)->update`; the graph owns flow |
| One writer | only `graph_writer` / `publisher` mutate persistent stores |
| tenant_id everywhere | threaded through State from entry; every Gateway call carries it |
| Idempotent commits | UUIDv7 ids assigned by the Validator, reused by the Writer |
| Human gate before publish | `approval_gate` interrupts; Publisher re-checks `approved` |

## LLM layer

`gateway.py` uses OpenAI (`OPENAI_API_KEY` from `.env`, model `NXR_LLM_MODEL`,
default `gpt-4o-mini`) and falls back to deterministic stubs if no key / on any
API error — so the whole flow runs keyless too. Per-session cap:
`NXR_LLM_MAX_CALLS` (default 100). To swap to Anthropic Gateway, change
`gateway.py` only.

## HTTP API (mounted in server/main.py)

```
GET  /api/v1/agents/info
POST /api/v1/agents/twin/start | /twin/message        GET /twin/{session}
POST /api/v1/agents/bundle/start | /bundle/message | /bundle/approve   GET /bundle/{session}
```
A request runs the graph until it interrupts (asks the human) or reaches END,
then returns the public state. Routes are sync `def` so blocking LLM calls run
in FastAPI's threadpool and don't stall the event loop.

## UI

- **Build a Twin** (`/build`) — Concierge chat + a live agent-pipeline rail.
- **Bundle Author** (`/bundle-author`) — author a vertical, see the drafted
  ontology + Tier-C rule, and the **Approve & Publish** human gate.

## Notes / caveats

- LLM Turtle output varies; the Ontology Drafter prepends missing prefixes and
  falls back to a deterministic, lint-clean fragment if the model's output won't
  parse. The Linter accepts `owl:Class` or `rdfs:Class`. If a generation still
  trips lint, the flow yields to the expert to refine (no hang, no loop).
- Checkpoints persist to `data/agent_checkpoints.db`; published bundles to
  `data/bundles.db` + `packs/published/*.ttl`. Delete those to reset.
