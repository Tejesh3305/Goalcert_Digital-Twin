"""
twin_agents.py — the five twin-building agents + their routers.

Each agent is a pure function `(state) -> partial_update`. They read keys they
need and write keys they own; they NEVER call each other (the graph owns flow).
Routers are pure `(state) -> key` functions feeding the conditional edges.

Build order mirrors the spec (most fundamental first):
    Graph Writer · Validator · Capability Composer · Domain Classifier · Concierge
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TOOLS = ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))  # so `import gate` (the SHACL write-gate) works

from agents.gateway import get_gateway
from agents.registry import get_registry

CORE = "https://ontology.nextxr.io/v3/core#"

# Known demo verticals the Domain Classifier chooses from.
# NOTE: vague terms like "building", "facility" are NOT here — they're handled
# in the low-confidence path so the Concierge asks for clarification.
KNOWN_DOMAINS = ["hvac", "cooling", "maritime", "energy", "manufacturing"]
CONFIDENCE_THRESHOLD = 0.7


def _new_id_safe() -> str:
    """Time-ordered UUIDv7 id (reuses the platform generator)."""
    from graph.crud import _new_id
    return _new_id()


# ==========================================================================
# 5 · Graph Writer   (deterministic — the only node that mutates the graph)
# ==========================================================================
def graph_writer(state: dict) -> dict:
    """Commit validated drafts via the platform's single write path
    (GraphWriter → SHACL gate → Neo4j → Change Log → event bus). Idempotent on
    UUIDv7 keys. Resolves template `key` references to real node ids.

    On a connection error it RAISES (per spec) so the workflow can retry the
    same idempotent op — it does NOT loop back to Concierge."""
    from graph.writer import GraphWriter, Rel
    from graph.sensor_defaults import inject_observes
    from changelog.service import ChangeLog
    from twins import TwinRegistry

    tenant_id = state["tenant_id"]
    drafts = state.get("draft_entities", [])
    rel_drafts = state.get("draft_relationships", [])

    writer = GraphWriter(changelog=ChangeLog())

    # Ensure schema exists (idempotent); keep the shared driver open.
    try:
        from graph import schema
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            schema.apply_schema(dry_run=False, close=False)
    except Exception:
        pass

    # Pre-resolve the draft ids the Validator assigned (idempotent + consistent
    # with what was validated). Map template key -> id up front so an entity can
    # be created WITH its outgoing relationships in one atomic create (some SHACL
    # shapes require the relationship at creation, e.g. AirHandler servesSpace).
    key_to_id: dict[str, str] = {}
    for ent in drafts:
        ent.setdefault("_draft_id", _new_id_safe())
        if ent.get("key"):
            key_to_id[ent["key"]] = ent["_draft_id"]

    # Group outgoing relationships by their source entity's draft id.
    out_rels: dict[str, list] = {}
    for rel in rel_drafts:
        src = key_to_id.get(rel.get("source_key"), rel.get("source_id"))
        tgt = key_to_id.get(rel.get("target_key"), rel.get("target_id"))
        if src and tgt:
            out_rels.setdefault(src, []).append(Rel(predicate=rel["predicate"], target_id=tgt))

    # Create targets before the sources that point at them, so a relationship's
    # target already exists when its source is written.
    #
    # This is a real topological sort, not a "does it have any outgoing edge"
    # split. That earlier heuristic only worked while leaf entities happened to
    # have no relationships; as soon as every entity has one (e.g. each sensor
    # monitors the machine), nothing sorted first and the commit failed with
    # "Relationship target(s) not found".
    by_id = {e["_draft_id"]: e for e in drafts}
    deps: dict[str, set] = {nid: set() for nid in by_id}
    for nid, rels in out_rels.items():
        for r in rels:
            if r.target_id in by_id and r.target_id != nid:
                deps.setdefault(nid, set()).add(r.target_id)

    ordered_ids: list[str] = []
    created: set[str] = set()
    remaining = {n: set(d) for n, d in deps.items()}
    while remaining:
        ready = sorted(n for n, d in remaining.items() if d <= created)
        if not ready:
            # A dependency cycle (A serves B, B monitors A). Nothing can be
            # created edges-intact, so break it at the node with the fewest
            # unmet deps; its back-edges are added after every node exists.
            ready = [min(remaining, key=lambda k: (len(remaining[k] - created), k))]
        for n in ready:
            ordered_ids.append(n)
            created.add(n)
            remaining.pop(n, None)

    primary_id = None
    errors = []
    deferred: list[tuple[str, object]] = []   # (source_id, Rel) applied after
    done: set[str] = set()
    for nid in ordered_ids:
        ent = by_id[nid]
        # Hold back only edges whose target is a draft we haven't written yet
        # (cycle back-edges). Ontology refs like sosa:observes have non-draft
        # targets and must stay on the create call — some shapes require them.
        now, later = [], []
        for r in out_rels.get(nid) or []:
            (later if (r.target_id in by_id and r.target_id not in done) else now).append(r)
        deferred.extend((nid, r) for r in later)

        # Same sosa:observes injection the Validator applied, so what commits is
        # exactly what passed the gate.
        rels = inject_observes(ent["canonical_type"], now)
        res = writer.create(
            tenant_id=tenant_id,
            canonical_type=ent["canonical_type"],
            actor="agent:graph_writer",
            properties=dict(ent.get("properties", {})),
            relationships=rels or None,
            node_id=nid,
        )
        if not res.ok:
            errors.append(f"{ent.get('canonical_type','?').split('#')[-1]}: {res.error}")
            continue
        done.add(nid)
        if primary_id is None and res.label == "PhysicalAsset":
            primary_id = res.node_id

    # Now that every node exists, close the cycle edges.
    for src, r in deferred:
        if src not in done or r.target_id not in done:
            continue
        rr = writer.relate(tenant_id=tenant_id, actor="agent:graph_writer",
                           source_id=src, predicate=r.predicate,
                           target_id=r.target_id)
        if not rr.ok:
            errors.append(f"relationship {r.predicate}: {rr.error}")

    committed_count = len(drafts) - len(errors)
    if errors and committed_count == 0:
        # Nothing committed — surface as a validation-style failure, not a crash.
        return {"committed": False, "errors": state.get("errors", []) + errors,
                "next_action": "ask",
                "reply_to_user": "I couldn't commit the twin: " + "; ".join(errors)}

    twin_id = tenant_id  # a twin == a tenant
    # Register the twin so it appears in the Twins list + the feed can target it.
    try:
        reg = TwinRegistry()
        if reg.get(tenant_id) is None:
            from twins.service import Twin, _now_iso
            reg._insert(Twin(
                tenant_id=tenant_id,
                name=state.get("twin_name") or state.get("domain", "Twin").title(),
                domain=state.get("domain") or "hvac",
                description=f"Authored by the agent flow from "
                            f"{', '.join(state.get('loaded_bundles', [])) or 'a bundle'}.",
                created_at=_now_iso(), seed_asset_id=primary_id,
            ))
        elif primary_id:
            reg._set_seed_asset(tenant_id, primary_id)
    except Exception:
        pass

    msg = f"Your twin is live ({committed_count} entities committed)."
    if errors:
        msg += f" ({len(errors)} entity/-ies were skipped: {'; '.join(errors)})"
    return {"committed": True, "twin_id": twin_id, "next_action": "done",
            "errors": state.get("errors", []) + errors,
            "reply_to_user": msg + " You can watch it on the dashboard."}


# ==========================================================================
# 4 · Validator   (deterministic — pure code, SHACL + completeness)
# ==========================================================================
def validator(state: dict) -> dict:
    """Validate every draft entity through the SHACL write gate, exactly as the
    Graph Writer will. We reuse the writer's OWN Turtle renderer and assign each
    draft a real UUIDv7 id + the tenant, so what passes here is what commits.
    Relationship cardinality (e.g. "an air handler must serve a space") is
    checked by rendering each entity WITH its outgoing draft relationships.

    Errors are human-readable so the Concierge can voice them. Pure code."""
    import gate  # tools/gate.py
    from graph.writer import GraphWriter, Rel
    from graph.crud import _new_id
    from graph.sensor_defaults import inject_observes

    drafts = state.get("draft_entities", [])
    rel_drafts = state.get("draft_relationships", [])
    errors = []

    if not drafts:
        return {"validation": {"ok": False, "errors": [
            {"path": "draft_entities", "reason": "No entities were composed."}]},
            "next_action": "ask"}

    # Assign each draft a real id (keyed by template key) so relationships and
    # the UUIDv7 base-shape check resolve like a real commit.
    key_to_id = {}
    for ent in drafts:
        ent_id = _new_id()
        ent["_draft_id"] = ent_id
        if ent.get("key"):
            key_to_id[ent["key"]] = ent_id

    # Build a writer purely to reuse its renderer (no DB writes happen here).
    w = GraphWriter.__new__(GraphWriter)  # bypass __init__ (no driver needed)

    for ent in drafts:
        node_id = ent["_draft_id"]
        ct = ent["canonical_type"]
        props = {
            "id": node_id, "tenantId": state["tenant_id"],
            "canonicalType": ct,
            "createdAt": _now_iso_z(), "updatedAt": _now_iso_z(),
            "createdBy": "agent:validator",
            **{k: v for k, v in ent.get("properties", {}).items()},
        }
        # Outgoing relationships for THIS entity (resolve template keys to ids).
        rels = []
        for r in rel_drafts:
            if key_to_id.get(r.get("source_key")) == node_id:
                tgt = key_to_id.get(r.get("target_key"), r.get("target_id"))
                if tgt:
                    rels.append(Rel(predicate=r["predicate"], target_id=tgt))
        # A Sensor must declare sosa:observes. That is an ontology fact the
        # drafting model shouldn't have to know, so inject it here exactly as the
        # REST write path does — otherwise every sensor the agent drafts fails
        # the shape and the twin can never be built.
        rels = inject_observes(ct, rels)
        try:
            # Rendering must be inside the guard: `rels` carries LLM-drafted
            # predicates, so a malformed one has to surface as a validation
            # error the agent can report and correct — not a 500.
            ttl = w._render_node_ttl(node_id, ct, props, rels)
            result = gate.validate(ttl)
            if not result.ok:
                for v in result.violations:
                    errors.append({"path": ct.split("#")[-1],
                                   "reason": _humanize(str(v))})
        except Exception as e:
            errors.append({"path": ct.split("#")[-1],
                           "reason": f"validation error: {e}"})

    ok = len(errors) == 0
    return {"validation": {"ok": ok, "errors": errors},
            "next_action": "commit" if ok else "ask"}


def _now_iso_z() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _humanize(violation: str) -> str:
    """Trim SHACL violation noise into something the Concierge can speak."""
    for sep in (" — ", " - ", ": "):
        if sep in violation:
            tail = violation.split(sep)[-1].strip()
            if tail and "conform to Shape" not in tail:
                return tail
    return violation.strip()


# ==========================================================================
# 3 · Capability Composer   (hybrid — deterministic load, templates → drafts)
# ==========================================================================
def capability_composer(state: dict) -> dict:
    """Query the registry for a bundle matching the domain, load it, and turn
    its templates into draft entities/relationships (MVP: no free-text mapping).
    The demo's plug-and-play moment — surfaces which bundle loaded."""
    registry = get_registry()
    domain = state.get("domain") or ""
    matches = registry.query(domain)

    if not matches:
        return {"loaded_bundles": [], "draft_entities": [], "draft_relationships": [],
                "next_action": "ask",
                "errors": state.get("errors", []) + [f"No bundle for domain '{domain}'."],
                "reply_to_user": f"We don't support '{domain}' yet — no capability "
                                 f"bundle is available. Want to author one?"}

    bundle = registry.load(matches[0]["bundle_id"])
    twin_name = state.get("twin_name") or f"{domain.title()} Twin"

    # Templates → drafts. Personalise the root display name.
    entities = []
    for t in bundle.get("entity_templates", []):
        props = dict(t.get("properties", {}))
        if t.get("key") == "site":
            props["displayName"] = twin_name
        entities.append({"key": t.get("key"),
                         "canonical_type": t["canonical_type"],
                         "properties": props})
    rels = [dict(r) for r in bundle.get("relationship_templates", [])]

    return {"loaded_bundles": [bundle["bundle_id"]],
            "draft_entities": entities, "draft_relationships": rels,
            "next_action": "validate",
            "reply_to_user": f"Loaded the **{bundle['name']}** bundle "
                             f"({len(entities)} entities, {len(bundle.get('rules', []))} rule(s)). "
                             f"Validating before commit…"}


# ==========================================================================
# 2 · Domain Classifier   (LLM — structured, low temperature)
# ==========================================================================
def domain_classifier(state: dict) -> dict:
    """Pick a vertical + confidence from the conversation. Never guesses past
    the threshold — low confidence routes back to the Concierge.

    The vertical list is dynamic: it includes the domains of any PUBLISHED
    bundles, so a vertical the Bundle Author just authored is immediately
    classifiable (this is what makes the closed loop land on the new bundle)."""
    gw = get_gateway()
    convo = _convo_text(state)
    intent = state.get("user_intent") or ""

    # Domains available right now = built-in known + every published bundle's domains.
    # Also gather bundle metadata (names, entity catalogues) for richer matching.
    published_domains = []
    published_bundles_meta = []  # [{domains, name, entities, rules}]
    try:
        for b in get_registry().list_published():
            published_domains += [d for d in b.get("domains", [])]
            full = get_registry().load(b["bundle_id"])
            if full:
                published_bundles_meta.append({
                    "domains": full.get("domains", []),
                    "name": full.get("name", ""),
                    "entities": [t.get("canonical_type", "").split("#")[-1]
                                 for t in full.get("entity_templates", [])],
                    "primary_signal": full.get("primary_signal", ""),
                })
    except Exception:
        pass
    # Published domains first so they win on a keyword match.
    available = list(dict.fromkeys(published_domains + KNOWN_DOMAINS))

    def _stub() -> dict:
        text = (convo + " " + intent).lower()
        best, conf = None, 0.0

        # 1. Exact domain name match (published first).
        for d in available:
            if d and d.lower() in text:
                best, conf = d, 0.9
                break

        # 2. Match against published bundle entity names and bundle names.
        if best is None:
            for bm in published_bundles_meta:
                bundle_text = " ".join(bm["domains"] + bm["entities"] +
                                       [bm["name"]]).lower()
                # Check if any word from the conversation appears in the bundle.
                for word in text.split():
                    if len(word) > 3 and word in bundle_text:
                        best = bm["domains"][0] if bm["domains"] else None
                        conf = 0.85
                        break
                if best:
                    break

        # 3. Keyword heuristics (lowered confidence for vague terms).
        if best is None:
            for kw, d, c in [
                ("refriger", None, 0.85), ("cold storage", None, 0.85),
                ("freezer", None, 0.85), ("cool", "cooling", 0.8),
                ("air condition", "hvac", 0.85), ("temperature", "hvac", 0.75),
                ("ship", "maritime", 0.85), ("port", "maritime", 0.8),
                ("power", "energy", 0.8), ("server room", "hvac", 0.8),
                ("data cent", "hvac", 0.8),
            ]:
                if kw in text:
                    match = d
                    for pd in published_domains:
                        if kw.split()[0] in pd.lower() or pd.lower() in text:
                            match = pd
                            break
                    if match:
                        best, conf = match, c
                        break

        # 4. Very vague terms get LOW confidence (forces Concierge to clarify).
        if best is None:
            for kw, d in [("building", "facility"), ("plant", "facility"),
                          ("room", "facility"), ("facility", "facility")]:
                if kw in text:
                    best, conf = d, 0.5  # below threshold — asks for clarification
                    break

        return {"domain": best or "hvac", "sub_type": None,
                "confidence": conf if best else 0.4}

    # Build a richer context for the LLM including bundle descriptions.
    bundle_hints = ""
    if published_bundles_meta:
        lines = [f"  - {bm['name']}: domains={bm['domains']}, "
                 f"entities={bm['entities'][:5]}" for bm in published_bundles_meta]
        bundle_hints = "\n\nPublished bundles (prefer these):\n" + "\n".join(lines)

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You classify a facility-twin request into ONE vertical. "
                f"Choose `domain` from this list (earlier entries are preferred "
                f"when they fit): {available}. Return JSON "
                '{"domain": str, "sub_type": str|null, "confidence": 0.0-1.0}. '
                "Set confidence below 0.7 if the conversation is too vague or "
                "generic (e.g. just 'building' with no specifics). Prefer "
                "published bundles over generic built-in domains."),
        user=f"Conversation:\n{convo}\n\nStated intent: {intent}{bundle_hints}",
        stub=_stub(),
    )
    domain = (result.get("domain") or "hvac").lower()
    conf = float(result.get("confidence", 0.0) or 0.0)
    return {"domain": domain, "sub_type": result.get("sub_type"),
            "domain_confidence": conf,
            "next_action": "compose" if conf >= CONFIDENCE_THRESHOLD else "ask"}


# ==========================================================================
# 1 · Concierge   (LLM — user-facing guide; never writes the graph)
# ==========================================================================
def concierge_agent(state: dict) -> dict:
    """Jargon-free guide. Learns facility type + rough scope, then proceeds.
    Voices any Validator/Classifier loop-back reason and re-asks. Sets
    ready_to_classify only when type + rough scope are known.

    Emits an interrupt to yield the turn back to the human when more input is
    needed (next_action='ask')."""
    from agents.engine import INTERRUPT_KEY
    gw = get_gateway()
    convo = _convo_text(state)

    # If we just looped back from Validator/Classifier, lead with the reason.
    loopback = _loopback_reason(state)

    # Load domain-specific elicitation questions from published bundles.
    # These were authored by the Elicitation Designer — Team 3's output
    # reconfiguring Team 1's behaviour (the elegant closed loop).
    elicitation_hint = ""
    try:
        text_lower = convo.lower() + " " + (state.get("user_intent") or "").lower()
        for b in get_registry().list_published():
            bundle = get_registry().load(b["bundle_id"])
            if not bundle:
                continue
            domains = [d.lower() for d in bundle.get("domains", [])]
            # Check if the user's conversation mentions this bundle's domain.
            if any(d in text_lower for d in domains if d):
                questions = bundle.get("elicitation_questions") or []
                if questions:
                    qs = [q["question"] for q in questions[:5]
                          if isinstance(q, dict) and q.get("question")]
                    if qs:
                        elicitation_hint = (
                            "\n\nDomain-specific questions for this vertical "
                            "(use these to guide the conversation):\n" +
                            "\n".join(f"- {q}" for q in qs))
                break
    except Exception:
        pass

    def _stub() -> dict:
        # Deterministic: if the user has said anything substantive, proceed.
        last_user = _last_user_msg(state)
        if loopback:
            return {"reply_to_user": loopback + " Could you clarify the facility "
                    "type and what you want to monitor?", "ready_to_classify": False}
        if last_user and len(last_user.split()) >= 3:
            return {"reply_to_user": "Got it — let me set that up.",
                    "ready_to_classify": True}
        # If we have elicitation questions, use the first one.
        if elicitation_hint:
            return {"reply_to_user": "I recognise this domain. " +
                    elicitation_hint.strip().split("\n")[2].lstrip("- "),
                    "ready_to_classify": False}
        return {"reply_to_user": "Tell me about the facility you want a digital "
                "twin for — what kind of site is it, and what do you want to keep "
                "an eye on?", "ready_to_classify": False}

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You are a friendly, jargon-free guide helping a facility "
                "operator create a digital twin. Your goal is to PROCEED quickly, "
                "not to chat. Return JSON "
                "{\"reply_to_user\": str, \"ready_to_classify\": bool}. "
                "Set ready_to_classify=TRUE as soon as you can name the facility "
                "TYPE and roughly what they want to monitor — do NOT keep asking "
                "for more detail once you have those two things. If the user says "
                "anything like 'build it', 'go ahead', or 'create it', set "
                "ready_to_classify=true immediately. Only set it false when the "
                "facility type is genuinely unknown. When proceeding, keep "
                "reply_to_user to a short confirmation like 'Got it — building "
                "your <type> twin now.' If a reason for re-asking is provided, "
                "voice it plainly first, then ask ONE focused question."
                + elicitation_hint),
        user=(f"{('Reason to re-ask: ' + loopback) if loopback else ''}\n\n"
              f"Conversation so far:\n{convo}\n\n"
              "Decide: do you already know the facility type and rough scope? "
              "If yes, set ready_to_classify=true."),
        stub=_stub(),
    )

    reply = result.get("reply_to_user") or ""
    ready = bool(result.get("ready_to_classify"))

    convo_list = list(state.get("conversation", []))
    if reply:
        convo_list = convo_list + [{"role": "assistant", "content": reply}]

    update = {
        "conversation": convo_list,
        "reply_to_user": reply,
        "user_intent": _last_user_msg(state) or state.get("user_intent"),
        "next_action": "classify" if ready else "ask",
        # clear stale loopback signal once voiced
        "validation": None if ready else state.get("validation"),
    }
    if not ready:
        # Yield the turn back to the human; the graph pauses here.
        update[INTERRUPT_KEY] = "awaiting_user_input"
    return update


# ==========================================================================
# V · Plan Parser   (multimodal — turns a 2-D plan into a bim_model)
# ==========================================================================
PLAN_PARSER_SYSTEM = (
    "You are an architectural plan parser for a digital-twin platform. From a "
    "2-D floor-plan image, reconstruct the building geometry FAITHFULLY as JSON — "
    "match the plan's actual room arrangement, proportions, and outline.\n\n"
    "Coordinates are in METRES, origin at the building's top-left corner, x to the "
    "right, y downwards in the plan. IMPORTANT: many plans PRINT each room's size "
    "next to its label (e.g. 'WARD 16.88X7.26' means 16.88 m wide x 7.26 m deep, "
    "'TOIL. 2.40X1.20' means 2.4 x 1.2 m) — READ those numbers and use them as the "
    "room's bbox w/l. Lay rooms out so they tile the building without overlapping. "
    "Read EVERY labelled room (there may be 40+). Infer building.facility from the "
    "drawing (hospital, datacenter, residential, office, factory). Return EXACTLY "
    "this shape:\n"
    '{"building":{"name":str,"facility":str,"widthM":num,"lengthM":num,"floors":int},'
    '"levels":[{"index":int,"elevationM":num,"heightM":num}],'
    '"rooms":[{"id":str,"level":int,"name":str,"type":str,'
    '"bbox":{"x":num,"y":num,"w":num,"l":num},'
    '"footprint":[[x,y],...],"areaM2":num}],'
    '"walls":[{"level":int,"start":[x,y],"end":[x,y],"heightM":num,"thicknessM":num}],'
    '"openings":[{"type":"door"|"window","level":int,"at":[x,y],"widthM":num}],'
    '"equipment":[{"id":str,"label":str,"assetType":str,"room":str,"x":num,"y":num,"level":int}]}\n'
    "room.type ∈ bedroom, master_bedroom, living, dining, kitchen, bathroom, "
    "garage, porch, office, balcony, utility, closet, corridor. Give bbox for every "
    "room; give footprint ONLY for non-rectangular rooms. Put a door/window opening "
    "wherever the plan shows one (an arc = a door). List FIXED building equipment "
    "you can see (assetType: split ac, ceiling light, water heater, fridge, stove, "
    "sink, toilet, shower, water tank, electrical panel, meter); do NOT invent "
    "loose furniture (the platform furnishes rooms from their type). Use ONE level "
    "(index 0) unless the plan clearly shows multiple. Output only the JSON."
)


def plan_parser(state: dict) -> dict:
    """Reconstruct an uploaded 2-D plan into a format-agnostic `bim_model`
    (building outline, levels, rooms with geometry, walls, equipment). Degrades
    to a synthesized building when no LLM/vision is available or the parse is
    unusable, so the flow always yields a renderable twin. Also emits coarse
    `vision_findings` (label + count) to enrich the Domain Classifier.

    If no files are uploaded, the graph skips this node entirely."""
    from agents import bim_support as bs

    gw = get_gateway()
    files = state.get("uploaded_files") or []
    if not files:
        return {"vision_findings": [], "bim_model": None}

    convo = _convo_text(state)
    facility = bs.infer_facility(convo + " " + (state.get("user_intent") or ""))
    floors = bs.infer_floors(convo, default=1)

    image_urls = [f["url"] for f in files
                  if f.get("type") in (None, "image") and f.get("url")]
    bim_files = [f for f in files if f.get("type") == "bim"]

    # IFC fast-follow — optional dependency, stub-safe when absent.
    if bim_files and not image_urls:
        try:
            from agents.ifc_parser import parse_ifc  # optional
            bm = parse_ifc(bim_files[0])
            if bm:
                bm = bs.normalize_bim_model(bm, facility, floors)
                return {"bim_model": bm, "vision_findings": bs.findings_from_bim(bm)}
        except Exception:
            pass
        bm = bs.synthesize_bim_model(facility, floors)
        return {"bim_model": bm, "vision_findings": bs.findings_from_bim(bm)}

    if not image_urls:
        bm = bs.synthesize_bim_model(facility, floors)
        return {"bim_model": bm, "vision_findings": bs.findings_from_bim(bm)}

    result = gw.complete_json_vision(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=PLAN_PARSER_SYSTEM,
        user_text=(f"Facility hint: {facility}. Floors hint: {floors}. Parse this "
                   f"floor plan into the bim_model JSON. Capture every labelled "
                   f"room with its printed dimensions; do not omit rooms."),
        image_urls=image_urls,
        stub={"_synth": True},
        max_tokens=12000,
    )
    bm = bs.normalize_bim_model(result, facility, floors)
    if bm.get("synthesized"):
        # parse failed/empty → we fell back to a generic building. Surface why.
        bm["parse_note"] = (gw.last_vision_error
                            or "plan could not be parsed; using a generic layout")
        try:
            print(f"[plan_parser] vision fallback ({gw.backend}): {bm['parse_note']}",
                  file=sys.stderr)
        except Exception:
            pass
    return {"bim_model": bm, "vision_findings": bs.findings_from_bim(bm)}


# Backwards-compatible alias: the graph wiring imports `vision_agent`.
vision_agent = plan_parser


# ==========================================================================
# 3b · Schema Mapper   (LLM — maps free-text + findings to ontology entities)
# ==========================================================================
def schema_mapper(state: dict) -> dict:
    """Translates plain-language descriptions + Vision findings + bundle templates
    into concrete ontology entities. When no LLM is available, degrades to the
    Capability Composer's bundle-template behaviour (zero regression)."""
    gw = get_gateway()
    domain = state.get("domain") or ""
    bundles = state.get("loaded_bundles") or []
    vision = state.get("vision_findings") or []
    convo = _convo_text(state)

    # BIM path: a parsed (or synthesized) building drives the drafts directly —
    # Building → Floor → Room → equipment, carrying geometry. This IS the twin,
    # so it replaces the generic bundle templates.
    bim_model = state.get("bim_model")
    if bim_model and bim_model.get("rooms"):
        from agents import bim_support as bs
        source_plan = None
        for f in (state.get("uploaded_files") or []):
            if f.get("filename"):
                source_plan = f["filename"]
                break
        # Apply-to-plan auto-wiring: for hospital/datacenter plans, re-type the
        # assets to their dedicated classes, inject the infrastructure spine, wire
        # the functional coupling, and pre-set fault-demo params — so the twin
        # actually runs coupled physics and shows real status.
        facility = bim_model.get("facility") or bs.infer_facility(
            convo + " " + (state.get("user_intent") or ""))
        bs.enrich_domain(bim_model, facility)   # furnish + wire functional services
        twin_name = state.get("twin_name") or bim_model.get("building", {}).get("name")
        entities, rels = bs.bim_model_to_drafts(bim_model, twin_name, source_plan)
        return {"draft_entities": entities, "draft_relationships": rels,
                "twin_name": twin_name,
                "mapping_source": "bim", "next_action": "validate"}

    # If the Composer already drafted from templates and there's no vision
    # input requiring richer mapping, pass through.
    existing_drafts = state.get("draft_entities") or []
    if existing_drafts and not vision and gw.backend == "stub":
        return {"mapping_source": "bundle", "next_action": "validate"}

    # Load the bundle's templates as base vocabulary.
    registry = get_registry()
    bundle_templates = []
    for bid in bundles:
        b = registry.load(bid)
        if b:
            bundle_templates = b.get("entity_templates", [])
            break

    # Build a legal-type vocabulary from SchemaService for the LLM.
    legal_types = []
    try:
        import sys
        _tools = str(ROOT / "tools")
        if _tools not in sys.path:
            sys.path.insert(0, _tools)
        from schema_service import SchemaService
        svc = SchemaService.load()
        legal_types = [{"iri": t["iri"], "label": t.get("label", ""),
                        "category": t.get("category", "")}
                       for t in svc.legal_types(instantiable_only=True)]
    except Exception:
        pass

    def _stub() -> dict:
        # Stub: use bundle templates directly (MVP behaviour).
        return {"entities": [{"key": t.get("key"), "canonical_type": t["canonical_type"],
                              "properties": t.get("properties", {})}
                             for t in bundle_templates],
                "relationships": [dict(r) for r in (registry.load(bundles[0]) or {}).get(
                    "relationship_templates", [])] if bundles else []}

    vision_text = ""
    if vision:
        items = [f"- {f.get('label', '?')} (x{f.get('count', 1)}, "
                 f"location: {f.get('location', 'unknown')})" for f in vision]
        vision_text = "\nVision findings:\n" + "\n".join(items)

    # Show the model the WHOLE instantiable vocabulary. This used to be
    # `legal_types[:30]`, an alphabetical slice of ~212 types — so 'Air Quality
    # Sensor' was the only sensor class the mapper could ever see, and every
    # twin got air-quality sensors regardless of what the user described.
    # The list is a few thousand tokens; correctness is worth it.
    type_lines = "\n".join(
        f"  {t['iri']}  ({t.get('label') or t['iri'].split('#')[-1]})"
        for t in legal_types)

    result = gw.complete_json(
        tenant_id=state["tenant_id"], session_id=state["session_id"],
        system=("You map facility descriptions to concrete NextXR ontology entities. "
                "Each entity needs: \"key\" (short local ref like 'site', 'ahu-01'), "
                "\"canonical_type\" (full IRI from the legal types list), "
                "and \"properties\" (dict with at least displayName). "
                "Also produce relationships: [{\"source_key\", \"predicate\", \"target_key\"}]. "
                "Predicates must be prefixed CURIEs (e.g. 'nxr:hasPart', "
                "'nxr:monitors', 'cfp:servesSpace'), never a bare name. "
                "Every Sensor MUST have an outgoing 'nxr:monitors' relationship to "
                "the thing it watches — a sensor that monitors nothing is rejected. "
                "Return JSON {\"entities\": [...], \"relationships\": [...]}. "
                "Use ONLY canonical_type IRIs from the legal types provided — pick "
                "the CLOSEST match to what the user actually described; do not "
                "substitute an unrelated class just because it appears first."),
        user=f"Domain: {domain}\n"
             f"Conversation:\n{convo}\n{vision_text}\n\n"
             f"Legal types (use these IRIs):\n{type_lines}\n\n"
             f"Bundle templates (for reference): {bundle_templates}",
        stub=_stub(),
        max_tokens=4000,
    )

    entities = result.get("entities") or _stub()["entities"]
    rels = result.get("relationships") or _stub()["relationships"]

    return {"draft_entities": entities, "draft_relationships": rels,
            "mapping_source": "mapper" if gw.backend != "stub" else "bundle",
            "next_action": "validate"}


# ==========================================================================
# 6 · Scene Generator   (real — committed building graph → nxr-scene/1)
# ==========================================================================
def scene_generator(state: dict) -> dict:
    """Turn the committed building into a renderable `nxr-scene/1` scene graph:
    extruded floor slabs, walls, room pads, and equipment props placed at their
    coordinates. Every node carries the graph `entityId` so the viewer can paint
    live status onto it.

    Primary source is the parsed `bim_model` (rich geometry) joined to the
    committed entity ids via each draft's `key`. When no plan was used, it
    synthesizes a plausible building from the committed assets so non-BIM twins
    still render something."""
    from agents import bim_support as bs

    tenant_id = state.get("tenant_id") or ""
    twin_id = state.get("twin_id")
    if not twin_id:
        return {"scene_result": {"format": "nxr-scene/1", "status": "skipped",
                                 "nodes": [],
                                 "message": "No committed twin to visualize."},
                "next_action": "done"}

    # Map bim element key → committed graph node id (set during validate/commit).
    drafts = state.get("draft_entities") or []
    id_map = {d.get("key"): d.get("_draft_id")
              for d in drafts if d.get("key") and d.get("_draft_id")}

    bim_model = state.get("bim_model")

    if not (bim_model and bim_model.get("rooms")):
        # Fallback: synthesize from the committed graph (labels → equipment),
        # then match ids back by display name.
        try:
            from graph.query import GraphQuery
            q = GraphQuery()
            assets = q.list_by_label(tenant_id, "PhysicalAsset", limit=80)
        except Exception:
            assets = []
        facility = bs.infer_facility(
            _convo_text(state) + " " + (state.get("domain") or ""))
        hints = [{"label": a.get("displayName", "Equipment"), "count": 1}
                 for a in assets] or None
        bim_model = bs.synthesize_bim_model(facility, 1, equipment_hints=hints)
        # best-effort id match by display name
        by_name = {a.get("displayName"): a.get("id") for a in assets}
        for eq in bim_model.get("equipment", []):
            if eq.get("label") in by_name:
                id_map[eq["id"]] = by_name[eq["label"]]

    scene = bs.bim_model_to_scene(bim_model, id_map=id_map)
    scene["status"] = "ok"
    scene["twin_id"] = twin_id
    # Cache the full scene (structure + assets + furniture) so the twin re-renders
    # identically per-tenant later (Dashboard hero, reloads) without a session.
    bs.save_scene_cache(twin_id, scene)
    return {"scene_result": scene, "next_action": "done"}


# ==========================================================================
#  Routers (pure functions feeding the conditional edges)
# ==========================================================================
def route_after_concierge(state: dict) -> str:
    """Original MVP router (kept for backwards compat reference)."""
    return "classify" if state.get("next_action") == "classify" else "ask"


def route_after_concierge_v2(state: dict) -> str:
    """Extended router: branches to Vision Agent when files are uploaded."""
    if state.get("next_action") == "classify":
        if state.get("uploaded_files"):
            return "vision"
        return "classify"
    return "ask"


def route_after_classify(state: dict) -> str:
    conf = state.get("domain_confidence") or 0.0
    return "ok" if conf >= CONFIDENCE_THRESHOLD else "low"


def route_after_validate(state: dict) -> str:
    v = state.get("validation") or {}
    return "ok" if v.get("ok") else "fail"


def route_after_graph_writer(state: dict) -> str:
    """After commit: optionally generate a scene, or done."""
    if state.get("scene_result") is not None:
        return "done"  # already has a scene result, skip
    if state.get("committed") and state.get("uploaded_files"):
        return "scene"  # uploaded files hint that visualization is wanted
    return "done"


# ==========================================================================
#  Helpers
# ==========================================================================
def _convo_text(state: dict) -> str:
    lines = []
    for m in state.get("conversation", []):
        role = m.get("role", "user")
        lines.append(f"{role}: {m.get('content', '')}")
    return "\n".join(lines) or "(no messages yet)"


def _last_user_msg(state: dict):
    for m in reversed(state.get("conversation", [])):
        if m.get("role") == "user":
            return m.get("content")
    return None


def _loopback_reason(state: dict):
    """If the Validator failed or the Classifier was unsure, produce a short
    human reason for the Concierge to voice."""
    v = state.get("validation")
    if v and not v.get("ok") and v.get("errors"):
        reasons = "; ".join(e.get("reason", "") for e in v["errors"][:3])
        return f"The twin didn't pass validation: {reasons}."
    if state.get("domain_confidence") is not None and \
       state["domain_confidence"] < CONFIDENCE_THRESHOLD and \
       state.get("conversation"):
        return "I'm not yet sure what kind of facility this is."
    return None
