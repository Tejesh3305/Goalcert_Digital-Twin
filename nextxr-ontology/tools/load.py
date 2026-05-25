#!/usr/bin/env python3
"""
load.py — Assemble the NextXR ontology layers into one RDFlib graph
and run OWL-RL reasoning over it.

Load order matters: imports (BFO, SOSA) -> platform (classes,
properties, base shape, units, shapes) -> packs.

Usage:
    python tools/load.py                 # load + reason + summary
    python tools/load.py --no-reason     # load only
    python tools/load.py --export out.ttl
"""

import argparse
import sys
from pathlib import Path

try:
    from rdflib import Graph, RDF, RDFS, OWL, URIRef
except ImportError:
    sys.exit("Install deps first:  pip install rdflib owlrl pyshacl")

from ontology_graph import ROOT, NXR_CORE as NXR, PLATFORM_FILES, PACK_FILES

# The shared layer lists, split into (folder, file) pairs for friendly output.
def _split(rel):
    folder, _, fname = rel.rpartition("/")
    return folder, fname

LAYERS = [_split(r) for r in PLATFORM_FILES]
PACKS = [_split(r) for r in PACK_FILES]


def load_graph(include_packs=True):
    g = Graph()
    g.bind("nxr", NXR)
    files = LAYERS + (PACKS if include_packs else [])
    for folder, fname in files:
        path = ROOT / folder / fname
        if not path.exists():
            print(f"  [skip] missing {path}")
            continue
        before = len(g)
        g.parse(path, format="turtle")
        print(f"  [ok]   {folder}/{fname:<22} (+{len(g) - before} triples)")
    return g


def reason(g):
    import owlrl
    before = len(g)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    print(f"\nOWL-RL reasoning: {before} -> {len(g)} triples "
          f"(+{len(g) - before} inferred)")
    return g


def summary(g):
    classes = set(g.subjects(RDF.type, OWL.Class))
    obj_props = set(g.subjects(RDF.type, OWL.ObjectProperty))
    dt_props = set(g.subjects(RDF.type, OWL.DatatypeProperty))
    nxr_classes = sorted(str(c) for c in classes if str(c).startswith(NXR))
    print(f"\n--- Summary ---")
    print(f"  Total classes:          {len(classes)}")
    print(f"  Platform (nxr) classes: {len(nxr_classes)}")
    print(f"  Object properties:      {len(obj_props)}")
    print(f"  Datatype properties:    {len(dt_props)}")

    # Prove inference worked: is hvac:AirHandler inferred to be a PhysicalAsset?
    ah = URIRef("https://ontology.nextxr.io/v3/hvac#AirHandler")
    pa = URIRef(NXR + "PhysicalAsset")
    me = URIRef("http://purl.obolibrary.org/obo/BFO_0000040")
    supers = set(g.objects(ah, RDFS.subClassOf))
    print(f"\n  Inference check — hvac:AirHandler superclasses include:")
    print(f"    nxr:PhysicalAsset      {'YES' if pa in supers else 'no'}")
    print(f"    bfo:MaterialEntity     {'YES' if me in supers else 'no'}  "
          f"(inferred transitively)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reason", action="store_true")
    ap.add_argument("--no-packs", action="store_true")
    ap.add_argument("--export", metavar="FILE")
    args = ap.parse_args()

    print("Loading NextXR ontology layers...\n")
    g = load_graph(include_packs=not args.no_packs)
    if not args.no_reason:
        reason(g)
    summary(g)

    if args.export:
        g.serialize(destination=args.export, format="turtle")
        print(f"\nExported materialised graph -> {args.export}")


if __name__ == "__main__":
    main()
