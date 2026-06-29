#!/usr/bin/env bash
#
# Deploy NEX Studio v2 PARALLEL PROD with an auto-derived, VISIBLE version (CR-V2-040).
#
# Bakes VITE_APP_VERSION = 2.0.<commit-count>+<short-sha> into the frontend so the sidebar version VISIBLY
# CHANGES on every deploy — the Manažér can tell which build is live and whether a fix actually landed.
# NEVER hardcode the version in a manual ``docker build``; always run THIS so the version stays honest.
#
# Usage:  scripts/deploy-v2-prod.sh [backend|frontend|all]   (default: all)
#
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

COMPOSE="/opt/prod-v2/nex-studio/docker-compose.yml"
PROJDIR="/opt/prod-v2/nex-studio"
TARGET="${1:-all}"
VER="2.0.$(git rev-list --count HEAD)+$(git rev-parse --short HEAD)"

echo "==> Deploying NEX Studio v2 PROD — version ${VER} (target: ${TARGET})"

if [[ "${TARGET}" == "all" || "${TARGET}" == "backend" ]]; then
  docker build -f backend/Dockerfile -t nex-studio-backend:v2.0.0 .
fi
if [[ "${TARGET}" == "all" || "${TARGET}" == "frontend" ]]; then
  docker build -f frontend/Dockerfile -t nex-studio-frontend:v2.0.0 \
    --build-arg VITE_API_BASE_URL="" --build-arg VITE_APP_VERSION="${VER}" ./frontend
fi

SERVICES=()
[[ "${TARGET}" == "all" || "${TARGET}" == "backend" ]] && SERVICES+=(backend)
[[ "${TARGET}" == "all" || "${TARGET}" == "frontend" ]] && SERVICES+=(frontend)
docker compose -f "${COMPOSE}" --project-directory "${PROJDIR}" up -d --force-recreate --wait "${SERVICES[@]}"

echo "==> Deployed ${VER}"
