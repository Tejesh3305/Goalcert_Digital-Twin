# NextXR Digital Twin — image.
#
# Bakes the built React app (frontend/dist) into the image so single-URL hosts (e.g.
# Render) serve both the API and the UI from one process — see the "Frontend serving"
# section of server/main.py, which serves frontend/dist at "/" when present. On AWS ECS
# the federated UI is ALSO published to S3/CloudFront by CI (AWS_DEPLOYMENT.md §5); the
# copy baked in here just goes unused there, since traffic to the ECS task's "/" isn't
# what serves the AWS frontend.
#
# STATE LIVES ON A VOLUME, NOT IN THIS IMAGE.
# The twin keeps its twin registry, change log, agent bundles, checkpoints and the
# reconstructed 3-D models under a data directory. Baking those into the image (the old
# `COPY nextxr-ontology/` did, .db files and all) is worse than losing them: every redeploy
# would RESET the live twin registry to whatever snapshot was committed to git. .dockerignore
# keeps them out; NXR_DATA_DIR points at the EFS mount instead.

FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    # The EFS access point is mounted here by the task definition. Overridable; the code
    # falls back to ./data when unset (local dev).
    NXR_DATA_DIR=/data \
    # The 3-D platform (object-photo → GLB) uses its OWN data dir (threed_platform
    # app/config.py). Point it under the SAME mounted volume, or every generated
    # model is written to an ephemeral in-image path and lost on task replacement.
    DATA_DIR=/data/threed

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nextxr-ontology/ ./nextxr-ontology/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

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
