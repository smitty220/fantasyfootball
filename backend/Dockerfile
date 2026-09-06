# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend static assets ----
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: install backend dependencies ----
FROM python:3.12-slim AS backend-build
WORKDIR /backend
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Stage 3: runtime image ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# Copy the resolved virtual environment from the build stage.
COPY --from=backend-build /backend/.venv /app/backend/.venv
ENV PATH="/app/backend/.venv/bin:$PATH"

# Backend application code.
COPY backend/app /app/backend/app

# Built frontend static assets, served by the backend.
COPY --from=frontend-build /frontend/dist /app/frontend/dist

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
