# Gridiron HQ

A self-hosted fantasy football helper app, designed to run on a Raspberry Pi
and reachable from anywhere over HTTPS via
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) — no cloud hosting,
no port forwarding. See [deploy/pi-setup.md](deploy/pi-setup.md) for the full
setup walkthrough.

It connects to your Yahoo Fantasy leagues and joins that with player
data/projections from FantasyPros, Sleeper, ESPN and FantasyCalc (all synced
into a local SQLite database on a schedule, so the UI stays fast and doesn't
hammer rate-limited APIs on every page load). On top of that data it gives
you:

- **Leagues** — connect one or more Yahoo leagues (keeper or redraft), view
  teams, rosters, and settings.
- **Free agents** — browse the waiver wire with projections and ownership
  trends layered in.
- **Trade analyzer** — evaluate proposed trades using projection-backed
  values, scored under your league's actual scoring rules.
- **Matchup preview** — see your weekly matchup with projected points per
  player.
- **Lineup editor** — set your lineup for the week from the same roster/
  projection data.
- **Auto-refreshing data** — a background scheduler keeps projections,
  trade values, and ownership data current without manual intervention.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (SQLite), Alembic migrations, APScheduler, managed with [uv](https://docs.astral.sh/uv/).
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

Credentials and other secrets live in `backend/secrets.env` (gitignored,
never committed — `.env.example` at the repo root just documents which
variables exist). Create it once:

```bash
cp .env.example backend/secrets.env
# then edit backend/secrets.env and fill in real values
```

This one file is the canonical secrets location for both dev (`uv run`
reads it directly, relative to its `backend/` working directory) and Docker
Compose (which bind-mounts this exact path into the container — see
`docker-compose.yml`), so there's never a second copy to keep in sync.

## Raspberry Pi deployment (Docker Compose + Tailscale Funnel)

The app ships as a single container: a multi-stage `backend/Dockerfile`
builds the frontend and bundles it with the backend, which serves both the
API and the static UI. Images use the official multi-arch
`node:22-slim` and `python:3.12-slim` bases, so this builds and runs on
`linux/arm64` (Raspberry Pi 4/5) as well as `amd64`.

```bash
docker compose up -d --build
```

The app is then available at `http://<pi-hostname>:8000`. SQLite data
persists on the host in `./data`, mounted into the container; Alembic
migrations run automatically against it on every startup.

For the full Raspberry Pi walkthrough — installing Docker and Tailscale,
first-time data sync, and exposing the app to the internet at
`https://<your-pi>.<your-tailnet>.ts.net` via `tailscale funnel` — see
**[deploy/pi-setup.md](deploy/pi-setup.md)**.
