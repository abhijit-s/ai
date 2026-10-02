#!/usr/bin/env sh
# tuios-statusline.sh — feed the Claude Code status-line payload to tuios, but
# never let a tuios failure cost you the status line.
#
# tuios 0.8.4's `agent-statusline` segfaults when TUIOS_SOCKET names a path it
# cannot dial AND the payload carries a non-empty `model` object: the failed
# dial leaves a nil VerbClient that a deferred Close() dereferences
# (internal/session/verb_client.go:152 via cmd/tuios/agent_statusline.go:227).
# Deterministic, 20/20. Claude Code renders the status line constantly, so an
# unguarded call blanks it for the whole session.
#
# An `-S` test alone is not enough, which is the trap this file fell into: a
# daemon that dies without unlinking its socket leaves a file that -S accepts
# and tuios still cannot dial, so the guard admitted the crash in precisely the
# case it existed to catch. Enumerating dial failures is the wrong shape --
# run tuios and fall back whenever it fails, which also covers crashes that
# have not been met yet.
#
# Both the exit status and the output are checked. A panicking tuios still
# prints the status-line template with every field empty, so "produced output"
# alone accepts a crash; the status is captured into rc on the very next line
# because any command in between would overwrite $?.
#
# stdin is buffered because the first consumer would otherwise drain it and
# leave the fallback reading from a closed pipe.
payload=$(cat)
fallback="$HOME/.dotfiles/ai/statusline.sh"

if [ "${TUIOS_ENV:-}" = "1" ] && [ -S "${TUIOS_SOCKET:-}" ]; then
  line=$(printf '%s' "$payload" | tuios agent-statusline claude-code \
           --integration 1 --then "$fallback" 2>/dev/null)
  rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$line" ]; then
    printf '%s\n' "$line"
    exit 0
  fi
fi

printf '%s' "$payload" | "$fallback"
