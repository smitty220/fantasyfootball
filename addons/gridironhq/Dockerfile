# Home Assistant add-on image for Gridiron HQ.
#
# Adapted from backend/Dockerfile at the repo root. Two differences:
#
#   1. Source paths are prefixed with `app/`. The Supervisor builds a local
#      add-on with the add-on folder as the build context, so the application
#      source has to live *inside* this folder — `deploy/build-addon.sh`
#      rsyncs backend/ and frontend/ into ./app/ before this builds.
#   2. The entrypoint is run.py, which translates /data/options.json (the
#      add-on's configuration, written by the Supervisor) into the environment
#      variables app.config.Settings reads, then execs uvicorn.
#
# There is no `ARG BUILD_FROM` here on purpose: since Supervisor 2026.04.0 the
# BUILD_FROM argument is no longer supplied by default, and add-ons are
# expected to name their base image explicitly. Using the same official
# node/python bases as the Compose build keeps the add-on byte-for-byte
# equivalent to what runs on the Mac and under docker compose.

# ---- Stage 1: build the frontend static assets ----
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY app/frontend/package*.json ./
RUN npm install
COPY app/frontend/ ./
RUN npm run build

# ---- Stage 2: install backend dependencies ----
# WORKDIR must match the runtime stage's final venv location (/app/backend):
# `uv sync` bakes this directory's absolute path into the venv (pyvenv.cfg and
# the shebang of every console script under .venv/bin). Copying the venv to a
# different path breaks those shebangs at exec time.
FROM python:3.12-slim AS backend-build
WORKDIR /app/backend
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY app/backend/pyproject.toml app/backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Stage 3: runtime image ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# curl is not used by the Supervisor watchdog (that probes from outside the
# container), but it makes `docker exec` debugging on the Pi far easier.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=backend-build /app/backend/.venv /app/backend/.venv
ENV PATH="/app/backend/.venv/bin:$PATH"

# Backend application code.
COPY app/backend/app /app/backend/app

# Alembic config + migration scripts. app.migrations.run_migrations() runs at
# startup and resolves these relative to the backend directory, so they must
# ship alongside app/.
COPY app/backend/alembic.ini /app/backend/alembic.ini
COPY app/backend/alembic /app/backend/alembic

# Built frontend static assets, served by the backend.
COPY --from=frontend-build /frontend/dist /app/frontend/dist

# Mount point for the add-on's persistent folder (config.yaml maps
# `addon_config` here). Created empty so run.py can tell "mounted" from "not
# mounted" with os.path.ismount().
RUN mkdir -p /app/data

COPY run.py /run.py

WORKDIR /app/backend
EXPOSE 8000

LABEL io.hass.type="addon" \
      io.hass.name="Gridiron HQ"

CMD ["python3", "/run.py"]
