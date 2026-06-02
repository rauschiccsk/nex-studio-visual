#!/usr/bin/env bash
# PostToolUse hook — Telegram-notify the Director when an agent writes a
# DONE report or a question into the .dedo-channel inbox (CR-NS-011).
#
# Reads Claude Code hook JSON from stdin, extracts tool_input.file_path, and
# fires only for agent-authored channel reports:
#     */.dedo-channel/inbox/<role>-to-<target>-*.md
#   role   ∈ {implementer, auditor, designer, coordinator, customer}
#   target ∈ {dedo, director}
#
# Notifies only when the YAML frontmatter type ∈ {done-report, question}
# (stop / blocked are treated as question-class), and never for outbound
# messages (from: dedo / director). Silent on every miss — the hook must
# never disrupt the main session.
#
# Wired by Dedo into .claude/agents/<role>/settings.json hooks.PostToolUse
# for Write|Edit. Mirrors hook_rag_reindex.sh structure.

set -uo pipefail

INPUT="$(cat)"
FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -z "$FILE_PATH" ] && exit 0

# Scope 1: must live under a .dedo-channel/inbox/ and be markdown.
case "$FILE_PATH" in
    */.dedo-channel/inbox/*.md) ;;
    *) exit 0 ;;
esac

# Scope 2: filename must be an agent → dedo/director report.
BASENAME="$(basename "$FILE_PATH")"
case "$BASENAME" in
    implementer-to-dedo-*.md|implementer-to-director-*.md|\
    auditor-to-dedo-*.md|auditor-to-director-*.md|\
    designer-to-dedo-*.md|designer-to-director-*.md|\
    coordinator-to-dedo-*.md|coordinator-to-director-*.md|\
    customer-to-dedo-*.md|customer-to-director-*.md) ;;
    *) exit 0 ;;
esac

[ -r "$FILE_PATH" ] || exit 0

# Read a single key from the first YAML frontmatter block.
_frontmatter() {
    awk -v key="$1" '
        /^---[[:space:]]*$/ { fence++; next }
        fence == 1 && index($0, key ":") == 1 {
            sub("^" key ":[[:space:]]*", "")
            print
            exit
        }
    ' "$FILE_PATH"
}

FROM="$(_frontmatter from)"
TOPIC="$(_frontmatter topic)"
TYPE="$(_frontmatter type)"

# Outbound (Dedo / Director authored) → skip.
case "$FROM" in
    dedo|director) exit 0 ;;
esac

# Trigger only on the two report classes.
case "$TYPE" in
    done-report) LABEL="done" ;;
    question) LABEL="question" ;;
    stop|blocked) LABEL="question" ;;
    *) exit 0 ;;
esac

# Role from the filename prefix (<role>-to-...), capitalized for display.
ROLE="${BASENAME%%-to-*}"
ROLE_CAP="$(printf '%s' "${ROLE:0:1}" | tr '[:lower:]' '[:upper:]')${ROLE:1}"

# Project = repo-root basename: .../<project>/.dedo-channel/inbox/<file>.md
PROJECT="$(basename "$(dirname "$(dirname "$(dirname "$FILE_PATH")")")")"

TEXT="🔔 AG ${ROLE_CAP} [${LABEL}]: ${TOPIC} · ${PROJECT}"

"$(dirname "$0")/notify_telegram.sh" "$TEXT" || true
exit 0
