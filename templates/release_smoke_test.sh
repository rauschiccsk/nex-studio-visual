#!/usr/bin/env bash
#
# release_smoke_test.sh — behavioural release acceptance (gate-g-hardening GAP 1; CR-V2-051 risk floor).
#
# Black-box acceptance, run by NEX Studio's engine at the Verifikácia release gate against the project's OWN
# compose brought up under an ISOLATED `-p <slug>-smoke` project. Host ports are stripped for isolation, so
# this script reaches the app via `docker compose exec` (NOT host curl). The engine passes the stack
# addressing through the environment:
#
#   SMOKE_PROJECT       the `-p` compose project name (<slug>-smoke)
#   SMOKE_COMPOSE       path to the project's docker-compose.yml
#   SMOKE_OVERRIDE      path to the ephemeral isolation override (container_name + ports stripped)
#   SMOKE_BACKEND       the backend service name (where Python + the app live)
#   SMOKE_FRONTEND      the frontend service name (may be empty)
#   SMOKE_BACKEND_PORT  the backend CONTAINER port the app listens on
#
# CONTRACT (the engine enforces ALL of these — CR-V2-051):
#   * exit 0  ⇒ every assertion passed.  ANY non-zero exit ⇒ FAIL → the Verifikácia PASS verdict is blocked.
#   * the script MUST print three sentinels (the LAST value of each is read):
#       ASSERTIONS_RUN=<n>           total assertions (n>0 — the anti-empty floor).
#       FEATURE_ASSERTIONS_RUN=<n>   positive behavioural assertions (≥1 per DECLARED flagship feature).
#       NEGATIVE_ASSERTIONS_RUN=<n>  negative/safety assertions (≥1 per DECLARED safety property — the risky
#                                    op MUST be REJECTED; a green "it works" test can never prove a safety
#                                    invariant, only a red-when-abused test can).
#   * RISK FLOOR: the engine reads the Návrh design's declared flagship features + safety properties and FAILs
#     the acceptance when FEATURE_ASSERTIONS_RUN < (declared features) or NEGATIVE_ASSERTIONS_RUN < (declared
#     safety properties). Missing coverage is a FAIL, never a silent SKIP. Proving the app BOOTS is not proving
#     it does what the spec promises, nor that it refuses what the spec forbids.
#
# ADD YOUR SPEC-DERIVED ASSERTIONS where marked below: one FEATURE assertion per flagship feature, one NEGATIVE
# assertion per safety property. The seeded floor is the app-starts assertion only.

set -euo pipefail

ASSERTIONS_RUN=0
FEATURE_ASSERTIONS_RUN=0
NEGATIVE_ASSERTIONS_RUN=0

# Print all three sentinels (called on every exit path so the engine always reads a final value).
emit_sentinels() {
  echo "ASSERTIONS_RUN=${ASSERTIONS_RUN}"
  echo "FEATURE_ASSERTIONS_RUN=${FEATURE_ASSERTIONS_RUN}"
  echo "NEGATIVE_ASSERTIONS_RUN=${NEGATIVE_ASSERTIONS_RUN}"
}

fail() {
  echo "ASSERTION FAILED: $*" >&2
  emit_sentinels
  exit 1
}

# Run a command inside the backend container of the running isolated smoke stack.
dc_exec() {
  docker compose -p "${SMOKE_PROJECT}" -f "${SMOKE_COMPOSE}" -f "${SMOKE_OVERRIDE}" exec -T "${SMOKE_BACKEND}" "$@"
}

# ── Assertion 1 (MANDATORY floor): the app is up and answers HTTP (any status < 500). ───────────────────
# Probe from inside the backend container with the stdlib (slim prod images ship no curl). A 4xx (e.g. a
# 404 on a versioned health route) still means "up"; only a connection error / 5xx is a failure. This is a
# boot assertion — it counts toward ASSERTIONS_RUN but is NEITHER a feature NOR a negative assertion.
dc_exec python - <<PY || fail "app did not answer HTTP on :${SMOKE_BACKEND_PORT:-8000}"
import sys, urllib.request, urllib.error
try:
    urllib.request.urlopen("http://localhost:${SMOKE_BACKEND_PORT:-8000}/health", timeout=5)
    sys.exit(0)
except urllib.error.HTTPError as exc:
    sys.exit(0 if exc.code < 500 else 1)
except Exception as exc:
    print("err", exc, file=sys.stderr)
    sys.exit(1)
PY
ASSERTIONS_RUN=$((ASSERTIONS_RUN + 1))

# ── FEATURE assertions (ADD ONE PER FLAGSHIP FEATURE). Positive behaviour — the spec's promise holds. ────
# Example shape — replace with real spec-derived checks. Bump BOTH counters for each:
#
#   body=$(dc_exec python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:${SMOKE_BACKEND_PORT}/api/v1/invoices').read().decode())")
#   echo "${body}" | grep -q '"peppol_valid":true' || fail "invoice export is not Peppol-valid"
#   ASSERTIONS_RUN=$((ASSERTIONS_RUN + 1)); FEATURE_ASSERTIONS_RUN=$((FEATURE_ASSERTIONS_RUN + 1))

# ── NEGATIVE / SAFETY assertions (ADD ONE PER SAFETY PROPERTY). The risky op MUST be REJECTED. ───────────
# A safety property is only proven by exercising the forbidden path and asserting it FAILS. Example shape —
# the operation is EXPECTED to be refused; the assertion fails if it was ALLOWED. Bump BOTH counters:
#
#   # safety property: "the read_only preset must block writes"
#   if dc_exec python -c "from app.agents.permissions import is_allowed; import sys; sys.exit(0 if is_allowed('READ_ONLY','Bash(cat x > y)') else 1)"; then
#     fail "SAFETY: read_only preset ALLOWED a write (cat redirection) — must be rejected"
#   fi
#   ASSERTIONS_RUN=$((ASSERTIONS_RUN + 1)); NEGATIVE_ASSERTIONS_RUN=$((NEGATIVE_ASSERTIONS_RUN + 1))

emit_sentinels
[ "${ASSERTIONS_RUN}" -gt 0 ]
