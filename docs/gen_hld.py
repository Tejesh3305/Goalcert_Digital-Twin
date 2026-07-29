"""Generate NextXR_HLD.docx — High-Level Design.

Architecture, the four-layer ontology, the domain-pack extension model,
module map, technology stack, integration points and non-functional requirements.

Matches the GoalCert technical-document format: title page, document control,
version history, reference documents, ToC placeholder, and numbered sections, with the infrastructure and component diagrams embedded.

Every builder lives in gen_all_docs.py so the five documents cannot drift from
one another (shared helpers, shared style, one source of truth for the tables
that appear in more than one document). Running this file produces exactly one
document; run gen_all_docs.py to produce the whole set.

Usage: python gen_hld.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_all_docs import (  # noqa: E402
    build_hld,
    generate_infrastructure_png,
    generate_component_png,
)


def main():
    print("Generating NextXR HLD ...")
    generate_infrastructure_png()
    generate_component_png()
    build_hld()


if __name__ == "__main__":
    main()
