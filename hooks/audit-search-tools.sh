#!/usr/bin/env bash
# PostToolUse hook: log every search-class tool call to a JSONL audit log.
#
# Logged events:
#   - Bash commands invoking: rg, fd, grep, find, ls, eza, git status
#   - Any mcp__fff__* tool call
#
# Log file: ~/.claude/logs/search-tool-audit.jsonl
# Each line: {"ts": "<iso8601>", "tool": "<tool_name>", "tier": "<fff|hi|lo>",
#             "kind": "<rg|fd|grep|find|ls|eza|git-status|fff-grep|...>",
#             "session": "<session_id>", "cwd": "<cwd>"}
#
# Use ~/.claude/hooks/analyze-search-audit.sh to summarize the ratio.

input=$(cat)

log_dir="$HOME/.claude/logs"
log_file="$log_dir/search-tool-audit.jsonl"
mkdir -p "$log_dir"

tool_name=$(echo "$input" | jq -r '.tool_name // ""')
session=$(echo "$input" | jq -r '.session_id // ""')
cwd=$(echo "$input" | jq -r '.cwd // ""')
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

kind=""
tier=""

case "$tool_name" in
  Bash)
    cmd=$(echo "$input" | jq -r '.tool_input.command // ""')
    # Detect first matching search verb. Order matters: check 'git status' before bare 'git'.
    # Boundary class includes `(` so subshells like `$(grep foo)` count — must
    # stay in lockstep with BASH_VERB_PATTERNS in hooks/enforce-tool-registry.py
    # (the audit log feeds the AE2 block-mode graduation contract).
    if echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)git[[:space:]]+status'; then
      kind="git-status"; tier="hi"
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)rg[[:space:]]'; then
      kind="rg"; tier="hi"
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)fd[[:space:]]'; then
      kind="fd"; tier="hi"
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)grep[[:space:]]'; then
      kind="grep"; tier="lo"
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)find[[:space:]]'; then
      kind="find"; tier="lo"
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)ls([[:space:]]|$)'; then
      kind="ls"; tier="hi"
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)eza([[:space:]]|$)'; then
      kind="eza"; tier="hi"
    fi
    ;;
  mcp__fff__*)
    kind="${tool_name#mcp__fff__}"
    tier="fff"
    ;;
esac

if [ -n "$kind" ]; then
  jq -nc \
    --arg ts "$ts" \
    --arg tool "$tool_name" \
    --arg tier "$tier" \
    --arg kind "$kind" \
    --arg session "$session" \
    --arg cwd "$cwd" \
    '{ts: $ts, tool: $tool, tier: $tier, kind: $kind, session: $session, cwd: $cwd}' \
    >> "$log_file"
fi

exit 0
