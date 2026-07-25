# NextXR Digital Twin — image.
#
# Bakes the built React app (frontend/dist) into the image so single-URL hosts (e.g.
# Render) serve both the API and the UI from one process — see the "Frontend serving"
# section of server/main.py, which serves frontend/dist at "/" when present. On AWS ECS
# the federated UI is ALSO published to S3/CloudFront by CI (AWS_DEPLOYMENT.md §5); the
# copy baked in here just goes unused there, since traffic to the ECS task's "/" isn't
# what serves the AWS frontend.
#
# THE RUNNING TASK IS STATELESS. STATE LIVES IN RDS, S3 AND ELASTICACHE.
#
#   records (twins, change log, bundles, checkpoints, scenes, 3-D jobs) -> RDS Postgres
#   blobs   (generated GLBs, 3-D job artifacts)                         -> S3
#   live events                                                         -> ElastiCache Redis
#
# None of NXR_DATABASE_URL / NXR_S3_BUCKET / NXR_REDIS_URL is set here. The first
# carries a password (a task-definition SECRET from Secrets Manager), and all three are
# environment-specific. Left unset the app silently falls back to PER-TASK storage —
# right for local dev, wrong for a deploy, and it does not error. Set
# NXR_REQUIRE_DB / NXR_REQUIRE_S3 / NXR_REQUIRE_REDIS in the task definition so a
# missing one fails the boot instead, and read the [db]/[blobs]/[bus] lines after the
# first rollout (AWS_DEPLOYMENT.md §5.2, §9, §12).
#
# /data is now only scratch — mounting EFS is optional (§7.4). Committed .db files are
# still excluded from the image by .dockerignore: baking them in is worse than losing
# them, since a redeploy would RESET the live twin registry to a git snapshot.

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
    # Working directory for scratch, and the fallback location for records/blobs when
    # NXR_DATABASE_URL / NXR_S3_BUCKET are unset. An EFS access point can be mounted
    # here but is no longer required (§7.4). Overridable; the code falls back to ./data
    # when unset (local dev).
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
