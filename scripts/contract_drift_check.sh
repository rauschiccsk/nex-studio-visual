#!/usr/bin/env bash
#
# The BE↔FE contract gate (v0.7.0 R2-c) — one script for both places that run it (DEV-207): the CI job
# „Contract Drift-Gate“ and the cockpit's pre-commit hook. Regenerates the frontend's contract types from the
# backend's OpenAPI schema and fails when they differ from what git holds — the index, i.e. what is about to be
# committed (in CI the checkout, so the commit itself).
#
# 23.10.2026 (DEV-203) the docstrings of two routes changed, which are their OpenAPI descriptions; codegen was not
# run, and only CI caught the stale file — a second commit to fix it.
#
# The regenerated file stays in the working tree, so after a failure `git add` it and commit again.

set -euo pipefail

GENERATED=frontend/src/services/api/pipeline.generated.ts
# DEV-373: the build buttons' plain names, written from backend/services/action_labels.py by the same codegen.
LABELS=frontend/src/services/api/actionLabels.generated.json

cd "$(git rev-parse --show-toplevel)"
# `npm run codegen` dumps the OpenAPI schema (poetry → backend.scripts.dump_openapi) and runs openapi-typescript.
(cd frontend && npm run codegen)
if ! git diff --exit-code -- "${GENERATED}" "${LABELS}"; then
    echo "::error::pipeline.generated.ts or actionLabels.generated.json is stale — run 'npm run codegen' in frontend/ and commit the result."
    exit 1
fi
