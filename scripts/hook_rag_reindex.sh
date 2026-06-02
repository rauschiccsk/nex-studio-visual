#!/usr/bin/env bash
# PostToolUse hook — auto-reindex RAG when a KB markdown file is modified.
#
# Reads Claude Code hook JSON from stdin, extracts tool_input.file_path,
# and if it points to /home/icc/knowledge/**.md, re-indexes that file.
#
# Wired in .claude/settings.json under hooks.PostToolUse for Edit|Write|MultiEdit.
# Silent on miss (non-KB files) — hook must not disrupt the main session.

set -uo pipefail

# Read hook JSON from stdin, extract file_path (handle absent gracefully).
INPUT="$(cat)"
FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"

# No path → nothing to do.
[ -z "$FILE_PATH" ] && exit 0

# Scope: only files under KB root and only markdown.
case "$FILE_PATH" in
    /home/icc/knowledge/*.md) ;;
    *) exit 0 ;;
esac

# Skip if file was deleted (e.g. by a tool run that removed it) or unreadable.
[ -r "$FILE_PATH" ] || exit 0

cd /opt/projects/nex-studio || exit 0

# Run reindex; log to stderr so Claude Code can surface it, but never fail the hook.
poetry run python scripts/rag_index.py "$FILE_PATH" >&2 || true
exit 0
