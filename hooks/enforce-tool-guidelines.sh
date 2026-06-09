#!/usr/bin/env bash
# Warn when search commands are used in Bash without first reaching for fff MCP.
#
# Two tiers:
#   HARD (grep / find)          — these are LAST RESORT per CLAUDE.md tool hierarchy.
#   SOFT (rg / fd / ls / eza /  — these are higher-tier than grep/find but still
#         git status)             below fff MCP for indexed-repo work. A nudge fires
#                                 so Claude considers fff first.
#
# The hook always allows the command (permissionDecision: allow); it only adds
# additionalContext so the model sees a reminder.

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""')

reminder=""

# ---- HARD tier: last-resort tools ----
if echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)find[[:space:]]'; then
  reminder="TOOL GUIDELINE REMINDER (HARD): You used find, which is a last resort. Per CLAUDE.md tool hierarchy: prefer fff MCP first (mcp__fff__find_files), then fd. Only use find if fd is truly unavailable.\n  Wrong: find . -name \"*.rb\"\n  Right:  mcp__fff__find_files  OR  fd -e rb ."
elif echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)grep[[:space:]]'; then
  reminder="TOOL GUIDELINE REMINDER (HARD): You used grep, which is a last resort. Per CLAUDE.md tool hierarchy: prefer fff MCP first (mcp__fff__grep), then rg. Only use grep if rg is truly unavailable.\n  Wrong: grep -r pattern .\n  Right:  mcp__fff__grep  OR  rg pattern  (use --type ruby not --type rb)"

# ---- SOFT tier: prefer fff for indexed-repo work ----
elif echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)rg[[:space:]]'; then
  reminder="TOOL GUIDELINE NUDGE (SOFT): You used rg. Inside a git-indexed project, mcp__fff__grep is the higher-tier choice (frecency-ranked, .gitignore-aware, faster). rg is fine for non-repo scratch dirs or when fff isn't loaded — but if you're searching this repo's code, load fff via ToolSearch (select:mcp__fff__grep,mcp__fff__find_files,mcp__fff__multi_grep) and use it instead."
elif echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)fd[[:space:]]'; then
  reminder="TOOL GUIDELINE NUDGE (SOFT): You used fd. Inside a git-indexed project, mcp__fff__find_files is the higher-tier choice (frecency-ranked). fd is fine for non-repo dirs or when fff isn't loaded — but if you're discovering files in this repo, load fff via ToolSearch (select:mcp__fff__find_files) and use it instead."
elif echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)git[[:space:]]+status'; then
  reminder="TOOL GUIDELINE NUDGE (SOFT): You used 'git status'. Prefer mcp__fff__get_git_status — same info, frecency-enriched, grouped by status, and avoids a Bash permission prompt. Load via ToolSearch (select:mcp__fff__get_git_status)."
elif echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)(ls|eza)[[:space:]]'; then
  reminder="TOOL GUIDELINE NUDGE (SOFT): You used ls/eza. For orienting in a project, prefer mcp__fff__list_directories (frecency-ranked) or mcp__fff__list_recent_files. Load via ToolSearch (select:mcp__fff__list_directories,mcp__fff__list_recent_files). Plain ls/eza is fine outside repos or for one-off checks."
fi

if [ -n "$reminder" ]; then
  jq -n --arg ctx "$reminder" '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "additionalContext": $ctx}}'
  exit 0
fi

exit 0
