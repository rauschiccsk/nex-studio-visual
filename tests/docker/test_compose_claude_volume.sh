#!/usr/bin/env bash
# =============================================================================
# Test: docker-compose — Claude config volume mount + environment variables
#
# Verifies:
#   1. Volume mount /home/andros/.claude:/root/.claude:ro is configured
#   2. CLAUDE_CONFIG_DIR env var is set to /root/.claude
#   3. CLAUDE_CLI_PATH env var is set to claude
#   4. Claude CLI executable is accessible inside the container
#   5. Volume is mounted read-only
#
# Usage:
#   ./tests/docker/test_compose_claude_volume.sh
# =============================================================================
set -euo pipefail

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }

echo "=== Docker Compose — Claude Volume Mount Tests ==="

# ---------------------------------------------------------------------------
# Test 1: docker-compose.yml contains the volume mount
# ---------------------------------------------------------------------------
echo ""
echo "[1/5] Volume mount in docker-compose.yml"
if grep -q '/home/andros/.claude:/root/.claude:ro' docker-compose.yml; then
    pass "Volume mount /home/andros/.claude:/root/.claude:ro found"
else
    fail "Volume mount not found in docker-compose.yml"
fi

# ---------------------------------------------------------------------------
# Test 2: CLAUDE_CONFIG_DIR env var in docker-compose.yml
# ---------------------------------------------------------------------------
echo "[2/5] CLAUDE_CONFIG_DIR environment variable"
if grep -q 'CLAUDE_CONFIG_DIR.*"/root/.claude"' docker-compose.yml; then
    pass "CLAUDE_CONFIG_DIR=/root/.claude found in docker-compose.yml"
else
    fail "CLAUDE_CONFIG_DIR not found in docker-compose.yml"
fi

# ---------------------------------------------------------------------------
# Test 3: CLAUDE_CLI_PATH env var in docker-compose.yml
# ---------------------------------------------------------------------------
echo "[3/5] CLAUDE_CLI_PATH environment variable"
if grep -q 'CLAUDE_CLI_PATH.*"claude"' docker-compose.yml; then
    pass "CLAUDE_CLI_PATH=claude found in docker-compose.yml"
else
    fail "CLAUDE_CLI_PATH not found in docker-compose.yml"
fi

# ---------------------------------------------------------------------------
# Test 4: .env.example contains Claude variables
# ---------------------------------------------------------------------------
echo "[4/5] .env.example Claude variables"
ENV_OK=true
if ! grep -q 'CLAUDE_CONFIG_DIR=' .env.example; then
    fail "CLAUDE_CONFIG_DIR missing from .env.example"
    ENV_OK=false
fi
if ! grep -q 'CLAUDE_CLI_PATH=' .env.example; then
    fail "CLAUDE_CLI_PATH missing from .env.example"
    ENV_OK=false
fi
if [ "${ENV_OK}" = true ]; then
    pass "Both CLAUDE_CONFIG_DIR and CLAUDE_CLI_PATH in .env.example"
fi

# ---------------------------------------------------------------------------
# Test 5: Claude CLI accessible in built image
# ---------------------------------------------------------------------------
echo "[5/5] Claude CLI accessible in container"
IMAGE="nex-studio-backend:compose-test"

# Build image if not already built
if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "  Building image ${IMAGE}..."
    docker build -f backend/Dockerfile -t "${IMAGE}" . >/dev/null 2>&1
fi

CLAUDE_CHECK=$(docker run --rm -e CLAUDE_CONFIG_DIR=/root/.claude -e CLAUDE_CLI_PATH=claude \
    "${IMAGE}" sh -c 'which claude 2>/dev/null || command -v claude 2>/dev/null || echo "NOT_FOUND"' 2>&1)

if [ "${CLAUDE_CHECK}" != "NOT_FOUND" ] && [ -n "${CLAUDE_CHECK}" ]; then
    pass "Claude CLI accessible at: ${CLAUDE_CHECK}"
else
    fail "Claude CLI not found in container"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi

echo "All tests passed."
exit 0
