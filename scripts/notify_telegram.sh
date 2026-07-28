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
# Two modes, because two callers need opposite things:
#
#   * DEFAULT (fire-and-forget) — agent hooks. No-ops (exit 0) whenever any piece
#     of config is absent: a missing notify config must never fail an agent
#     session, and the token must never reach stdout/logs.
#   * CHECKED (NOTIFY_CHECK=1) — the backend. EVERY path now reports its outcome
#     on stdout ("TELEGRAM_OK" / "TELEGRAM_FAIL:<reason>") and exits non-zero
#     when nothing was sent. This used to exit 0 on every path INCLUDING the
#     silent no-ops, so a caller could not distinguish "delivered" from "no
#     token configured" — "notification sent" was structurally unknowable.
#
# Set NOTIFY_DRY_RUN=1 to print "DRY: <chat_id> <text>" instead of calling
# Telegram (the token is never printed in any mode).

set -uo pipefail

# Shared "cannot send" exit. In CHECKED mode it names the reason and fails
# (exit 3) so the backend learns WHY nothing was sent; in the default mode it
# stays a silent success so an agent hook is never broken by missing config.
give_up() {
    if [ "${NOTIFY_CHECK:-}" = "1" ]; then
        printf 'TELEGRAM_FAIL:%s\n' "$1"
        exit 3
    fi
    exit 0
}

MSG="${1:-}"
[ -z "$MSG" ] && give_up "prázdna správa"
CHAT_ID_ARG="${2:-}"

CENTRAL_ENV="${TELEGRAM_CENTRAL_ENV:-/opt/infra/telegram/icc-agents.env}"

# Bot token from the central host env (trusted file). Missing/empty → no-op.
TELEGRAM_ICC_BOT_TOKEN=""
if [ -r "$CENTRAL_ENV" ]; then
    # shellcheck disable=SC1090
    . "$CENTRAL_ENV"
fi
[ -z "${TELEGRAM_ICC_BOT_TOKEN:-}" ] && give_up "bot token nie je nakonfigurovaný na serveri (${CENTRAL_ENV})"

# chat_id: explicit 2nd arg wins (backend cockpit path); otherwise fall back to
# the repo .env (parsed line-by-line, never sourced — a project .env may contain
# arbitrary shell-unsafe content).
if [ -n "$CHAT_ID_ARG" ]; then
    CHAT_ID="$CHAT_ID_ARG"
else
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    [ -z "$REPO_ROOT" ] && give_up "chýba chat ID (nie je zadané a cwd nie je git repozitár)"
    ENV_FILE="$REPO_ROOT/.env"
    [ -r "$ENV_FILE" ] || give_up "chýba chat ID (v repozitári nie je čitateľný .env)"
    CHAT_ID="$(grep -E '^TELEGRAM_NOTIFY_CHAT_ID=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
fi
# Strip optional surrounding quotes and any whitespace.
CHAT_ID="${CHAT_ID%\"}"; CHAT_ID="${CHAT_ID#\"}"
CHAT_ID="${CHAT_ID%\'}"; CHAT_ID="${CHAT_ID#\'}"
CHAT_ID="$(printf '%s' "$CHAT_ID" | tr -d '[:space:]')"
[ -z "$CHAT_ID" ] && give_up "chat ID je prázdne"

if [ "${NOTIFY_DRY_RUN:-}" = "1" ]; then
    printf 'DRY: %s %s\n' "$CHAT_ID" "$MSG"
    # A dry run reached the send — that IS the verdict the CHECKED contract asks for. Without the
    # marker the backend sees no verdict, records a delivery FAILURE, and tells the operator
    # "Telegram nie je na serveri nakonfigurovaný." — a false diagnosis that sends them debugging a
    # healthy configuration, when all they did was rehearse the wiring.
    [ "${NOTIFY_CHECK:-}" = "1" ] && printf 'TELEGRAM_OK\n'
    exit 0
fi

# CHECKED mode (NOTIFY_CHECK=1) — the backend path (every nudge) + the "Poslať test" self-service button
# (v4.0.52): capture the API RESPONSE and report the outcome so the caller can tell a delivered message from
# an unreachable chat_id.
# TOKEN-SAFE: the token lives ONLY in the request URL; the Telegram RESPONSE body never contains it — we
# print only the parsed ``ok`` / ``description`` from that body. Prints ``TELEGRAM_OK`` (exit 0) or
# ``TELEGRAM_FAIL:<description>`` (exit 3). The default path below stays fire-and-forget (stdout suppressed).
if [ "${NOTIFY_CHECK:-}" = "1" ]; then
    RESP="$(curl -s -m 10 -X POST \
        "https://api.telegram.org/bot${TELEGRAM_ICC_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        --data-urlencode "text=${MSG}" 2>/dev/null || true)"
    if printf '%s' "$RESP" | grep -q '"ok":true'; then
        echo "TELEGRAM_OK"
        exit 0
    fi
    DESC="$(printf '%s' "$RESP" | grep -oE '"description":"[^"]*"' | head -1 | sed 's/^"description":"//; s/"$//')"
    echo "TELEGRAM_FAIL:${DESC:-neznáma chyba (žiadna odpoveď)}"
    exit 3
fi

# Suppress all curl output (stdout + stderr) so the token embedded in the URL
# can never surface in logs; never fail the session.
curl -s -m 10 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_ICC_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${MSG}" >/dev/null 2>&1 || true
exit 0
