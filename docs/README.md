# NextXR Digital Twin — documentation

## Current (generated from the live system, 2026-09-07)

| Document | What it answers |
|---|---|
| [**API-SPECIFICATION.md**](API-SPECIFICATION.md) | The 188 routes, the auth model, the error contract, per-group detail |
| [**ER-DIAGRAM.md**](ER-DIAGRAM.md) | Every table and column, the Neo4j graph model, and the seam between them |
| [**AWS-ARCHITECTURE.md**](AWS-ARCHITECTURE.md) | Component topology, the four stores, failure modes, security posture |
| [SetupDocs/nextxr_openapi.json](SetupDocs/nextxr_openapi.json) | Machine-readable OpenAPI 3.1.0 — import into Postman, Swagger UI, Redoc |

All three were built by introspecting the running application and databases
rather than transcribed from design notes, so they describe what the system
does rather than what it was meant to do. Diagrams are Mermaid and render
natively on GitHub.

**Related, at the repository root:**
[`AWS_DEPLOYMENT.md`](../AWS_DEPLOYMENT.md) is the operational runbook
(provisioning, deploy procedure, backup/recovery) and remains the authority for
*how to deploy*; AWS-ARCHITECTURE.md is the *why it is shaped this way* view.
[`nextxr-ontology/work/README.md`](../nextxr-ontology/work/README.md) documents
the dispatch package in depth.

## Regenerating

```bash
cd nextxr-ontology
python - <<'PY'
import sys, json; sys.path.insert(0, ".")
from server.main import app
json.dump(app.openapi(), open("../docs/SetupDocs/nextxr_openapi.json","w"), indent=1)
PY
```

No running services are needed — the app builds its route table at import and
degrades cleanly without Neo4j or Redis. The prose documents are maintained by
hand; the row counts and route totals in them carry their extraction date, so a
stale figure is visible rather than silent.

## PDF set

`docs/pdf/` holds the same three documents as print-ready PDFs, for handing to
someone who is not going to clone a repository:

| PDF | Pages |
|---|---|
| `NextXR_API_SPECIFICATION.pdf` | 11 |
| `NextXR_ER_DIAGRAM.pdf` | 17 (2 landscape) |
| `NextXR_AWS_ARCHITECTURE.pdf` | 12 (2 landscape) |

Every Mermaid diagram is rendered to vector SVG and embedded, so the text in
them stays selectable and searchable rather than being a screenshot. Diagrams
wider than they are tall are placed on their own landscape page — a wide figure
squeezed into the portrait column drops its labels to about 3pt, which is
present but unreadable.

Rebuild with:

```bash
python docs/build_pdfs.py                    # all three
python docs/build_pdfs.py ER-DIAGRAM.md      # just one
```

Requirements: Node (the script fetches `mermaid-cli` and `marked` through
`npx -y`, caching them outside the repo) and Chrome or Edge, which the script
locates itself — set `CHROME` if it is somewhere unusual. Nothing is installed
into the project's Python environment.

## Superseded

`TechDocs/` holds the previous generated set — `NextXR_ERD.svg`,
`NextXR_AWS_Architecture.docx/html`, `NextXR_Component.html`,
`NextXR_DataFlow.html`, the `.docx` HLD/LLD/FRD/DRD, and their PNGs — produced
by the `gen_*.py` builders on **23 Aug 2026**.

**They predate the identity, persona, work and scenario subsystems.** The ERD in
particular contains none of `tasks`, `xp_ledger`, `scenario_runs`, `memberships`
or `teams`, and the OpenAPI YAML that sat beside it described a materially
smaller API with a different auth model — it was removed rather than left to
contradict the current spec (recover from git history if needed).

Treat `TechDocs/` as a historical snapshot. The `gen_*.py` builders still work
and could be re-run, but they generate Word/HTML deliverables for a formal
document pack rather than the repo-native Markdown above; regenerate them only
when that pack is actually being reissued.
