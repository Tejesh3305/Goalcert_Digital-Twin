"""Generate NextXR_LLD.docx — Low-Level Design.

Package structure, all 136 API endpoints, the relational/historian/graph
schema, the service layer, the middleware pipeline and the key sequences.

Matches the GoalCert technical-document format: title page, document control,
version history, reference documents, ToC placeholder, and numbered sections, with the data-flow diagram embedded.

Every builder lives in gen_all_docs.py so the five documents cannot drift from
one another (shared helpers, shared style, one source of truth for the tables
that appear in more than one document). Running this file produces exactly one
document; run gen_all_docs.py to produce the whole set.

Usage: python gen_lld.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_all_docs import (  # noqa: E402
    build_lld,
    generate_dataflow_png,
)


def main():
    print("Generating NextXR LLD ...")
    generate_dataflow_png()
    build_lld()


if __name__ == "__main__":
    main()
