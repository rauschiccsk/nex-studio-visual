#!/usr/bin/env bash
#
# Pre-commit hook — bootstrapped by NEX Studio Create Project (K-005).
# Blocks the commit if the local Lint checks fail, so a build we already know CI will
# reject never gets pushed (mirrors the CI `lint` stage: ruff format --check + ruff check
# on the backend, type-check + eslint on every frontend).
#
# A frontend is a top-level directory with its own package.json and vite.config.ts — frontend/ in every project,
# web/ too in a Web project (DEV-161: administration + public web). The rule finds them, so a frontend added later is
# checked without this hook being edited.
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
FRONTENDS=()
while IFS= read -r path; do
    case "${path}" in
        backend/*) NEEDS_BACKEND=1 ;;
        */*)
            dir="${path%%/*}"
            if [[ -f "${dir}/package.json" && -f "${dir}/vite.config.ts" && " ${FRONTENDS[*]} " != *" ${dir} "* ]]; then
                FRONTENDS+=("${dir}")
            fi
            ;;
    esac
done <<< "${STAGED}"

if [[ "${NEEDS_BACKEND}" == "1" && -f backend/pyproject.toml ]]; then
    # Prefer `ruff` on PATH; fall back to the pip module form.
    RUFF="ruff"
    command -v ruff >/dev/null 2>&1 || RUFF="python3 -m ruff"

    # Is the tool actually THERE? This distinction is load-bearing, not defensive padding. The AI Agent
    # commits from inside the backend container, where ruff is present neither on PATH nor as a module —
    # so without this check its very first backend commit died on "❌ ruff format --check FAILED", a
    # message that blames the code when the truth is that the linter is not installed. A hook that
    # cannot run its check must say so; it must not convict.
    if ! ${RUFF} --version >/dev/null 2>&1; then
        echo "pre-commit: backend — ruff SKIPPED (nie je nainštalovaný v tomto prostredí)"
        echo "            CI ho spustí tak či tak; lokálne doinštaluj: pip install ruff"
    else
        echo "pre-commit: backend — ruff format --check"
        ( cd backend && ${RUFF} format --check . ) \
            || { echo "❌ ruff format --check FAILED — run: cd backend && ${RUFF} format ."; exit 1; }
        echo "pre-commit: backend — ruff check"
        ( cd backend && ${RUFF} check . ) \
            || { echo "❌ ruff check FAILED — run: cd backend && ${RUFF} check --fix ."; exit 1; }
    fi
fi

for fe in "${FRONTENDS[@]}"; do
    echo "pre-commit: ${fe} — type-check"
    ( cd "${fe}" && npm run type-check ) \
        || { echo "❌ ${fe} type-check FAILED"; exit 1; }
    # ESLint runs here because the CI this hook exists to MIRROR makes `npm run lint` a hard gate
    # (templates/github-actions-workflow.yml). Without it the hook announced "Lint checks passed"
    # having run no linter at all, and the developer first heard about it from a red CI minutes later
    # — the precise failure this hook is for. Skipped, with a reason, when the project defines no
    # `lint` script: silence would be the same lie in the other direction.
    if ( cd "${fe}" && node -e "process.exit(require('./package.json').scripts?.lint ? 0 : 1)" ) 2>/dev/null; then
        echo "pre-commit: ${fe} — eslint"
        ( cd "${fe}" && npm run lint ) \
            || { echo "❌ ${fe} eslint FAILED — CI gates on this too"; exit 1; }
    else
        echo "pre-commit: ${fe} — eslint SKIPPED (no 'lint' script in ${fe}/package.json)"
    fi
done

echo "pre-commit: ✓ all checks passed"
