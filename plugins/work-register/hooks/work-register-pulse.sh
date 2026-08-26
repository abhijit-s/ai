#!/usr/bin/env bash
# SessionStart — two things, two audiences.
#
# 1. A NAG for the owner. The register drifts silently: cards sit in a lane while the work
#    behind them finishes elsewhere, and a week worked without captures reads as a week
#    idle. Nothing fails, so nothing prompts you to look. This fires at the one moment you
#    would act on it and stays quiet otherwise.
#
# 2. CONTEXT for the model. A session that has been re-tracked owns a slice of the board,
#    and without this it would learn that slice by reading a 12KB board or a 15KB day file
#    — or, more likely, not at all. Its open cards are its scope, so they belong in the
#    session's context from turn zero rather than behind a tool call nobody makes.
#
# Silent by design when healthy. A hook that speaks every session is a hook you learn to
# ignore, which would cost more than the drift it reports — so the nag still only speaks
# when something is stale, and the card list is capped rather than exhaustive.
#
# Track resolution lives HERE, not in the engine. The engine holds no vault path and reads
# no memory-kit state; wiring one subsystem's per-conversation ledger to another's board is
# glue, and glue belongs in the hook. Glue still does not get to invent a vault location:
# the ledger path is DERIVED from memory-kit's own per-machine corpus registry, which is
# the component that owns it. A hook that assumed one would be silently wrong on any other
# machine — and silently is the whole problem, since a wrong path is indistinguishable
# from an untracked session.

set -uo pipefail

# The engine ships inside this plugin. Claude Code exports CLAUDE_PLUGIN_ROOT when it runs
# this as a plugin hook; run straight from a checkout — a direct test, or a machine that
# holds the repo but has not installed the plugin — no such variable exists, so the root
# falls back to the script's own location. Neither branch makes the engine conditional on
# plugin context: it stays a plain script invoked by path.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
ENGINE="${WORK_REGISTER_ENGINE:-${PLUGIN_ROOT}/skills/work-register/scripts/sync_board.py}"
MEMORY_KIT_CONFIG="${MEMORY_KIT_CONFIG:-$HOME/.config/memory-kit/config.toml}"
# Enough to orient a session, few enough to still read as scope rather than wallpaper.
WORK_REGISTER_PULSE_CARDS="${WORK_REGISTER_PULSE_CARDS:-6}"

# memory-kit's per-conversation track ledger, resolved the way memory-kit resolves it:
# the default corpus's `data_root` + `memory_dir`. An explicit override still wins, for a
# machine whose ledger sits somewhere its registry does not describe. No registry means no
# tracks to read, so an empty answer is correct rather than a fallback worth guessing at.
resolve_ledger() {
  [ -r "$MEMORY_KIT_CONFIG" ] || return 0
  timeout 5 python3 - "$MEMORY_KIT_CONFIG" <<'PY' 2>/dev/null
import sys, tomllib
from pathlib import Path

try:
    with open(sys.argv[1], "rb") as handle:
        cfg = tomllib.load(handle)
except Exception:
    sys.exit(0)
corpus = cfg.get("corpus", {}).get(cfg.get("default_corpus"), {})
root = corpus.get("data_root")
if root:
    print(Path(root) / corpus.get("memory_dir", "Memory") / ".track-ledger")
PY
}

WORK_REGISTER_TRACK_LEDGER="${WORK_REGISTER_TRACK_LEDGER:-$(resolve_ledger)}"
# Enough to orient a session, few enough to still read as scope rather than wallpaper.
WORK_REGISTER_PULSE_CARDS="${WORK_REGISTER_PULSE_CARDS:-6}"

[ -f "$ENGINE" ] || exit 0

# Never let a nudge delay or break a session: no config, no python, no network, no matter.
verdict="$(timeout 10 python3 "$ENGINE" --status --brief 2>/dev/null | head -1)"
# Only nag when something is actually stale. The healthy verdict starts with ✅.
nag=""
case "$verdict" in
  *"⚠️"*) nag="$verdict" ;;
esac

# --- This conversation's track, if it has one -------------------------------------
#
# The ledger key is the basename of the payload's `transcript_path` minus its extension —
# memory-kit's `conversation_key`, which is stable across the compaction that rolls
# `session_id`. On a FRESH session this hook fires before any `::track` directive, so no
# track at turn zero is the normal case, not a fault; the payoff is on resume, when the
# key resolves the track the conversation was already working under.
payload=""
[ -t 0 ] || payload="$(cat)"

track=""
if [ -n "$payload" ] && [ -d "$WORK_REGISTER_TRACK_LEDGER" ] && command -v jq >/dev/null 2>&1; then
  transcript="$(printf '%s' "$payload" | jq -r '.transcript_path // ""' 2>/dev/null)"
  if [ -n "$transcript" ]; then
    key="$(basename -- "$transcript")"
    key="${key%.*}"
    # `_retired.json` is bookkeeping, not a conversation.
    if [ -n "$key" ] && [ "$key" != "_retired" ] && [ -r "$WORK_REGISTER_TRACK_LEDGER/$key.json" ]; then
      track="$(jq -r '.track // ""' "$WORK_REGISTER_TRACK_LEDGER/$key.json" 2>/dev/null)"
    fi
  fi
fi

# --- That track's open cards ------------------------------------------------------
cards=""
if [ -n "$track" ]; then
  slice="$(timeout 10 python3 "$ENGINE" --list --json --open --track "$track" 2>/dev/null)"
  total="$(printf '%s' "$slice" | jq 'length' 2>/dev/null)" || total=""
  if [ -n "${total:-}" ] && [ "$total" -gt 0 ] 2>/dev/null; then
    listed="$(printf '%s' "$slice" \
      | jq -r --argjson n "$WORK_REGISTER_PULSE_CARDS" \
          '.[:$n][] | "   \(.id)  [\(.column)]  \(.text[0:110])"' 2>/dev/null)"
    if [ -n "$listed" ]; then
      cards="Your work-register slice — track \`${track}\`, ${total} open card(s):
${listed}"
      if [ "$total" -gt "$WORK_REGISTER_PULSE_CARDS" ]; then
        cards="${cards}
   … $((total - WORK_REGISTER_PULSE_CARDS)) more — \`sync_board.py --list --open --track ${track}\`"
      fi
      cards="${cards}

Read one card's reasoning with \`sync_board.py --show <id>\`; the whole board with
\`sync_board.py --list\`. Do not read WORK-REGISTER.md to answer a question these answer."
    fi
  fi
fi

[ -n "$nag" ] || [ -n "$cards" ] || exit 0

stale_hint=""
if [ -n "$nag" ]; then
  stale_hint="$nag
   Take stock with \`sync_board.py --status\` (detail) or \`--probe\` (resolve each card's
   own pull-request / issue / canon references and propose status changes)."
fi

if [ -n "$cards" ] && command -v jq >/dev/null 2>&1; then
  # The two audiences split here. The cards are context the model should act on, so they go
  # through additionalContext; the nag is a message for the owner, so it also surfaces as
  # systemMessage. Mixing plain text and JSON on one stdout is not possible, so when there
  # is nothing to inject the plain-text branch below keeps the nag exactly as it was.
  context="$stale_hint${stale_hint:+

}$cards"
  envelope="$(jq -n --arg ctx "$context" --arg msg "$nag" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}
     + (if $msg == "" then {} else {systemMessage: $msg} end)' 2>/dev/null)"
  # A half-written envelope would reach the model as literal JSON, so an emit that failed
  # falls back to the nag rather than to garbage.
  if [ -n "$envelope" ]; then
    printf '%s\n' "$envelope"
    exit 0
  fi
fi

[ -n "$stale_hint" ] || exit 0
printf '%s\n' "$stale_hint"
