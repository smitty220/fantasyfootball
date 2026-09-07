# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend static assets ----
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: install backend dependencies ----
# WORKDIR must match the runtime stage's final venv location (/app/backend):
# `uv sync` bakes this directory's absolute path into the venv (pyvenv.cfg
# and the shebang line of every console-script under .venv/bin, e.g.
# `#!/backend/.venv/bin/python`). If the venv is later copied somewhere else,
# those scripts fail at exec time with "no such file or directory" even
# though the file is right there — the shebang points at the old path.
FROM python:3.12-slim AS backend-build
WORKDIR /app/backend
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Stage 3: runtime image ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# curl is used by the Docker/Compose healthcheck to hit /api/health.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the resolved virtual environment from the build stage.
COPY --from=backend-build /app/backend/.venv /app/backend/.venv
ENV PATH="/app/backend/.venv/bin:$PATH"

# Backend application code.
COPY backend/app /app/backend/app

# Alembic config + migration scripts. app.migrations.run_migrations() (called
# at startup, before the app starts serving) resolves these relative to the
# backend directory, so they must ship in the image alongside app/ — without
# them the container fails at startup as soon as it tries to run migrations.
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/alembic /app/backend/alembic

# Built frontend static assets, served by the backend.
COPY --from=frontend-build /frontend/dist /app/frontend/dist

WORKDIR /app/backend
EXPOSE 8000

# SCHEDULER_ENABLED defaults to true in app.config.Settings; set it here too
# so `docker inspect` / `docker compose config` show the effective default,
# and so it's easy to flip off (SCHEDULER_ENABLED=false) for a second
# read-only instance without touching secrets.env.
ENV SCHEDULER_ENABLED=true

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
