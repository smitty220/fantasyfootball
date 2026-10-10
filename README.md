# Gridiron HQ

A self-hosted fantasy football helper app. It runs as a Docker Compose stack on
the homelab server — see [deploy/server-setup.md](deploy/server-setup.md) — and
is reachable on the home LAN only for now (no login yet).

It syncs your Yahoo Fantasy leagues (the source of truth for leagues, rosters,
players, schedule, roster positions and scoring) and joins them with
projections and values from FantasyPros, ESPN, Sleeper and FantasyCalc, all
synced into a local SQLite database on a schedule so the UI stays fast. On top
of that it gives you:

- **Leagues** — every Yahoo league you're in, with teams, rosters and records.
- **Lineups** — your lineup as set in Yahoo, with a toggle to this week's
  optimal lineup and flags for bye/injured starters and better bench or
  free-agent options. (The app never changes anything in Yahoo.)
- **Free agents** — the waiver wire with projections, trade values and trends.
- **Trade analyzer / finder** — projection- and FantasyCalc-backed trade values
  for your league's format (team count, superflex, PPR).
- **Matchup preview** — any week's games, opening on yours this week.
- **Standings & playoff odds** — Monte Carlo odds on the real remaining schedule.
- **Auto-refreshing data** — Yahoo every 15 minutes, projections hourly, the
  rest daily or weekly; see the Data page.

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

## Deployment

The app ships as a single container: a multi-stage `backend/Dockerfile`
builds the frontend and bundles it with the backend, which serves both the
API and the static UI. SQLite data persists on the host in `./data`; Alembic
migrations run on every startup, and the app keeps rolling backups in
`data/backups/`.

The live deployment is the homelab server stack:
**[deploy/server-setup.md](deploy/server-setup.md)**. The older Raspberry Pi /
Home Assistant OS add-on paths ([deploy/pi-setup.md](deploy/pi-setup.md),
`addons/gridironhq/`) are kept for reference only.
