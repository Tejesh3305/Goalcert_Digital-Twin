"""Generate NextXR_AWS Architecture.docx — AWS Architecture Document.

Runtime topology, network layout, ECS service configuration, the data tier,
security posture, cost estimation and the deployment pipeline.

Matches the GoalCert technical-document format: title page, document control,
version history, reference documents, ToC placeholder, and numbered sections, with all three architecture diagrams embedded.

Every builder lives in gen_all_docs.py so the five documents cannot drift from
one another (shared helpers, shared style, one source of truth for the tables
that appear in more than one document). Running this file produces exactly one
document; run gen_all_docs.py to produce the whole set.

Usage: python gen_aws.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_all_docs import (  # noqa: E402
    build_aws,
    generate_infrastructure_png,
    generate_component_png,
    generate_dataflow_png,
)


def main():
    print("Generating NextXR AWS Architecture ...")
    generate_infrastructure_png()
    generate_component_png()
    generate_dataflow_png()
    build_aws()


if __name__ == "__main__":
    main()
