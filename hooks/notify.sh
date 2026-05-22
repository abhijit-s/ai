#!/usr/bin/env bash

# notify.sh — Send a desktop notification from Claude Code hooks.
# Run `notify.sh -h` for full documentation.

set -o pipefail

show_help() {
  cat <<'EOF'
notify.sh — Send a desktop notification from Claude Code hooks

USAGE
  notify.sh [-s STATE] [-m MESSAGE] [-t TASK] [-q]
  echo '{...}' | notify.sh [-s STATE] [-m MESSAGE] [-t TASK] [-q]

OPTIONS
  -s STATE     Notification type (default: done). See STATES below.
  -m MESSAGE   Custom message body (overrides the default for STATE).
  -t TASK      Task/session label appended to the title. Falls back to
               $CLAUDE_TASK_NAME, then to the Claude session name (looked
               up via the hook payload's session_id), then to the tmux
               session name (when running inside tmux and the session has
               been renamed from its default numeric name).
  -q           Quiet: suppress the notification sound.
  -h           Show this help and exit.

STATES
  done         Task completed successfully       sound: Glass   urgency: normal
  incomplete   Task was not fully completed      sound: Purr    urgency: normal
  error        Task encountered an error         sound: Basso   urgency: critical
  warning      Task completed with warnings      sound: Purr    urgency: normal
  info         FYI                               sound: Tink    urgency: low
  <other>      Treated as a custom label         sound: Tink    urgency: normal

STDIN
  Reads JSON from stdin (optional). If `.stop_hook_active` is true, the
  script exits 0 without notifying — this prevents Stop-hook loops.

BACKENDS (auto-detected)
  macOS         terminal-notifier if installed (supports custom icon),
                falls back to osascript -> Notification Center
  Linux         notify-send (urgency + icon)
  Fallback      Terminal bell + plain text line

ICON
  Uses $CLAUDE_NOTIFY_ICON if set, else ~/.claude/assets/claude.png.
  If the file is missing, the backend's default icon is used.

ENVIRONMENT
  CLAUDE_TASK_NAME     Task label fallback when -t is not given.
  CLAUDE_NOTIFY_ICON   Override the notification icon path.
  CLAUDE_NOTIFY_QUIET  When set to 1/true/yes, suppress the notification
                       sound (equivalent to passing -q).

EXIT CODES
  0   Always (including the stop_hook_active short-circuit).

EXAMPLES
  notify.sh
  notify.sh -s done -m "Tests passed"
  notify.sh -s error -m "Build failed: 3 type errors"
  notify.sh -s info -m "Deploy started"
  notify.sh -s deploy -m "Pushed to staging"          # custom label
  notify.sh -s done -t fix-login-bug                  # explicit task name
  notify.sh -s done -m "silent ping" -q               # no sound
  CLAUDE_TASK_NAME=ingest-rewrite notify.sh           # via env var
  CLAUDE_NOTIFY_QUIET=1 notify.sh                     # silence via env var
  echo '{"stop_hook_active":true}' | notify.sh        # no-op
EOF
}

die() {
  echo "notify.sh: $*" >&2
  echo "Run 'notify.sh -h' for usage." >&2
  exit 2
}

STATE="done"
CUSTOM_MSG=""
TASK_ARG=""
QUIET=0
case "${CLAUDE_NOTIFY_QUIET:-}" in 1|true|TRUE|yes|YES) QUIET=1 ;; esac

while getopts ":s:m:t:qh" opt; do
  case "$opt" in
    s) STATE="$OPTARG" ;;
    m) CUSTOM_MSG="$OPTARG" ;;
    t) TASK_ARG="$OPTARG" ;;
    q) QUIET=1 ;;
    h) show_help; exit 0 ;;
    :) die "option -$OPTARG requires an argument" ;;
    \?) die "unknown option: -$OPTARG" ;;
  esac
done
shift $((OPTIND - 1))
[ $# -gt 0 ] && die "unexpected positional argument: $1"

# Read hook payload from stdin — but only when stdin isn't a TTY, otherwise
# `cat` blocks waiting for Ctrl-D in interactive use.
if [ -t 0 ]; then
  INPUT="{}"
else
  INPUT=$(cat 2>/dev/null || echo "{}")
fi

# CRITICAL: Prevent infinite Stop-hook loops.
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ]; then
  exit 0
fi

case "$STATE" in
  done)
    TITLE="🤖 Claude Code — Done"
    MESSAGE="${CUSTOM_MSG:-Task completed successfully}"
    URGENCY="normal"
    SOUND="Glass"
    ;;
  incomplete)
    TITLE="🤖 Claude Code — Incomplete"
    MESSAGE="${CUSTOM_MSG:-Task was not fully completed}"
    URGENCY="normal"
    SOUND="Purr"
    ;;
  error)
    TITLE="🤖 Claude Code — Error"
    MESSAGE="${CUSTOM_MSG:-Task encountered an error}"
    URGENCY="critical"
    SOUND="Basso"
    ;;
  warning)
    TITLE="🤖 Claude Code — Warning"
    MESSAGE="${CUSTOM_MSG:-Task completed with warnings}"
    URGENCY="normal"
    SOUND="Purr"
    ;;
  info)
    TITLE="🤖 Claude Code — Info"
    MESSAGE="${CUSTOM_MSG:-FYI}"
    URGENCY="low"
    SOUND="Tink"
    ;;
  *)
    TITLE="🤖 Claude Code — ${STATE^}"
    MESSAGE="${CUSTOM_MSG:-$STATE}"
    URGENCY="normal"
    SOUND="Tink"
    ;;
esac

# Resolve task label. Priority:
#   1. -t TASK
#   2. $CLAUDE_TASK_NAME
#   3. Claude session name (hook payload's session_id -> ~/.claude/sessions/*.json .name)
#   4. tmux session name (when renamed from the default numeric value)
TASK_NAME="${TASK_ARG:-${CLAUDE_TASK_NAME:-}}"
if [ -z "$TASK_NAME" ]; then
  session_id=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
  if [ -n "$session_id" ] && compgen -G "$HOME/.claude/sessions/*.json" >/dev/null; then
    TASK_NAME=$(jq -r --arg sid "$session_id" \
      'select(.sessionId == $sid) | .name // empty' \
      "$HOME"/.claude/sessions/*.json 2>/dev/null | head -1)
  fi
fi
if [ -z "$TASK_NAME" ] && [ -n "${TMUX:-}" ] && command -v tmux &>/dev/null; then
  candidate=$(tmux display-message -p '#S' 2>/dev/null || true)
  [[ -n "$candidate" && ! "$candidate" =~ ^[0-9]+$ ]] && TASK_NAME="$candidate"
fi
[ -n "$TASK_NAME" ] && TITLE="$TITLE · $TASK_NAME"

ICON_PATH="${CLAUDE_NOTIFY_ICON:-$HOME/.claude/assets/claude.png}"

notify() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    if command -v terminal-notifier &>/dev/null; then
      local args=(-title "$TITLE" -message "$MESSAGE")
      [ "$QUIET" -eq 1 ] || args+=(-sound "$SOUND")
      [ -f "$ICON_PATH" ] && args+=(-contentImage "$ICON_PATH" -appIcon "$ICON_PATH")
      terminal-notifier "${args[@]}" >/dev/null 2>&1
    else
      if [ "$QUIET" -eq 1 ]; then
        osascript -e "display notification \"$MESSAGE\" with title \"$TITLE\""
      else
        osascript -e "display notification \"$MESSAGE\" with title \"$TITLE\" sound name \"$SOUND\""
      fi
    fi
  elif command -v notify-send &>/dev/null; then
    local linux_icon="terminal"
    [ -f "$ICON_PATH" ] && linux_icon="$ICON_PATH"
    notify-send --urgency="$URGENCY" --icon="$linux_icon" "$TITLE" "$MESSAGE"
  else
    [ "$QUIET" -eq 1 ] || echo -e "\a"
    echo "[$TITLE] $MESSAGE"
  fi
}

notify
exit 0
