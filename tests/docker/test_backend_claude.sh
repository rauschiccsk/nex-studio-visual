#!/usr/bin/env bash
# =============================================================================
# Test: backend Docker image — Node.js 20 + Claude CLI verification
#
# Usage:
#   ./tests/docker/test_backend_claude.sh [image_name]
#
# Default image: nex-studio-backend:test
# =============================================================================
set -euo pipefail

IMAGE="${1:-nex-studio-backend:test}"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }

echo "=== Building test image: ${IMAGE} ==="
docker build -f backend/Dockerfile -t "${IMAGE}" .

echo ""
echo "=== Running verification tests ==="

# Test 1: Python version
echo ""
echo "[1/6] Python version"
PY_VER=$(docker run --rm "${IMAGE}" python --version 2>&1)
if echo "${PY_VER}" | grep -q "Python 3.12"; then
    pass "Python 3.12 detected: ${PY_VER}"
else
    fail "Expected Python 3.12, got: ${PY_VER}"
fi

# Test 2: Node.js version
echo "[2/6] Node.js version"
NODE_VER=$(docker run --rm "${IMAGE}" node --version 2>&1)
if echo "${NODE_VER}" | grep -q "^v20\."; then
    pass "Node.js 20 detected: ${NODE_VER}"
else
    fail "Expected Node.js 20.x, got: ${NODE_VER}"
fi

# Test 3: npm available
echo "[3/6] npm available"
NPM_VER=$(docker run --rm "${IMAGE}" npm --version 2>&1)
if [ -n "${NPM_VER}" ]; then
    pass "npm available: ${NPM_VER}"
else
    fail "npm not found"
fi

# Test 4: Claude CLI installed
echo "[4/6] Claude CLI installed"
CLAUDE_VER=$(docker run --rm "${IMAGE}" claude --version 2>&1)
if [ -n "${CLAUDE_VER}" ]; then
    pass "Claude CLI available: ${CLAUDE_VER}"
else
    fail "Claude CLI not found"
fi

# Test 5: uvicorn importable
echo "[5/6] uvicorn importable"
if docker run --rm "${IMAGE}" python -c "import uvicorn" 2>&1; then
    pass "uvicorn importable"
else
    fail "uvicorn not importable"
fi

# Test 6: backend.main importable
echo "[6/6] backend.main importable"
if docker run --rm "${IMAGE}" python -c "from backend.main import app; print('app loaded')" 2>&1; then
    pass "backend.main:app importable"
else
    fail "backend.main not importable"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi

echo "All tests passed."
exit 0
