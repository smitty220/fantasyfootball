# Deploying Gridiron HQ on a Raspberry Pi

Two supported paths — pick the one that matches what's already on the Pi:

- **[Home Assistant OS](#home-assistant-os-add-on)** — the Pi runs HAOS. Gridiron
  HQ installs as a local add-on. HAOS owns Docker, so there is no Compose here;
  this path replaces the Compose one entirely.
- **[Raspberry Pi OS + Docker Compose](#raspberry-pi-os--docker-compose)** — a
  plain 64-bit Raspberry Pi OS / Debian bookworm install.

---

# Home Assistant OS (add-on)

On HAOS you can't SSH in and run `docker compose` — the Supervisor manages
every container on the box. Instead, Gridiron HQ ships as a **local add-on**:
a folder you copy into HAOS's `/addons` share, which Home Assistant then
builds and runs like any other add-on, with its options page for credentials
and a start-on-boot toggle.

Everything the add-on needs lives in `addons/gridironhq/` in this repo.

## 1. Get file access to the Pi

Install **one** of these from **Settings → Add-ons → Add-on Store**:

- **Samba share** (easiest — you'll want it later anyway for moving the
  database). After starting it, the Pi appears in Finder under *Network*, or
  connect directly with **Go → Connect to Server → `smb://homeassistant.local`**.
  Share it exposes: `addons` (or `local_apps` on newer versions), `config`,
  `addon_configs` (or `app_configs`), `share`, `backup`, `ssl`, `media`.
- **Advanced SSH & Web Terminal** — gives you `scp`/`rsync` into `/addons/`.

## 2. Stage the add-on source on your Mac

The Supervisor builds a local add-on with the add-on folder as the *entire*
Docker build context, so the application source has to be copied inside it
first. One script does that:

```bash
cd ~/path/to/fantasyfootball
./deploy/build-addon.sh
```

This rsyncs `backend/` and `frontend/` into `addons/gridironhq/app/` (minus
`node_modules`, `.venv`, the test suite, and `secrets.env` — credentials come
from the add-on's options, never from a file baked into the image). That
staging folder is gitignored; re-run the script after every `git pull`.

## 3. Copy the folder to the Pi

**Over Samba (Finder):** open the `addons` share and drag the whole
`addons/gridironhq` folder into it. You should end up with
`/addons/gridironhq/config.yaml` on the Pi.

**Over SSH:**

```bash
rsync -av --delete ~/path/to/fantasyfootball/addons/gridironhq/ \
  root@homeassistant.local:/addons/gridironhq/
```

(The SSH add-on's default user is `root` on port 22 once you've added your
public key to its configuration.)

## 4. Install it

1. **Settings → Add-ons → Add-on Store**.
2. Three-dot menu, top right → **Check for updates**.
3. Gridiron HQ appears under **Local add-ons** → open it → **Install**.

The build compiles the React frontend and resolves Python dependencies on the
Pi itself: expect **15–25 minutes** on a Pi 4/5, with long quiet stretches.
The Log tab shows progress.

## 5. Configure it

On the add-on's **Configuration** tab, fill in what you use — every field is
optional and each maps to one of the app's environment variables:

| Option | Maps to | Notes |
| --- | --- | --- |
| `owner_password` | `OWNER_PASSWORD` | Empty ⇒ **no password gate at all** |
| `league_password` | `LEAGUE_PASSWORD` | Read-only guests |
| `session_secret` | `SESSION_SECRET` | Any long random string; empty ⇒ everyone is logged out on each restart |
| `fantasypros_api_key` | `FANTASYPROS_API_KEY` | <https://secure.fantasypros.com/api-keys> |
| `yahoo_client_id` | `YAHOO_CLIENT_ID` | |
| `yahoo_client_secret` | `YAHOO_CLIENT_SECRET` | |
| `yahoo_redirect_uri` | `YAHOO_REDIRECT_URI` | Must match the Yahoo app's registered URI exactly |
| `scheduler_enabled` | `SCHEDULER_ENABLED` | Background auto-refresh |

Save, then **Start**. On the **Info** tab, turn on *Start on boot* and
*Watchdog*. The **OPEN WEB UI** button goes to `http://<pi>:8000`.

Confirm it's alive:

```bash
curl http://homeassistant.local:8000/api/health
# {"status":"ok","version":"0.1.0"}
```

## 6. Bring your existing database across

All persistent state lives in the add-on's own config folder on the host —
`/addon_configs/local_gridironhq/` — which is exactly the `addon_configs`
Samba share, so this is a drag-and-drop:

```
/addon_configs/local_gridironhq/
├── app.db          <- the SQLite database
├── backups/        <- one dated copy per day, 7 kept, written on every start
└── cache/          <- downloaded CSV/JSON caches, safe to delete
```

1. **Stop** the add-on first. SQLite holds the file open while it runs, and
   replacing it underneath a live connection corrupts that connection's view.
2. Copy `data/app.db` from your Mac in:

   **Finder:** *Go → Connect to Server →* `smb://homeassistant.local/addon_configs`,
   open `local_gridironhq`, drop `app.db` in and replace the existing file.

   **SSH (from the Mac):**

   ```bash
   scp ~/path/to/fantasyfootball/data/app.db \
     root@homeassistant.local:/addon_configs/local_gridironhq/app.db
   ```

3. **Start** the add-on and watch the Log tab. Alembic runs on every startup
   and will either report the schema is current or apply whatever migrations
   your copy was missing — no manual migration step.

If you'd rather start clean, skip this and run the first-time sync below
instead.

## 7. First data refresh

Open the web UI, go to the **Data** page and click **Refresh** on each source
(the first full sync takes a few minutes), then go to **Leagues** and follow
**Connect Yahoo**. The Yahoo step is a browser OAuth round-trip, so
`yahoo_redirect_uri` and the URI registered on your Yahoo developer app both
have to match the URL you're actually reaching the app on.

## 8. Remote access (Tailscale) — partially solved

There is an official-ish community **Tailscale** add-on
([hassio-addons/addon-tailscale](https://github.com/hassio-addons/addon-tailscale)),
which is the right way to get a tailnet onto HAOS. What it does and doesn't do
for *this* app, per its current documentation:

- ✅ **Tailnet access works out of the box.** The add-on runs with
  `host_network: true`, so once it's up, `http://<pi>.<tailnet>.ts.net:8000`
  reaches Gridiron HQ from any device on your tailnet.
- ✅ **Tailscale Serve to an arbitrary port is supported**, via the add-on's
  `services` option: each entry takes a `name` (with a `svc:` prefix), a
  `target` such as `http://127.0.0.1:8000`, a `protocol`
  (`http`/`https`/`tcp`/`tls-terminated-tcp`) and a `port`. Because of
  `host_network: true`, `127.0.0.1:8000` from inside the Tailscale add-on is
  the port this add-on publishes.
- ❌ **Funnel to an arbitrary port is not supported.** The add-on's docs state
  plainly that the `services` option "does not support Tailscale Funnel, only
  Tailscale Serve." Its `share_homeassistant` / `share_on_port` options *can*
  turn on Funnel, but only to present **Home Assistant itself**, not a second
  service.

So the `sudo tailscale funnel --bg --https=443 http://localhost:8000` trick
from the Docker Compose path below has **no direct equivalent on HAOS**.
Public HTTPS access is a **next step**, and the realistic options are:

- put the app behind Home Assistant's own reverse proxy / Nabu Casa Cloud, or
- run the funnel from a second machine on the tailnet that proxies to the Pi,
  or
- go back to the Compose path on Raspberry Pi OS if public access matters more
  than HAOS integration.

Set up tailnet access first (it covers phones and laptops you control) and
treat Funnel as a separate decision.

## Updating the add-on

The source ships inside the add-on folder, so an update is a re-copy plus a
rebuild:

```bash
cd ~/path/to/fantasyfootball
git pull
./deploy/build-addon.sh
rsync -av --delete addons/gridironhq/ root@homeassistant.local:/addons/gridironhq/
```

Then in Home Assistant: the add-on's three-dot menu → **Rebuild**. (If you also
bumped `version:` in `config.yaml`, the store shows a normal **Update** button
after *Check for updates* instead.)

Your data survives a rebuild untouched — it lives in `/addon_configs`, outside
the container.

## Logs and backups

- **Logs:** the add-on's **Log** tab, or `ha addons logs local_gridironhq` from
  the SSH add-on.
- **Backups:** the add-on declares `backup: cold`, so Home Assistant stops it
  before snapshotting — a Home Assistant backup therefore captures a
  consistent database. For an ad-hoc copy, just grab
  `/addon_configs/local_gridironhq/app.db` over Samba (the app also writes its
  own dated copies into `backups/` on every start).

---

# Raspberry Pi OS + Docker Compose

A copy-paste runbook for getting Gridiron HQ running on a fresh Raspberry Pi
4 or 5 (64-bit Raspberry Pi OS / Debian bookworm) and reachable from the
internet over HTTPS via [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) —
no port forwarding, no reverse proxy, no certificates to manage yourself.

Run every command below on the Pi itself (SSH in, or use a keyboard/monitor).

> Not applicable on Home Assistant OS — use the add-on path above.

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
