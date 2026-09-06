# Gridiron HQ

A self-hosted fantasy football helper app, designed to run on a Raspberry Pi.
It pulls league/player data (Yahoo Fantasy, FantasyPros) and serves a small
web UI for lineup and roster decisions.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (SQLite), APScheduler, managed with [uv](https://docs.astral.sh/uv/).
- **Frontend:** Vite + React + TypeScript.

## Dev setup

### Backend

```bash
cd backend
uv sync --dev
uv run uvicorn app.main:app --reload
```

The API is served at `http://localhost:8000`; health check at
`GET /api/health`.

Run tests:

```bash
cd backend
uv run pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server runs at `http://localhost:5173` and proxies `/api`
requests to `http://localhost:8000`, so run the backend alongside it.

Build for production:

```bash
cd frontend
npm run build
```

This outputs to `frontend/dist`, which the backend serves automatically as
static files when it's present.

## Configuration

Copy `.env.example` to `.env` at the repo root and fill in credentials as
needed:

```bash
cp .env.example .env
```

## Raspberry Pi deployment (Docker Compose)

The app ships as a single container: a multi-stage `backend/Dockerfile`
builds the frontend and bundles it with the backend, which serves both the
API and the static UI. Images use the official multi-arch
`node:22-slim` and `python:3.12-slim` bases, so this builds and runs on
`linux/arm64` (Raspberry Pi 4/5) as well as `amd64`.

```bash
cp .env.example .env   # fill in credentials
docker compose up -d --build
```

The app is then available at `http://<pi-hostname>:8000`. SQLite data
persists on the host in `./data`, mounted into the container.
