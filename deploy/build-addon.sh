#!/usr/bin/env bash
#
# Stage the application source into the Home Assistant add-on folder.
#
# Why this exists: when Home Assistant's Supervisor builds a local add-on it
# runs `docker build` with the *add-on folder itself* as the build context
# (/addons/gridironhq). Nothing outside that folder is visible to the build,
# so `backend/` and `frontend/` — which live at the repo root — have to be
# copied inside it first. This script does that copy into
# `addons/gridironhq/app/` (gitignored), which is what the add-on Dockerfile
# expects to find.
#
# Run this on your Mac before copying addons/gridironhq to the Pi:
#
#     ./deploy/build-addon.sh
#
# ...then copy the whole addons/gridironhq folder into HAOS's /addons share.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON_DIR="$REPO_ROOT/addons/gridironhq"
STAGE="$ADDON_DIR/app"

if [[ ! -f "$ADDON_DIR/config.yaml" ]]; then
  echo "error: $ADDON_DIR/config.yaml not found — wrong repo root?" >&2
  exit 1
fi

command -v rsync >/dev/null || { echo "error: rsync is required" >&2; exit 1; }

echo "Staging app source into $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# backend/ — everything the runtime image needs (app, alembic, pyproject,
# uv.lock). Deliberately NOT copied: the virtualenv (rebuilt in the image),
# caches, the test suite, and secrets.env (credentials come from the add-on's
# options, never baked into the image).
rsync -a \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.py[co]' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude 'tests/' \
  --exclude 'secrets.env' \
  --exclude '.env' \
  --exclude '.DS_Store' \
  "$REPO_ROOT/backend/" "$STAGE/backend/"

# frontend/ — sources only; node_modules and dist are produced inside the
# image's node build stage (a Mac-built node_modules would be the wrong
# platform for an arm64 Pi anyway).
rsync -a \
  --exclude 'node_modules/' \
  --exclude 'dist/' \
  --exclude '.DS_Store' \
  "$REPO_ROOT/frontend/" "$STAGE/frontend/"

# Fail loudly here rather than 15 minutes into a build on the Pi.
for required in \
  "$STAGE/backend/pyproject.toml" \
  "$STAGE/backend/uv.lock" \
  "$STAGE/backend/alembic.ini" \
  "$STAGE/backend/app/main.py" \
  "$STAGE/frontend/package.json"; do
  [[ -e "$required" ]] || { echo "error: missing $required after staging" >&2; exit 1; }
done

# Belt and braces: secrets must never end up in an image layer.
if find "$STAGE" -name 'secrets.env' -o -name '.env' | grep -q .; then
  echo "error: a secrets file made it into $STAGE — aborting" >&2
  exit 1
fi

echo "Staged $(du -sh "$STAGE" | cut -f1) into $STAGE"
echo
echo "Next: copy $ADDON_DIR into the HAOS /addons share, then"
echo "Settings -> Add-ons -> Add-on Store -> (three-dot menu) -> Check for updates."
