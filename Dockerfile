# syntax=docker/dockerfile:1.8@sha256:e87caa74dcb7d46cd820352bfea12591f3dba3ddc4285e19c7dcd13359f7cefd

ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
ARG NODE_IMAGE=node:22.22.2-alpine@sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30.4-alpine3.24@sha256:44e36330f74d4f3a1d4e222acca9e23b401fb87811a7597024502bb759c4dd49

FROM ${PYTHON_IMAGE} AS backend-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements-locking.txt requirements-runtime.txt ./
RUN python -m pip install \
        --index-url=https://pypi.org/simple \
        --only-binary=:all: \
        --require-hashes \
        -r requirements-locking.txt \
    && python -m pip wheel \
        --index-url=https://pypi.org/simple \
        --no-build-isolation \
        --no-deps \
        --require-hashes \
        --wheel-dir=/wheels \
        -r requirements-runtime.txt


FROM ${PYTHON_IMAGE} AS backend-runtime

ARG TARGETARCH
ARG TARGETOS
LABEL org.opencontainers.image.title="Foundation Intelligence API" \
      org.opencontainers.image.description="Data-free FastAPI runtime" \
      org.opencontainers.image.source="https://github.com/netlight/FoundationIntelligencePlatform" \
      io.foundation-intelligence.target-platform="${TARGETOS}/${TARGETARCH}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/tmp/fip \
    DATA_PATH=/app/runtime-data/unavailable.json \
    DB_PATH=/app/runtime-data/unavailable.db \
    SCORE_CONFIG_PATH=/app/config/scoring.example.json

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 10001 -m 0700 /tmp/fip

COPY --from=backend-builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=10001:10001 src/bff /app/src/bff
COPY --chown=10001:10001 src/data/*.py /app/src/data/
COPY --chown=10001:10001 src/governance /app/src/governance
COPY --chown=10001:10001 src/migration /app/src/migration
COPY --chown=10001:10001 src/observability /app/src/observability
COPY --chown=10001:10001 src/pipelines /app/src/pipelines
COPY --chown=10001:10001 src/preprocessing /app/src/preprocessing
COPY --chown=10001:10001 src/scoring /app/src/scoring
COPY --chown=10001:10001 src/scrapers /app/src/scrapers
COPY --chown=10001:10001 config/scoring.example.json /app/config/scoring.example.json
COPY --chown=10001:10001 config/data-governance.json /app/config/data-governance.json
COPY --chown=10001:10001 config/observability.json /app/config/observability.json
COPY --chown=10001:10001 config/source-pipelines.json /app/config/source-pipelines.json
COPY --chown=10001:10001 alembic.ini /app/alembic.ini
COPY --chown=10001:10001 alembic /app/alembic

USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2).read()"]
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["bff.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-graceful-shutdown", "30"]


FROM ${NODE_IMAGE} AS frontend-build

ENV NODE_ENV=production
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --include=dev --ignore-scripts --registry=https://registry.npmjs.org
COPY frontend/ ./
RUN npm run build


FROM ${NGINX_IMAGE} AS frontend-runtime

LABEL org.opencontainers.image.title="Foundation Intelligence Frontend" \
      org.opencontainers.image.description="Static Vite production build"
COPY docker/frontend-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
STOPSIGNAL SIGQUIT
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
