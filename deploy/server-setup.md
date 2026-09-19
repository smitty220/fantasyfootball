# Deploying Gridiron HQ on the homelab server

The live deployment. The box is the Proxmox **apps** LXC at `192.168.5.20`
(Debian 13, Docker + Compose plugin), deploy user `ffball`, one Compose
project per directory under `/opt/stacks`.

| | |
| --- | --- |
| Stack directory | `/opt/stacks/fantasy-football` |
| Compose project | `fantasy-football` (pinned via `name:` — the host runs several stacks) |
| URL | <http://192.168.5.20:8090> (LAN only) |
| Published port | **8090** → 8000 in the container. 3000/8080/9090 belong to Grafana, Speedtest Tracker and Prometheus; don't reuse them |
| Secrets | `/opt/stacks/fantasy-football/.env`, mode 600, never committed |
| Database | `/opt/stacks/fantasy-football/data/app.db`, bind-mounted to `/app/data` |

Estate rules this stack follows: image tags are pinned (no `:latest`, including
the `uv` build stage), secrets live in a mode-600 `.env` rather than in the
image or the compose file, and nothing mounts the Docker socket. Never run
`docker system prune -a --volumes` on this host — it would take out
`monitoring_grafana-data` and `monitoring_prometheus-data` along with it.

Other paths on this host (`/opt/stacks/monitoring`, `/opt/stacks/speedtest`)
are off limits.

## Pushing code from the Mac

`rsync` is not installed in the LXC, so the tree goes over as a tar stream:

```bash
cd ~/Projects/fantasyfootball
tar czf - --exclude='./.git' --exclude='./frontend/node_modules' \
  --exclude='./backend/.venv' --exclude='./data' \
  --exclude='./backend/secrets.env' --exclude='.DS_Store' \
  --exclude='./backend/.pytest_cache' --exclude='__pycache__' \
  --exclude='./addons/gridironhq/app' . \
  | ssh ffball@192.168.5.20 'tar xzf - -C /opt/stacks/fantasy-football'

ssh ffball@192.168.5.20 'cd /opt/stacks/fantasy-football && docker compose up -d --build'
```

`data/` is excluded on purpose: the server's database is the live one, and a
stray copy from the Mac would overwrite it.

Alembic migrations run at container startup (`app.migrations.run_migrations()`),
so a deploy that carries new migrations needs no extra step.

## Secrets

`.env` holds the Yahoo and FantasyPros credentials, `SESSION_SECRET`, and the
optional password gate. Compose mounts it read-only at
`/app/backend/secrets.env` — the path `app.config.Settings` reads, and the same
relative path local non-Docker dev uses, so there is only ever one copy.

`OWNER_PASSWORD` and `LEAGUE_PASSWORD` empty ⇒ **no login screen**: anyone who
can reach the port gets full owner access. Acceptable only while the app is
LAN-only. Set both before the box gets a public hostname.

After editing `.env`: `docker compose up -d` (recreates the container so the new
values are read).

## Operating it

```bash
ssh ffball@192.168.5.20
cd /opt/stacks/fantasy-football
docker compose ps                    # container + healthcheck state
docker compose logs -f app           # follow logs
docker compose restart app
curl -s localhost:8090/api/health    # {"status":"ok","version":"0.1.0"}
```

## Backups

The app writes a dated copy into `data/backups/` on every start and keeps
seven. For an off-box snapshot:

```bash
ssh ffball@192.168.5.20 'cd /opt/stacks/fantasy-football && docker compose stop app \
  && tar czf - data && docker compose start app' > ~/gridiron-backup-$(date +%Y%m%d).tar.gz
```

Stopping first is the safe version; SQLite tolerates a hot copy, but a
point-in-time snapshot of a stopped container is guaranteed consistent.

## Remote access

Not set up. The box has no public hostname, no Cloudflare tunnel and no Access
policy — exposing it is a separate piece of homelab work. Until then the app is
reachable only from the LAN, and the leaguemate-facing link doesn't exist yet.

## Other deployment paths

`deploy/pi-setup.md` documents the Raspberry Pi / Home Assistant OS add-on
path, which this replaces. It is kept for reference only.
