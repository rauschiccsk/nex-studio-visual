#!/usr/bin/env bash
#
# Deploy NEX Studio Visual to the live cockpit.
#
# Replaces scripts/deploy-v2-prod.sh, which was inherited from nex-studio when this project was forked
# and NEVER applied here: it deployed from /opt/prod-v2/nex-studio/docker-compose.yml (a path that does
# not exist on this machine), built images called nex-studio-backend / nex-studio-frontend (this stack
# runs nex-studio-visual-*), and derived a 2.0.N version (this line is v4.0.N). Anyone who trusted it
# would either watch it fail on the missing file or — if that directory were ever restored from a backup
# — bring up a SECOND stack beside the live one, on the same database. That is the shape of the incident
# that once took public routing down. (ICCINT-15, found 22.08.2026 while deploying ICCINT-12/13.)
#
# What it does, which is exactly the ritual that was being done by hand:
#   1. read the version the live compose is pinned to and derive the next one
#   2. build the images asked for, stamping the version into the frontend bundle
#   3. rewrite ONLY the image tags in the live compose (backed up first)
#   4. recreate the services and wait for healthy
#
# Migrations are NOT run here: the backend applies them itself on startup (backend/main.py).
#
# Usage:
#   scripts/deploy-prod.sh [--dry-run] [--version X.Y.Z] [backend|frontend|all]
#
# Default target is `all`. Use `frontend` for screen-only changes — it leaves the backend running, so a
# build waiting on the Manažér is not disturbed.

set -euo pipefail

COMPOSE="/opt/customers/dev/nex-studio-visual/docker-compose.yml"
PROJECT="nex-studio-visual-prod"
BACKEND_IMAGE="nex-studio-visual-backend"
FRONTEND_IMAGE="nex-studio-visual-frontend"

DRY_RUN=0
VERSION=""
TARGET="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    backend|frontend|all) TARGET="$1"; shift ;;
    *) echo "neznámy argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo "ODMIETNUTÉ: $*" >&2; exit 1; }

# ── Refusals ────────────────────────────────────────────────────────────────────────────────────────
# Each of these is a way the old script could have deployed somewhere other than the live stack. A
# deploy script that guesses is worse than no deploy script, because it looks like it worked.

[[ -f "$COMPOSE" ]] || die "compose súbor $COMPOSE neexistuje. Presne toto robil starý skript — ukazoval na cestu, ktorá tu nie je."

RUNNING="$(docker ps --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Names}}' | sort | tr '\n' ' ')"
[[ -n "$RUNNING" ]] || die "z projektu ${PROJECT} nebeží ani jeden kontajner. Nasadzoval by som vedľa toho, čo je naozaj v prevádzke."

CONFIGURED="$(docker inspect "${PROJECT}-backend-1" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)"
[[ "$CONFIGURED" == "$COMPOSE" ]] || die "bežiace kontajnery pochádzajú z '${CONFIGURED}', nie z '${COMPOSE}'. Nasadenie by vyrobilo druhý stack."

# The HIGHEST of the two, not the backend's. A screen-only deploy moves the frontend alone, so the two
# services sit on different versions until the next full one — reading either in isolation would propose
# a version that already exists and silently redeploy an old image under a new name.
# ``\K`` rather than a lookbehind: the two image names differ in length and PCRE lookbehind must be
# fixed-width, so the alternation would abort the whole script.
CURRENT="$(grep -oP "image: (?:${BACKEND_IMAGE}|${FRONTEND_IMAGE}):v\K[0-9]+\.[0-9]+\.[0-9]+" "$COMPOSE" \
  | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
[[ -n "$CURRENT" ]] || die "v compose sa nedá prečítať súčasná verzia obrazov ${BACKEND_IMAGE} / ${FRONTEND_IMAGE}."

if [[ -z "$VERSION" ]]; then
  VERSION="$(awk -F. '{printf "%s.%s.%d", $1, $2, $3 + 1}' <<<"$CURRENT")"
fi

echo "==> stack:      ${PROJECT}  (${COMPOSE})"
echo "==> beží:       ${RUNNING}"
echo "==> verzia:     v${CURRENT}  →  v${VERSION}"
echo "==> nasadzujem: ${TARGET}"

# The version being deployed MUST ship a release note (ICCINT-28). For a generated app NEX Studio writes
# this file itself from the version's epics; it does not build ITSELF that way, so its own note is written
# by hand — and until now nothing here wrote it or asked for it, so a deploy without one passed in silence.
# That is exactly what the v4.0.96 note complained about, and it recurred within a DAY of being written:
# the Aktualizácie tab sat at v4.0.96 while the app ran v4.1.3, seven releases with nothing said about them.
# Checked BEFORE the --dry-run exit on purpose — a dry-run that passes where the real deploy refuses is a
# trap, and the whole point is to find out now rather than after the images are built.
NOTE="$(git rev-parse --show-toplevel)/docs/specs/versions/v${VERSION}/RELEASE_NOTES.md"
[[ -s "$NOTE" ]] || die "v${VERSION} nemá poznámku k vydaniu. Napíš ju do ${NOTE} — jednou-dvoma vetami po slovensky, čo z toho má ten, kto appku používa (nie čo sa zmenilo v kóde). Bez nej sa v Aktualizáciách toto vydanie nikdy neobjaví."

if [[ "$DRY_RUN" == "1" ]]; then
  echo "==> --dry-run: nič sa nestavia ani nemení."
  exit 0
fi

cd "$(git rev-parse --show-toplevel)"

# ── Build ───────────────────────────────────────────────────────────────────────────────────────────
if [[ "$TARGET" == "all" || "$TARGET" == "backend" ]]; then
  docker build -f Dockerfile -t "${BACKEND_IMAGE}:v${VERSION}" .
fi
if [[ "$TARGET" == "all" || "$TARGET" == "frontend" ]]; then
  # The version is baked into the bundle so the sidebar shows what is actually deployed. Never hardcode
  # it in a manual `docker build` — that is how the screen starts lying about which version you are on.
  docker build -f frontend/Dockerfile -t "${FRONTEND_IMAGE}:v${VERSION}" \
    --build-arg VITE_API_BASE_URL="" --build-arg VITE_APP_VERSION="${VERSION}" ./frontend
fi

# ── Rewrite the live compose ────────────────────────────────────────────────────────────────────────
# ONLY the image tags. Routing, ports, volumes and env are hand-maintained on this file and nothing here
# may touch them.
cp -a "$COMPOSE" "${COMPOSE}.bak-${CURRENT}"

SERVICES=()
if [[ "$TARGET" == "all" || "$TARGET" == "backend" ]]; then
  sed -i "s|image: ${BACKEND_IMAGE}:v[0-9.]*|image: ${BACKEND_IMAGE}:v${VERSION}|" "$COMPOSE"
  # ``APP_VERSION`` is what /health reports. Its default in the compose had been left at 4.0.78 while the
  # image moved on sixteen deploys, so the backend spent all of them claiming to be a version it was not.
  # A version that is only right when somebody remembers to update it will be wrong; bump it with the tag.
  sed -i "s|APP_VERSION: \"\${APP_VERSION:-[0-9.]*}\"|APP_VERSION: \"\${APP_VERSION:-${VERSION}}\"|" "$COMPOSE"
  SERVICES+=(backend)
fi
if [[ "$TARGET" == "all" || "$TARGET" == "frontend" ]]; then
  sed -i "s|image: ${FRONTEND_IMAGE}:v[0-9.]*|image: ${FRONTEND_IMAGE}:v${VERSION}|" "$COMPOSE"
  SERVICES+=(frontend)
fi

echo "==> zmeny v compose:"
diff "${COMPOSE}.bak-${CURRENT}" "$COMPOSE" || true

# ── Up ──────────────────────────────────────────────────────────────────────────────────────────────
docker compose -f "$COMPOSE" up -d --force-recreate --wait "${SERVICES[@]}"

echo "==> nasadené v${VERSION}"
docker ps --filter "label=com.docker.compose.project=${PROJECT}" --format '    {{.Names}}  {{.Image}}  {{.Status}}'
