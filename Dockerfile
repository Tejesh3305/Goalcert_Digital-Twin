# NextXR Digital Twin — API image (ECS Fargate, PRIVATE subnet).
#
# API ONLY. The federated UI (remoteEntry.js + style.css) is published to S3/CloudFront by
# CI — see AWS_DEPLOYMENT.md §5. server/main.py serves frontend/dist at "/" when present;
# it is deliberately absent here, and main.py handles that.
#
# STATE LIVES ON A VOLUME, NOT IN THIS IMAGE.
# The twin keeps its twin registry, change log, agent bundles, checkpoints and the
# reconstructed 3-D models under a data directory. Baking those into the image (the old
# `COPY nextxr-ontology/` did, .db files and all) is worse than losing them: every redeploy
# would RESET the live twin registry to whatever snapshot was committed to git. .dockerignore
# keeps them out; NXR_DATA_DIR points at the EFS mount instead.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    # The EFS access point is mounted here by the task definition. Overridable; the code
    # falls back to ./data when unset (local dev).
    NXR_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nextxr-ontology/ ./nextxr-ontology/

WORKDIR /app/nextxr-ontology

# Non-root, and it must own /data or the first write to the EFS mount fails.
# NOTE: the ECS EFS access point should be configured with the SAME uid/gid (10001) —
# POSIX ownership on the volume is enforced by the access point, not by this chown.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

VOLUME ["/data"]

EXPOSE 8080

# /api/v1/health returns 200 with status "healthy" (Neo4j up) or "degraded" (Neo4j down).
# It deliberately never 503s, so a Neo4j blip does not roll the fleet — that is a product
# decision (RUN.md), and it means this check verifies the PROCESS, not the database.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('PORT','8080'), timeout=4).status==200 else 1)"

# server/main.py reads PORT itself (uvicorn.run(port=int(os.getenv("PORT", "8080")))).
CMD ["python", "-m", "server.main"]
