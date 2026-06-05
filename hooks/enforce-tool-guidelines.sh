#!/usr/bin/env bash
# Warn when find/grep are used; they are last-resort tools per CLAUDE.md

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""')

reminder=""

if echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)find[[:space:]]'; then
  reminder="TOOL GUIDELINE REMINDER: You used find, which is a last resort. Per CLAUDE.md tool hierarchy: prefer fff MCP first, then fd for file discovery. Only use find if fd is truly unavailable.\n  Wrong: find . -name \"*.rb\"\n  Right:  fd -e rb ."
fi

if echo "$cmd" | grep -qE '(^|[|;&[:space:]]+)grep[[:space:]]'; then
  reminder="TOOL GUIDELINE REMINDER: You used grep, which is a last resort. Per CLAUDE.md tool hierarchy: prefer fff MCP first, then rg (ripgrep) for text search. Only use grep if rg is truly unavailable.\n  Wrong: grep -r pattern .\n  Right:  rg pattern  (use --type ruby not --type rb for language names)"
fi

if [ -n "$reminder" ]; then
  jq -n --arg ctx "$reminder" '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "additionalContext": $ctx}}'
  exit 0
fi

exit 0
