#!/usr/bin/env python3
"""Add-on entrypoint: /data/options.json -> environment -> uvicorn.

Home Assistant's Supervisor writes the add-on's configuration (the fields
declared under `schema:` in config.yaml, as edited in the add-on's
Configuration tab) to ``/data/options.json`` before starting the container.
The application itself knows nothing about Home Assistant — it reads
uppercase environment variables via ``app.config.Settings`` — so this script
is the whole adapter layer between the two.

It also pins down *where the data lives*. The application hard-codes a few
paths relative to its own source tree (``data/cache/`` in
app/services/crosswalk.py and friends, ``<db>/../backups/`` in
app/migrations.py), which all resolve under ``/app/data`` in this image. So
whatever host folder the Supervisor actually mounted, ``/app/data`` has to end
up pointing at it — see _resolve_data_dir().
"""

from __future__ import annotations

import json
import os
import shutil
import sys

OPTIONS_PATH = "/data/options.json"

# The application's data directory, as baked into the image's source paths.
APP_DATA = "/app/data"

# Where config.yaml asks for `addon_config` to be mounted, followed by the
# paths Supervisor versions have historically used when the `path` property is
# not honoured ("/<type-name>", and the older "/config").
DATA_DIR_CANDIDATES = (APP_DATA, "/addon_config", "/config")

# Fallback when nothing is mounted (e.g. a plain `docker run` for testing):
# the add-on's own /data volume, which Home Assistant does persist across
# restarts and updates.
DATA_DIR_FALLBACK = "/data/app-data"

# Add-on option name -> environment variable read by app.config.Settings.
ENV_MAP = {
    "owner_password": "OWNER_PASSWORD",
    "league_password": "LEAGUE_PASSWORD",
    "session_secret": "SESSION_SECRET",
    "fantasypros_api_key": "FANTASYPROS_API_KEY",
    "yahoo_client_id": "YAHOO_CLIENT_ID",
    "yahoo_client_secret": "YAHOO_CLIENT_SECRET",
    "yahoo_redirect_uri": "YAHOO_REDIRECT_URI",
    "scheduler_enabled": "SCHEDULER_ENABLED",
}


def log(message: str) -> None:
    print(f"[gridironhq] {message}", flush=True)


def load_options() -> dict:
    """Read the Supervisor-written options file.

    Missing or malformed is not fatal: every option is optional, and the
    application has a working default for each one. Starting with defaults and
    a loud log line beats refusing to boot.
    """
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as handle:
            options = json.load(handle)
    except FileNotFoundError:
        log(f"{OPTIONS_PATH} not found; starting with built-in defaults")
        return {}
    except (OSError, ValueError) as exc:
        log(f"could not read {OPTIONS_PATH} ({exc}); starting with built-in defaults")
        return {}

    if not isinstance(options, dict):
        log(f"{OPTIONS_PATH} is not a JSON object; ignoring it")
        return {}
    return options


def apply_options(options: dict) -> None:
    for key, env_name in ENV_MAP.items():
        if key not in options:
            # Leave the variable unset so Settings' own default applies —
            # notably for yahoo_redirect_uri, whose default lives in
            # app/config.py and should not be duplicated here as "".
            continue
        value = options[key]
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        os.environ[env_name] = str(value)

    # Keep the log useful without leaking secrets.
    configured = [k for k in ENV_MAP if options.get(k) not in (None, "")]
    log(f"options applied: {', '.join(configured) or '(none set)'}")


def _resolve_data_dir() -> str:
    """Pick the persistent directory and guarantee /app/data reaches it."""
    for candidate in DATA_DIR_CANDIDATES:
        if os.path.ismount(candidate):
            log(f"persistent storage mounted at {candidate}")
            data_dir = candidate
            break
    else:
        data_dir = DATA_DIR_FALLBACK
        log(
            "no add-on config folder is mounted (is `map:` missing from "
            f"config.yaml?); falling back to {data_dir}"
        )
        os.makedirs(data_dir, exist_ok=True)

    if os.path.realpath(data_dir) != os.path.realpath(APP_DATA):
        # The image's hard-coded cache/backup paths live under /app/data, so
        # point it at the real storage. Safe because this branch only runs
        # when /app/data is *not* itself the mount, in which case it is the
        # empty directory created by the Dockerfile.
        if os.path.islink(APP_DATA):
            os.unlink(APP_DATA)
        elif os.path.isdir(APP_DATA):
            shutil.rmtree(APP_DATA)
        os.symlink(data_dir, APP_DATA)
        log(f"linked {APP_DATA} -> {data_dir}")

    return data_dir


def main() -> None:
    apply_options(load_options())

    _resolve_data_dir()
    # Four slashes: sqlite:/// + the absolute path /app/data/app.db. Always
    # addressed through /app/data so it agrees with the cache and backup
    # directories the application derives from that same path.
    os.environ["DATABASE_URL"] = f"sqlite:///{APP_DATA}/app.db"
    log(f"DATABASE_URL={os.environ['DATABASE_URL']}")

    # app.config.Settings also reads .env / secrets.env relative to the working
    # directory; neither is shipped in this image, so the environment above is
    # the only source of configuration.
    os.chdir("/app/backend")

    argv = [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    log("starting uvicorn on 0.0.0.0:8000")
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:  # pragma: no cover - only on a broken image
        log(f"failed to exec uvicorn: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
