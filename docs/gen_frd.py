"""Generate NextXR_FRD.docx — Functional Requirements Document.

14 functional modules, 74 requirements with acceptance criteria,
6 workflow specifications, non-functional requirements and assumptions.
Each requirement has: ID, Requirement, Acceptance Criteria, Priority.

Matches the GoalCert technical-document format: title page, document control,
version history, reference documents, ToC placeholder, and numbered sections.

Every builder lives in gen_all_docs.py so the five documents cannot drift from
one another (shared helpers, shared style, one source of truth for the tables
that appear in more than one document). Running this file produces exactly one
document; run gen_all_docs.py to produce the whole set.

Usage: python gen_frd.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_all_docs import (  # noqa: E402
    build_frd,
    # (this document embeds no diagrams)
)


def main():
    print("Generating NextXR FRD ...")
    # no diagrams to render for this document
    build_frd()


if __name__ == "__main__":
    main()
