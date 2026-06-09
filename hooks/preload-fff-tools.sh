#!/usr/bin/env bash
# SessionStart hook: inject a strong reminder to pre-load fff MCP tool schemas.
#
# fff MCP tools are deferred — their schemas aren't in the initial system prompt,
# so calling mcp__fff__* directly fails with InputValidationError until ToolSearch
# loads them. That friction biases the model toward rg/fd/grep/find by default.
#
# This hook can't mechanically load tool schemas (only Claude can, via ToolSearch),
# so it injects the strongest available signal: a system-reminder telling Claude
# to invoke ToolSearch with the canonical fff select query as one of its first
# actions in the session, before any search-class work.

read -r input  # consume stdin even though we don't use it; hooks must drain it

# Skip the reminder if this is a subagent — they have their own guideline injection
# via inject-guidelines.py, and we don't want to crowd their context.
src=$(echo "$input" | jq -r '.source // ""' 2>/dev/null)
if [ "$src" = "subagent" ]; then
  exit 0
fi

context=$(cat <<'EOF'
FFF MCP PRELOAD — IMPORTANT

The fff MCP tools (mcp__fff__grep, mcp__fff__find_files, mcp__fff__multi_grep,
mcp__fff__list_directories, mcp__fff__list_recent_files, mcp__fff__get_git_status,
mcp__fff__record_access) are FIRST-CHOICE for search/listing/git-status in any
git-indexed project, per CLAUDE.md tool hierarchy.

They are deferred tools — schemas are NOT loaded by default. Before your first
search-class action, run this exact ToolSearch call to load all of them at once:

  ToolSearch(query: "select:mcp__fff__grep,mcp__fff__find_files,mcp__fff__multi_grep,mcp__fff__list_directories,mcp__fff__list_recent_files,mcp__fff__get_git_status,mcp__fff__record_access", max_results: 7)

Once loaded, USE them — do not fall back to rg/fd/grep/find/ls/eza/git-status
unless you have a concrete reason fff doesn't fit (e.g. searching outside the
indexed repo, or fff returned an error you've already investigated).

A PreToolUse hook will nudge you if you reach for rg/fd/grep/find without
loading fff first, and a PostToolUse audit hook logs every search-class call to
~/.claude/logs/search-tool-audit.jsonl so the fff-vs-fallback ratio is visible.
EOF
)

jq -n --arg ctx "$context" '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": $ctx}}'
exit 0
