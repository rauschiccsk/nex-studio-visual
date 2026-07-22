#!/usr/bin/env bash
#
# Pre-commit hook — bootstrapped by NEX Studio Create Project (K-005).
# Blocks the commit if the local Lint checks fail, so a build we already know CI will
# reject never gets pushed (mirrors the CI `lint` stage: ruff format --check + ruff check
# on the backend, type-check on the frontend).
#
# Installed + activated by the scaffold (`git config core.hooksPath .githooks`).
# Skips docs-only commits (nothing lint-relevant staged).
# Bypass (rare, Director-approved only): `git commit --no-verify`.

set -euo pipefail
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "${REPO_ROOT}"

STAGED=$(git diff --cached --name-only --diff-filter=ACMR)
[[ -z "${STAGED}" ]] && exit 0

NEEDS_BACKEND=0
NEEDS_FRONTEND=0
while IFS= read -r path; do
    case "${path}" in
        backend/*) NEEDS_BACKEND=1 ;;
        frontend/*) NEEDS_FRONTEND=1 ;;
    esac
done <<< "${STAGED}"

if [[ "${NEEDS_BACKEND}" == "1" && -f backend/pyproject.toml ]]; then
    # Prefer `ruff` on PATH; fall back to the pip module form.
    RUFF="ruff"
    command -v ruff >/dev/null 2>&1 || RUFF="python3 -m ruff"
    echo "pre-commit: backend — ruff format --check"
    ( cd backend && ${RUFF} format --check . ) \
        || { echo "❌ ruff format --check FAILED — run: cd backend && ${RUFF} format ."; exit 1; }
    echo "pre-commit: backend — ruff check"
    ( cd backend && ${RUFF} check . ) \
        || { echo "❌ ruff check FAILED — run: cd backend && ${RUFF} check --fix ."; exit 1; }
fi

if [[ "${NEEDS_FRONTEND}" == "1" && -f frontend/package.json ]]; then
    echo "pre-commit: frontend — type-check"
    ( cd frontend && npm run type-check ) \
        || { echo "❌ frontend type-check FAILED"; exit 1; }
fi

echo "pre-commit: ✓ Lint checks passed"
