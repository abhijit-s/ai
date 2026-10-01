#!/usr/bin/env sh
# tuios-statusline.sh — feed the Claude Code status-line payload to tuios, but
# only from inside a daemon-managed tuios pane.
#
# Why the guard: tuios 0.8.3's `agent-statusline` segfaults when no daemon
# answers — a nil VerbClient closed in a deferred func
# (internal/session/verb_client.go:152 via cmd/tuios/agent_statusline.go:227).
# Measured 30/30 panics with no daemon, 30/30 clean with one. Claude Code runs
# the status line on every conversation change, so the unguarded command takes
# the status line down in every Claude session outside tuios.
#
# Drop this wrapper once upstream nil-checks that Close.
if [ "${TUIOS_ENV:-}" = "1" ] && [ -S "${TUIOS_SOCKET:-}" ]; then
  exec tuios agent-statusline claude-code --integration 1 --then "$HOME/.dotfiles/ai/statusline.sh"
fi
exec "$HOME/.dotfiles/ai/statusline.sh"
