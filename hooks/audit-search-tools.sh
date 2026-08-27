#!/usr/bin/env bash
# PostToolUse hook: log every search-class tool call to a JSONL audit log.
#
# Logged events:
#   - Bash commands invoking: rg, fd, grep, find, ls, eza, ast-grep, git status
#   - Any mcp__fff__* tool call
#
# One command, one record: the FIRST verb in branch order wins, matching
# detect_bash_verb() in hooks/enforce-tool-registry.py. A command that mixes
# verbs (`ast-grep -p ... | rg -i foo`) is therefore attributed to the
# higher-precedence one — the record proves the command was search-class, not
# which tool did the reading.
#
# Log file: ~/.claude/logs/search-tool-audit.jsonl
# Each line: {"ts": "<iso8601>", "tool": "<tool_name>", "tier": "<fff|hi|lo>",
#             "kind": "<rg|fd|grep|find|ls|eza|ast-grep|git-status|fff-grep|...>",
#             "session": "<session_id>", "cwd": "<cwd>"}
#
# Use ~/.claude/hooks/analyze-search-audit.sh to summarize the ratio.

input=$(cat)

# Heredoc bodies are data, not commands: an `rg` inside a script being written
# via `cat <<'EOF' ... EOF` is text, not a search call. Mirrors strip_heredocs()
# in hooks/lib/tool_registry_client.py — the two verb surfaces must agree on
# which calls count. The introducer token goes with the body, so this is
# idempotent.
strip_heredocs() {
  local line delim="" skipping=0 token
  while IFS= read -r line; do
    if [ "$skipping" -eq 1 ]; then
      if [[ "$line" =~ ^[[:space:]]*${delim}[[:space:]]*$ ]]; then
        skipping=0
      fi
      continue
    fi
    if [[ "$line" =~ \<\<-?[[:space:]]*[\'\"]?([A-Za-z_][A-Za-z0-9_]*)[\'\"]? ]]; then
      token="${BASH_REMATCH[0]}"
      delim="${BASH_REMATCH[1]}"
      skipping=1
      printf '%s\n' "${line/"$token"/}"
    else
      printf '%s\n' "$line"
    fi
  done
}

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
    cmd=$(echo "$input" | jq -r '.tool_input.command // ""' | strip_heredocs)
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
    elif echo "$cmd" | grep -qE '(^|[|;&([:space:]]+)ast-grep([[:space:]]|$)'; then
      # Last, mirroring BASH_VERB_PATTERNS. The bare-`grep` branch above cannot
      # claim it: `-` is not in the boundary class, so `ast-grep` never matches
      # `(^|[|;&([:space:]]+)grep`.
      kind="ast-grep"; tier="hi"
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
