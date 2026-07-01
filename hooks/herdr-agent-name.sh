#!/usr/bin/env bash
# herdr-agent-name.sh — surface each Claude agent's session name in herdr's
# agent panel. Custom hook that lives BESIDE herdr's managed herdr-agent-state.sh
# (herdr overwrites the managed one on integration install/update; it never
# touches this one).
#
# herdr's sidebar row renders as `<state> · <agent> · <custom-status>`, and
# --custom-status is the ONLY field that shows per-agent text — title,
# display-agent, and `agent rename` all get stored but never rendered. So we map
# the Claude session_id -> the session name in ~/.claude/sessions/*.json .name
# -> report it as the pane's custom status. Wired on UserPromptSubmit so it
# refreshes whenever you rename the session mid-conversation.
set -uo pipefail

# Only meaningful inside a herdr-managed pane.
[ "${HERDR_ENV:-}" = "1" ] || exit 0
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v herdr >/dev/null 2>&1 || exit 0
command -v jq   >/dev/null 2>&1 || exit 0

# Hook payload (JSON) arrives on stdin; guard against an interactive TTY run.
if [ -t 0 ]; then input="{}"; else input="$(cat 2>/dev/null || echo '{}')"; fi
sid="$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)"
[ -n "$sid" ] || exit 0

# Resolve the human-facing session name Claude stores per session id.
compgen -G "$HOME/.claude/sessions/*.json" >/dev/null 2>&1 || exit 0
name="$(jq -r --arg sid "$sid" 'select(.sessionId == $sid) | .name // empty' \
  "$HOME"/.claude/sessions/*.json 2>/dev/null | head -1)"
[ -n "$name" ] || exit 0

# Display-only metadata; a stable --source lets each call replace the last value
# without disturbing herdr's semantic agent state (idle/working/blocked).
herdr pane report-metadata "$HERDR_PANE_ID" \
  --source user:claude-session-name \
  --agent claude \
  --custom-status "$name" >/dev/null 2>&1 || true
exit 0
