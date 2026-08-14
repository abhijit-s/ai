#!/usr/bin/env bash
# SessionStart hook: inject a reminder to pre-load the deferred search-tool schemas.
#
# Both first-choice search families are deferred MCP tools — their schemas aren't in
# the initial system prompt, so calling them fails with InputValidationError until
# ToolSearch loads them. That friction biases the model toward rg/fd/grep/find for
# lexical work, and toward nothing at all for conceptual work: turbo-rag is simply
# never reached for, and a lexical grep over a vault returns "not found" for prose
# that is there under different wording.
#
# This hook can't load tool schemas itself (only Claude can, via ToolSearch), so it
# injects the strongest available signal. Subagents already get this via
# inject-guidelines.py (tool-hierarchy slug) and inject-tool-digest.py; the main
# thread has neither, so this hook is its only structured pointer at the two families.
#
# Corpus roots are read from turbo-rag's own corpus_roots.json rather than by asking
# the engine — a cold engine takes seconds to answer and this hook has a 5s budget.

read -r input  # consume stdin even though we don't use it; hooks must drain it

# Skip for subagents — they have their own guideline + digest injection.
src=$(echo "$input" | jq -r '.source // ""' 2>/dev/null)
if [ "$src" = "subagent" ]; then
  exit 0
fi

profile="${TURBO_RAG_PROFILE:-default}"
roots_file="${XDG_CONFIG_HOME:-$HOME/.config}/turbo-rag/$profile/corpus_roots.json"
roots=""
if [ -r "$roots_file" ]; then
  roots=$(jq -r '.[] | "     @\(.name // "unnamed")  (\(.tier))  \(.path)"' "$roots_file" 2>/dev/null)
fi
[ -n "$roots" ] || roots="     (corpus_roots.json unreadable — call mcp__turbo-rag__health for the live list)"

context=$(cat <<EOF
SEARCH TOOL PRELOAD — IMPORTANT

Both first-choice search families are deferred MCP (Model Context Protocol) tools:
their schemas are NOT loaded by default, so calling one before ToolSearch loads it
fails with InputValidationError. Decide which KIND of search this is, then load that
family BEFORE your first search-class action.

1. LEXICAL — you know the identifier, symbol, literal string, or filename pattern.
   fff MCP heads the ladder inside a git-indexed project:

     ToolSearch(query: "select:mcp__fff__grep,mcp__fff__find_files,mcp__fff__multi_grep,mcp__fff__list_directories,mcp__fff__list_recent_files,mcp__fff__get_git_status,mcp__fff__record_access", max_results: 7)

   Once loaded, USE them — do not fall back to rg/fd/grep/find/ls/eza/git-status
   unless you have a concrete reason fff does not fit (searching outside the indexed
   repo, or an fff error you have already investigated). Always pass base_path: fff
   MCP has ONE default root (the personal vault), so an unqualified search silently
   misses your working tree and reads as "not found".

2. CONCEPTUAL / SEMANTIC — you are after notes or prose ABOUT a topic, where the
   wording may differ from your query. turbo-rag (Retrieval-Augmented Generation
   index) heads this branch, and a lexical search misses these by construction:

     ToolSearch(query: "select:mcp__turbo-rag__hybrid_search,mcp__turbo-rag__semantic_search", max_results: 2)

   hybrid_search is the default (blends vector similarity with lexical signal);
   semantic_search when the query shares no keywords with the target text. Scope
   either with roots="@name" (or a list) — omit it to scope to the cwd's root.

   Indexed corpus roots:
$roots

   Outside these roots turbo-rag returns meta.unregistered_roots — fall back to the
   lexical ladder. If a conceptual search comes back weak or empty, also drop to the
   ladder: vocabulary you do know may match a file directly.

The same server also answers link-graph and note-structure questions (backlinks,
orphans, dangling links, similar notes, outlines, tag clouds, timelines) over the
indexed markdown — load those on demand rather than hand-rolling them from grep.

A PreToolUse hook nudges when you reach for a lower-tier tool, and a PostToolUse
audit hook logs every search-class call to ~/.claude/logs/search-tool-audit.jsonl so
the preferred-vs-fallback ratio stays visible.
EOF
)

jq -n --arg ctx "$context" '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": $ctx}}'
exit 0
