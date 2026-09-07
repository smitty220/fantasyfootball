# Deploying Gridiron HQ on a Raspberry Pi

A copy-paste runbook for getting Gridiron HQ running on a fresh Raspberry Pi
4 or 5 (64-bit Raspberry Pi OS / Debian bookworm) and reachable from the
internet over HTTPS via [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) —
no port forwarding, no reverse proxy, no certificates to manage yourself.

Run every command below on the Pi itself (SSH in, or use a keyboard/monitor).

## 1. Install Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"
```

Log out and back in (or run `newgrp docker`) so your shell picks up the new
`docker` group membership. Confirm it worked without `sudo`:

```bash
docker run --rm hello-world
```

The `get.docker.com` script also installs the `docker compose` plugin (the
`docker compose ...` subcommand, not the old standalone `docker-compose`),
which is what the rest of this guide uses.

## 2. Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` prints a login URL — open it on any device to authenticate the
Pi into your tailnet. Once it's connected, note the Pi's Tailscale name;
you'll need it later. Find it anytime with:

```bash
tailscale status
```

Your own machine is the first row, and its full public hostname is
`<device-name>.<tailnet-name>.ts.net` (also visible in the
[admin console](https://login.tailscale.com/admin/machines)).

This guide's Funnel commands require **Tailscale v1.52 or later** (the
`install.sh` script always installs the current release, so this is only a
concern if you already had an old version installed — check with
`tailscale version` and `sudo tailscale update` if needed).

## 3. Clone the repo

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/smitty220/fantasyfootball.git
cd fantasyfootball
```

## 4. Create secrets.env

Gridiron HQ reads credentials from `backend/secrets.env` (gitignored, never
committed, so a fresh clone won't have one). Create it and fill in real
values:

```bash
cat > backend/secrets.env <<'EOF'
# --- Yahoo (Fantasy Sports API app credentials) ---
YAHOO_CLIENT_ID=
YAHOO_CLIENT_SECRET=
# Must exactly match the redirect URI registered on your Yahoo developer app.
# You won't know your real https://<name>.ts.net URL until step 6 below —
# it's fine to leave this as localhost for now and come back to it before
# connecting a Yahoo league.
YAHOO_REDIRECT_URI=https://localhost:8000/api/auth/yahoo/callback

# --- FantasyPros (API key from secure.fantasypros.com/api-keys) ---
FANTASYPROS_API_KEY=

# --- App password gate (optional; leave blank to leave the app fully open) ---
OWNER_PASSWORD=
LEAGUE_PASSWORD=
SESSION_SECRET=
EOF
nano backend/secrets.env   # fill in the values, then Ctrl+O, Enter, Ctrl+X
```

`docker-compose.yml` bind-mounts this exact file into the container — there's
only ever one copy to keep track of, and it's the same file local (non-Docker)
dev reads too.

## 5. Build and start the app

```bash
docker compose up -d --build
```

The first build on a Pi compiles nothing exotic (Python/Node wheels are all
available as prebuilt ARM64 wheels), but downloading the `node:22-slim` and
`python:3.12-slim` base images plus `npm install` over a Pi's SD card/eMMC
storage still typically takes **10–20 minutes** the first time. Subsequent
`--build` runs are much faster thanks to Docker's layer cache.

Check it came up healthy:

```bash
docker compose ps
curl http://localhost:8000/api/health
```

The app is now reachable at `http://<pi-hostname-or-ip>:8000` on your LAN.

## 6. Run the initial data refresh

Gridiron HQ auto-refreshes its data sources on a schedule (see
`backend/app/services/scheduler.py`), but the first sync happens fastest if
you kick it off manually rather than waiting for the schedule:

- **Via the UI:** open `http://<pi-ip>:8000`, go to the Data page, and click
  "Refresh" for each source.
- **Via curl:**

  ```bash
  for source in crosswalk sleeper_players sleeper_trending fantasycalc \
                espn_projections espn_week_projections \
                fantasypros_projections fantasypros_week_projections; do
    curl -X POST "http://localhost:8000/api/data/refresh/$source"
    echo
  done
  ```

  (If you set `OWNER_PASSWORD` in `secrets.env`, log in first and reuse the
  cookie jar: `curl -c cj.txt -X POST http://localhost:8000/api/session/login -H 'Content-Type: application/json' -d '{"password":"..."}'`,
  then add `-b cj.txt` to each refresh call above.)

Connecting a Yahoo league is a browser OAuth flow (Yahoo no longer supports
the old device/PIN flow for new apps), so that part has to happen through the
UI: open the app, go to Leagues, and follow the "Connect Yahoo" prompt. This
is also the point where `YAHOO_REDIRECT_URI` (in `backend/secrets.env`) and
the redirect URI registered on your Yahoo developer app both need to match
your real public URL from step 7 below (`https://<name>.ts.net/api/auth/yahoo/callback`) —
update both, then `docker compose up -d` to pick up the new secrets.env value.

## 7. Expose it to the internet with Tailscale Funnel

One command does the whole job: it wires up an HTTPS reverse proxy in front
of the container (Tailscale terminates TLS with an auto-provisioned cert) and
turns on public Funnel access, then persists across reboots.

```bash
sudo tailscale funnel --bg --https=443 http://localhost:8000
```

The first time you run this, Tailscale prompts you to enable Funnel for your
tailnet (or asks an admin to approve it in the admin console) — follow the
printed link.

Find your public URL any time with:

```bash
tailscale funnel status
```

which prints something like:

```
https://your-pi.your-tailnet.ts.net (Funnel on)
|-- proxy http://127.0.0.1:8000
```

Gridiron HQ is now live at `https://<pi-name>.<tailnet-name>.ts.net` —
reachable from any device with internet access, not just your tailnet.

> Note: earlier Tailscale versions used a two-step `tailscale serve` +
> `tailscale funnel 443 on` syntax. As of Tailscale 1.52, `tailscale funnel`
> is a single command that does both; the two-step form is deprecated but
> still works if you're stuck on an older client.

To turn Funnel off later (e.g. to take the app offline while still using it
over the tailnet):

```bash
sudo tailscale funnel --https=443 off
```

## Updating

```bash
cd ~/fantasyfootball
git pull
docker compose up -d --build
```

Alembic migrations run automatically on container startup (see
`backend/app/main.py` / `backend/app/migrations.py`), so a `git pull` that
includes new migrations just works — no manual migration step needed.

## Checking logs

```bash
docker compose logs -f app        # follow live logs
docker compose logs --tail 200 app
docker compose ps                 # container + healthcheck status
```

## Backing up ./data

All persistent state (the SQLite database) lives in `./data` on the host,
bind-mounted into the container at `/app/data`. To back it up, the container
doesn't need to be stopped (SQLite handles concurrent readers fine for a
point-in-time copy), but stopping it first is safest if you want a
guaranteed-consistent snapshot:

```bash
docker compose stop app
tar -czf ~/gridiron-backup-$(date +%Y%m%d).tar.gz -C ~/fantasyfootball data
docker compose start app
```

Copy the resulting `.tar.gz` off the Pi (`scp`, a USB drive, cloud storage —
whatever you'd normally use) on whatever schedule you're comfortable with.
To restore, stop the app, extract the archive's `data/` directory back over
`./data`, then `docker compose up -d`.
