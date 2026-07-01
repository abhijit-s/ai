#!/usr/bin/env bash
# herdr-agent-name.sh — surface each Claude agent's session name AND current
# context-window usage in herdr's agent panel. Custom hook that lives BESIDE
# herdr's managed herdr-agent-state.sh (herdr overwrites the managed one on
# integration install/update; it never touches this one).
#
# herdr's sidebar row renders as `<state> · <agent> · <custom-status>`, and
# --custom-status is the ONLY field that shows per-agent text. herdr itself has
# no token/usage field, but the Claude transcript records per-turn usage, so we
# compute the live context size from it. Result e.g.:  herdr-setup · 142k/200k
# Wired on UserPromptSubmit so it refreshes every turn.
set -uo pipefail

# Only meaningful inside a herdr-managed pane.
[ "${HERDR_ENV:-}" = "1" ] || exit 0
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v herdr   >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# Hook payload (JSON) arrives on stdin; guard against an interactive TTY run.
if [ -t 0 ]; then input="{}"; else input="$(cat 2>/dev/null || echo '{}')"; fi

# Context-window limit in tokens. Default 1M for Opus 4.8's 1M-context setup;
# set HERDR_CTX_LIMIT=200000 for a standard 200k model.
limit="${HERDR_CTX_LIMIT:-1000000}"

status="$(HERDR_HOOK_INPUT="$input" HERDR_CTX_LIMIT="$limit" python3 - <<'PY'
import json, os, glob

try:
    inp = json.loads(os.environ.get("HERDR_HOOK_INPUT") or "{}")
except Exception:
    inp = {}

sid   = inp.get("session_id") or ""
tpath = inp.get("transcript_path") or ""
limit = int(os.environ.get("HERDR_CTX_LIMIT") or "1000000")

# Session name from Claude's per-session registry.
name = ""
if sid:
    for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("sessionId") == sid:
            name = d.get("name") or ""
            break

# Current context size = the latest main-line turn's input (fresh + cache),
# i.e. how full the window is right now. Subagent (sidechain) turns are skipped.
used = 0
if tpath and os.path.exists(tpath):
    try:
        with open(tpath, encoding="utf-8") as h:
            for line in h:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("isSidechain"):
                    continue
                u = (d.get("message") or {}).get("usage") or {}
                if u:
                    used = (u.get("input_tokens", 0)
                            + u.get("cache_read_input_tokens", 0)
                            + u.get("cache_creation_input_tokens", 0))
    except Exception:
        pass

def fmt(n):
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"{m:.0f}M" if m == int(m) else f"{m:.1f}M"
    return f"{round(n / 1000)}k"

parts = []
if name:
    parts.append(name)
if used:
    parts.append(f"{fmt(used)}/{fmt(limit)}")
print(" · ".join(parts))
PY
)"

[ -n "$status" ] || exit 0
herdr pane report-metadata "$HERDR_PANE_ID" \
  --source user:claude-session-name \
  --agent claude \
  --custom-status "$status" >/dev/null 2>&1 || true
exit 0
