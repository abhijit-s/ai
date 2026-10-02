#!/usr/bin/env bash
# tuios-agent-name.sh — rename each Claude pane to its session name in tuios.
# The tuios port of herdr-agent-name.sh, and deliberately the smaller half of it.
#
# tuios keeps agent identity on the rail, not in the pane title: `tuios
# agent-statusline` already feeds model, context use and cost as agent metadata,
# so the context-window arithmetic the herdr script does by parsing the
# transcript is not repeated here. Only the NAME is missing, because tuios has
# no hook that renames a pane from the harness's session.
#
# A custom name overrides the shell's own title (`set-window --name ""` restores
# that fallback), so naming a pane is also what makes `{title}` useful in
# appearance.window_title_format.
#
# Name preference, same order as the herdr script:
#   1. the session's customTitle — set by /rename or /resume's Ctrl+R
#   2. the auto-derived name in ~/.claude/sessions/<pid>.json
#   3. the cwd basename, as a last resort
# #2 goes stale when you /resume a DIFFERENT never-renamed session in the same
# process (same pid, new session_id), which is why #3 exists.
#
# Wired on UserPromptSubmit so it refreshes every turn.
# Toggle: TUIOS_AGENT_NAME_DISABLE=1 makes it a no-op.
set -uo pipefail

[ "${TUIOS_AGENT_NAME_DISABLE:-0}" = "1" ] && exit 0
[ "${TUIOS_ENV:-}" = "1" ] || exit 0
[ -n "${TUIOS_PANE_ID:-}" ] || exit 0
command -v tuios   >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

if [ -t 0 ]; then input="{}"; else input="$(cat 2>/dev/null || echo '{}')"; fi

name="$(TUIOS_HOOK_INPUT="$input" python3 - <<'PY'
import json, os, glob

try:
    inp = json.loads(os.environ.get("TUIOS_HOOK_INPUT") or "{}")
except Exception:
    inp = {}

sid   = inp.get("session_id") or ""
tpath = inp.get("transcript_path") or ""
cwd   = inp.get("cwd") or ""

cwd_name = os.path.basename(cwd.rstrip("/")) if cwd else ""

derived = ""
if sid:
    for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("sessionId") == sid:
            derived = d.get("name") or ""
            break

custom = ""
if tpath and os.path.exists(tpath):
    try:
        with open(tpath, encoding="utf-8") as h:
            for line in h:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "custom-title":
                    custom = d.get("customTitle") or custom
    except Exception:
        pass

print(custom or derived or cwd_name)
PY
)"

[ -n "$name" ] || exit 0
# -s is required: without it set-window resolves the window in the most
# recently active session, which is not necessarily this pane's.
if [ -n "${TUIOS_SESSION:-}" ]; then
  tuios set-window --name "$name" -s "$TUIOS_SESSION" -w "$TUIOS_PANE_ID" >/dev/null 2>&1 || true
else
  tuios set-window --name "$name" -w "$TUIOS_PANE_ID" >/dev/null 2>&1 || true
fi
exit 0
