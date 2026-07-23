#!/usr/bin/env bash
# herdr-agent-name.sh — rename each Claude pane to its session name in herdr's
# UI, and surface live context-window usage as the pane's custom status.
# Custom hook that lives BESIDE herdr's managed herdr-agent-state.sh (herdr
# overwrites the managed one on integration install/update; it never touches
# this one).
#
# Without a label, herdr falls back to showing a pane's bare agent type
# ("claude") wherever it renders that pane's name. `herdr pane rename` sets a
# real, persistent label instead — report-metadata --custom-status only
# affects the small per-agent status line (`<state> · <agent> ·
# <custom-status>`), not the pane's actual name. Preference order for the
# label:
#   1. the session's customTitle — set explicitly via /rename or /resume's
#      Ctrl+R rename
#   2. the session's auto-derived name from ~/.claude/sessions/<pid>.json
#   3. the cwd basename, as a last resort
# ~/.claude/sessions/<pid>.json is written once at process start and never
# updated again, so #2 goes stale the moment you `/resume` a DIFFERENT,
# never-renamed session in the same running process (same pid, new
# session_id) — the file still lists the session that was active at boot.
# #3 exists so that case still gets a real label instead of the stale one.
# herdr's own manual pane-rename UI has no notion of "source", so this hook
# will overwrite a manually-set pane label on the next turn.
# Wired on UserPromptSubmit so it refreshes every turn.
#
# Toggles (export in your shell profile or a herdr-managed pane's env):
#   HERDR_AGENT_NAME_DISABLE=1   skip this hook entirely (a no-op, as if unwired)
#   HERDR_PANE_RENAME_DISABLE=1  keep the custom-status refresh, skip only the
#                                pane-label rename (i.e. the pre-2026-07-23 behavior)
set -uo pipefail

# Only meaningful inside a herdr-managed pane.
[ "${HERDR_AGENT_NAME_DISABLE:-0}" = "1" ] && exit 0
[ "${HERDR_ENV:-}" = "1" ] || exit 0
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v herdr   >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# Hook payload (JSON) arrives on stdin; guard against an interactive TTY run.
if [ -t 0 ]; then input="{}"; else input="$(cat 2>/dev/null || echo '{}')"; fi

# Context-window limit in tokens. Default 1M for Opus 4.8's 1M-context setup;
# set HERDR_CTX_LIMIT=200000 for a standard 200k model.
limit="${HERDR_CTX_LIMIT:-1000000}"

result="$(HERDR_HOOK_INPUT="$input" HERDR_CTX_LIMIT="$limit" python3 - <<'PY'
import json, os, glob

try:
    inp = json.loads(os.environ.get("HERDR_HOOK_INPUT") or "{}")
except Exception:
    inp = {}

sid   = inp.get("session_id") or ""
tpath = inp.get("transcript_path") or ""
cwd   = inp.get("cwd") or ""
limit = int(os.environ.get("HERDR_CTX_LIMIT") or "1000000")

# Last-resort fallback: cwd basename, used only when neither an explicit
# rename nor a live registry match resolves (see the stale-registry note
# in the file header above).
cwd_name = os.path.basename(cwd.rstrip("/")) if cwd else ""

# Fallback name: Claude's per-session registry (auto-derived from cwd).
derived_name = ""
if sid:
    for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("sessionId") == sid:
            derived_name = d.get("name") or ""
            break

# Preferred name + current context size come from one pass over the
# transcript: customTitle entries record every /resume Ctrl+R rename (last
# one wins), and usage on the latest main-line turn (fresh + cache tokens)
# shows how full the window is right now. Subagent (sidechain) turns skipped.
custom_title = ""
used = 0
if tpath and os.path.exists(tpath):
    try:
        with open(tpath, encoding="utf-8") as h:
            for line in h:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "custom-title":
                    custom_title = d.get("customTitle") or custom_title
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

print(custom_title or derived_name or cwd_name)
print(f"{fmt(used)}/{fmt(limit)}" if used else "")
PY
)"

name="$(sed -n '1p' <<<"$result")"
status="$(sed -n '2p' <<<"$result")"

if [ -n "$name" ] && [ "${HERDR_PANE_RENAME_DISABLE:-0}" != "1" ]; then
  herdr pane rename "$HERDR_PANE_ID" "$name" >/dev/null 2>&1 || true
fi

if [ -n "$status" ]; then
  herdr pane report-metadata "$HERDR_PANE_ID" \
    --source user:claude-session-name \
    --agent claude \
    --custom-status "$status" >/dev/null 2>&1 || true
fi

exit 0
