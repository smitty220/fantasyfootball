# Gridiron HQ

A self-hosted fantasy football helper. It syncs your Yahoo Fantasy leagues and
joins them with player data from FantasyPros, Sleeper, ESPN and FantasyCalc
into a local SQLite database, then gives you a free-agent browser, trade
analyzer, matchup preview and lineup editor on top of that.

This is a **local add-on**: it is not downloaded from a registry, it is built
on your Home Assistant machine from the source in this folder.

## Installation

1. Copy the whole `gridironhq` folder into Home Assistant's `/addons`
   directory. The easiest route is the **Samba share** add-on — the folder
   shows up as the `addons` (newer HA versions call it `local_apps`) share, so
   you can drag it across in Finder or Explorer. The **Advanced SSH & Web
   Terminal** add-on works too (`scp -r` into `/addons/`).
2. In Home Assistant go to **Settings → Add-ons → Add-on Store**, open the
   three-dot menu in the top right and choose **Check for updates**.
3. Gridiron HQ appears under **Local add-ons**. Open it and click **Install**.

The first build takes a while — it compiles the React frontend and resolves
the Python dependencies on the Pi itself. Budget **15–25 minutes** on a
Raspberry Pi 4/5 and don't be alarmed by the quiet stretches; watch the Log
tab if you want to see progress.

> **Important:** the folder must contain an `app/` subfolder holding
> `app/backend` and `app/frontend`. That is the application source, staged
> there by `deploy/build-addon.sh` in the project repo, because Home Assistant
> builds a local add-on with the add-on folder as the *entire* Docker build
> context — anything outside it is invisible to the build. If you copied the
> folder straight out of a fresh `git clone` without running that script, the
> build fails immediately on a missing `app/frontend/package.json`.

## Configuration

All fields are optional; fill in what you use, then **Save** and **Start**.

| Option | Description |
| --- | --- |
| `owner_password` | Password for full (read/write) access. **Leaving this empty disables the password gate entirely and the app is open to anyone who can reach it.** |
| `league_password` | Password for read-only guests (leaguemates). Only meaningful while `owner_password` is set. |
| `session_secret` | Any long random string. It signs the login cookie. If you leave it empty a new one is generated on every start, which logs everybody out each time the add-on restarts. |
| `fantasypros_api_key` | API key from <https://secure.fantasypros.com/api-keys>. Without it the FantasyPros projection sources stay empty; everything else still works. |
| `yahoo_client_id` | Client ID of your Yahoo Fantasy Sports developer app. |
| `yahoo_client_secret` | Matching client secret. |
| `yahoo_redirect_uri` | Must match the redirect URI registered on the Yahoo app **exactly**, e.g. `https://your-host.your-tailnet.ts.net/api/auth/yahoo/callback`. Yahoo dropped the out-of-band flow for apps created after ~Oct 2025, so this has to be a real HTTPS URL you can reach in a browser. |
| `scheduler_enabled` | Background auto-refresh of the data sources. Leave on unless you want to refresh purely by hand. |

Each of these is handed to the application as an environment variable at
startup, so changing an option and restarting the add-on is all that's needed
to apply it — there is no config file to edit inside the container.

## Opening the app

Once started, use the **OPEN WEB UI** button on the add-on page, or browse to
`http://<home-assistant-ip>:8000` from anywhere on your LAN.

## Where the data lives

The SQLite database, its dated backups and the HTTP response caches all live
in the add-on's own config folder on the host:

```
/addon_configs/local_gridironhq/
├── app.db          <- the database
├── backups/        <- one dated copy per day, 7 kept (made on every start)
└── cache/          <- downloaded CSV/JSON caches, safe to delete
```

That folder is exposed by the Samba add-on as the **`addon_configs`** share
(named `app_configs` on newer Home Assistant versions), so you can copy the
database in and out with a file manager. See "Moving an existing database in"
below.

Alembic migrations run automatically every time the add-on starts, so
upgrading the add-on over an existing `app.db` needs no manual step.

## Moving an existing database in

To bring across a database from an existing install (e.g. one that was running
under Docker Compose on a Mac or Pi):

1. **Stop** the add-on in Home Assistant.
2. Mount the Samba share (`smb://<home-assistant-ip>/addon_configs` in Finder),
   open `local_gridironhq`, and drop your `app.db` in, replacing the one
   that's there.
3. **Start** the add-on again and check the Log tab — you should see Alembic
   either confirm the schema is current or apply the migrations your old copy
   was missing.

Do not copy the file in while the add-on is running: SQLite keeps the database
open, and swapping the file underneath it corrupts the connection's view of it.

## First run

With no database to import, the app starts with an empty one. Open the web UI
and:

1. Go to the **Data** page and click **Refresh** on each source. The first full
   sync takes a few minutes.
2. Go to **Leagues** and follow **Connect Yahoo**. This is a browser OAuth
   round-trip, so `yahoo_redirect_uri` (above) and the redirect URI registered
   on your Yahoo developer app must already match the URL you're reaching the
   app on.

## Updating

Updates are a rebuild, because the source ships inside the add-on folder:

1. On your dev machine, `git pull`, then run `./deploy/build-addon.sh`.
2. Bump `version:` in `config.yaml` if you want Home Assistant to offer an
   "Update" button rather than needing a manual rebuild.
3. Copy the folder over the old one in `/addons`.
4. In the add-on page's three-dot menu choose **Rebuild** (or **Check for
   updates** in the store first if you bumped the version).

Your data is untouched by a rebuild — it lives in `/addon_configs`, not in the
container.

## Troubleshooting

**Build fails on `app/frontend/package.json` not found** — you skipped
`deploy/build-addon.sh`; see the note under Installation.

**The add-on starts but the log says "no add-on config folder is mounted"** —
the `map:` block in `config.yaml` didn't take effect. The add-on falls back to
its internal `/data` volume so nothing is lost, but that folder isn't reachable
over Samba. Uninstall and reinstall the add-on after confirming `config.yaml`
copied across intact.

**Everything returns 401** — `owner_password` is set and you're not signed in.
Open the web UI root and log in there; `/api/health` is the only endpoint that
stays open, which is what Home Assistant's watchdog uses.

**Port 8000 is already taken** by something else on your Home Assistant box:
change the host side of the port mapping in the add-on's **Network** panel.
The OPEN WEB UI link follows the change automatically.
