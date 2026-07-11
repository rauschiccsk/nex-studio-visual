#!/usr/bin/env bash
#
# NEX Studio v3 — deploy + version stamp.
#
# Fixes the "version frozen at v3.0.0" problem: the displayed version is BAKED
# at build time (FE: the ``VITE_APP_VERSION`` build-arg → ``import.meta.env`` in
# the bundle; BE: the ``APP_VERSION`` env → ``/health``). A plain ``docker build``
# never passed it, so it stayed at the Dockerfile default. This script computes
# ``3.0.N`` (N = commit count on the v3 line, the same scheme v2 used for 2.0.N),
# bakes it into BOTH images, optionally publishes a Slovak Aktualizácie note at
# ``docs/specs/versions/v3.0.N/RELEASE_NOTES.md`` (the existing release-notes API
# globs ``v*/RELEASE_NOTES.md`` and serves it newest-first — no app-code change),
# and recreates the v3 PROD containers.
#
# Usage:
#   scripts/deploy-v3.sh                      # deploy, stamp version, no new note
#   scripts/deploy-v3.sh --notes <file.md>    # + publish a Slovak Aktualizácie note
#
# The notes file may contain the token ``{{VERSION}}`` — it is replaced with the
# stamped version (e.g. ``v3.0.130``) before publishing, so the heading matches
# the version dir the API derives.

set -euo pipefail

REPO="/opt/projects/nex-studio"
# The running v3 cockpit's deploy artifacts moved under /opt/customers/dev/ in the 2026-07 /opt
# consolidation (was /opt/prod-v3/nex-studio, which no longer exists). The containers are unchanged
# (nex-studio-v3-prod-*, ports 9206/9207); only the compose+env location moved.
COMPOSE="/opt/customers/dev/nex-studio/docker-compose.yml"
ENVFILE="/opt/customers/dev/nex-studio/.env"

NOTES=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --notes) NOTES="${2:?--notes needs a file path}"; shift 2 ;;
        *) echo "deploy-v3: unknown arg: $1" >&2; exit 2 ;;
    esac
done

cd "$REPO"

# ── Version = 3.0.N (custom monotonic counter, obs #2) ─────────────────────
# Was ``git rev-list --count main..HEAD`` — but each deploy makes TWO commits (the
# code change + this note), so published Aktualizácie numbers SKIPPED (only odd:
# …161, 163, 165…), which reads as missing versions. Use a COMMITTED counter that
# advances by EXACTLY 1 per published note → continuous numbering. Seeded on first
# run from the highest existing ``v3.0.<N>`` note dir so it CONTINUES from there
# (never resets to a lower number).
COUNTER_FILE="docs/specs/versions/.version-counter"
if [[ -f "$COUNTER_FILE" ]]; then
    LAST=$(cat "$COUNTER_FILE")
else
    LAST=$(ls -d docs/specs/versions/v3.0.* 2>/dev/null | sed 's#.*/v3\.0\.##' | sort -n | tail -1)
    LAST=${LAST:-0}
fi
if [[ -n "$NOTES" ]]; then
    VERSION="3.0.$((LAST + 1))"   # a published note advances the counter by exactly 1
else
    VERSION="3.0.${LAST}"          # no note → stamp the current version, don't advance
fi
echo "deploy-v3: stamping VERSION=${VERSION}"

# ── Publish the Slovak Aktualizácie note (optional) ────────────────────────
if [[ -n "$NOTES" ]]; then
    [[ -f "$NOTES" ]] || { echo "deploy-v3: notes file not found: $NOTES" >&2; exit 2; }
    DEST="docs/specs/versions/v${VERSION}/RELEASE_NOTES.md"
    if [[ -e "$DEST" ]]; then
        echo "deploy-v3: ${DEST} already exists — refusing to overwrite" >&2
        exit 2
    fi
    mkdir -p "$(dirname "$DEST")"
    sed "s|{{VERSION}}|v${VERSION}|g" "$NOTES" > "$DEST"
    echo "$((LAST + 1))" > "$COUNTER_FILE"   # advance the monotonic counter; committed WITH the note
    git add "$DEST" "$COUNTER_FILE"
    git commit -q -m "docs(aktualizacie): v${VERSION}"
    echo "deploy-v3: published ${DEST} (counter → $((LAST + 1)))"
fi

# ── Build both images with the version baked in ────────────────────────────
echo "deploy-v3: building frontend (VITE_APP_VERSION=${VERSION})…"
docker build --build-arg VITE_APP_VERSION="${VERSION}" \
    -t nex-studio-frontend:v3.0.0 -f frontend/Dockerfile frontend/
echo "deploy-v3: building backend…"
docker build -t nex-studio-backend:v3.0.0 -f backend/Dockerfile .

# ── Recreate v3 PROD with APP_VERSION stamped for the backend /health ──────
# Inline ``APP_VERSION`` wins compose interpolation over the .env default; the
# --env-file still supplies the runtime secrets (SECRET_KEY, OAuth, GH tokens).
echo "deploy-v3: recreating v3 containers…"
APP_VERSION="${VERSION}" docker compose -f "$COMPOSE" --env-file "$ENVFILE" up -d

# ── Verify ─────────────────────────────────────────────────────────────────
sleep 6
echo "deploy-v3: backend /health →"
curl -fsS "http://localhost:9206/health" 2>/dev/null || echo "  (health not ready yet — check 'docker ps')"
echo
echo "deploy-v3: DONE — deployed ${VERSION}"
