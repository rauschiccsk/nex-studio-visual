#!/usr/bin/env bash
# Telegram notify sender for ICC agents (CR-NS-011).
#
# Usage: notify_telegram.sh "<message text>" ["<chat_id>"]
#
# Routes a single Telegram message to a recipient:
#   - Bot token  : TELEGRAM_ICC_BOT_TOKEN, sourced from the central host env
#                  $TELEGRAM_CENTRAL_ENV (default
#                  /opt/infra/telegram/icc-agents.env).
#   - chat_id    : taken from the optional 2nd arg when given (the backend
#                  cockpit resolves the version owner's chat_id from the DB,
#                  CR-NS-018 Phase 5a). When omitted, falls back to
#                  TELEGRAM_NOTIFY_CHAT_ID, read (not sourced) from the repo
#                  .env at the git toplevel.
#
# No-ops (exit 0) whenever any piece of config is absent — never fails the
# session and never leaks the bot token to stdout/logs. Set NOTIFY_DRY_RUN=1
# to print "DRY: <chat_id> <text>" instead of calling Telegram (the token is
# never printed in any mode).

set -uo pipefail

MSG="${1:-}"
[ -z "$MSG" ] && exit 0
CHAT_ID_ARG="${2:-}"

CENTRAL_ENV="${TELEGRAM_CENTRAL_ENV:-/opt/infra/telegram/icc-agents.env}"

# Bot token from the central host env (trusted file). Missing/empty → no-op.
TELEGRAM_ICC_BOT_TOKEN=""
if [ -r "$CENTRAL_ENV" ]; then
    # shellcheck disable=SC1090
    . "$CENTRAL_ENV"
fi
[ -z "${TELEGRAM_ICC_BOT_TOKEN:-}" ] && exit 0

# chat_id: explicit 2nd arg wins (backend cockpit path); otherwise fall back to
# the repo .env (parsed line-by-line, never sourced — a project .env may contain
# arbitrary shell-unsafe content).
if [ -n "$CHAT_ID_ARG" ]; then
    CHAT_ID="$CHAT_ID_ARG"
else
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    [ -z "$REPO_ROOT" ] && exit 0
    ENV_FILE="$REPO_ROOT/.env"
    [ -r "$ENV_FILE" ] || exit 0
    CHAT_ID="$(grep -E '^TELEGRAM_NOTIFY_CHAT_ID=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
fi
# Strip optional surrounding quotes and any whitespace.
CHAT_ID="${CHAT_ID%\"}"; CHAT_ID="${CHAT_ID#\"}"
CHAT_ID="${CHAT_ID%\'}"; CHAT_ID="${CHAT_ID#\'}"
CHAT_ID="$(printf '%s' "$CHAT_ID" | tr -d '[:space:]')"
[ -z "$CHAT_ID" ] && exit 0

if [ "${NOTIFY_DRY_RUN:-}" = "1" ]; then
    printf 'DRY: %s %s\n' "$CHAT_ID" "$MSG"
    exit 0
fi

# Suppress all curl output (stdout + stderr) so the token embedded in the URL
# can never surface in logs; never fail the session.
curl -s -m 10 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_ICC_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${MSG}" >/dev/null 2>&1 || true
exit 0
