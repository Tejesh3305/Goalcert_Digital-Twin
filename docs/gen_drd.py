"""Generate NextXR_DRD.docx — Data Requirements Document.

Every persistent data entity: 9 relational tables, 3 continuous aggregates,
the Neo4j graph model and its closed taxonomy, retention, integrity and privacy.

Matches the GoalCert technical-document format: title page, document control,
version history, reference documents, ToC placeholder, and numbered sections, with the data-model diagram embedded.

Every builder lives in gen_all_docs.py so the five documents cannot drift from
one another (shared helpers, shared style, one source of truth for the tables
that appear in more than one document). Running this file produces exactly one
document; run gen_all_docs.py to produce the whole set.

Usage: python gen_drd.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_all_docs import (  # noqa: E402
    build_drd,
    generate_erd_png,
)


def main():
    print("Generating NextXR DRD ...")
    generate_erd_png()
    build_drd()


if __name__ == "__main__":
    main()
