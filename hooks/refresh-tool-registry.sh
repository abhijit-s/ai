#!/usr/bin/env bash
# SessionStart hook: rebuild the tool-registry manifest cache.
#
# Spawns the tool-registry MCP server with --refresh-and-exit, which runs
# discovery, atomically writes ~/.claude/cache/tool-registry-manifest.json,
# and exits 0. Runs AFTER preload-search-tools.sh in claude-settings.json so the
# preload reminder lands first.
#
# On timeout or failure, exits 0 silently — the PreToolUse + SubagentStart
# hooks fall back to embedded behaviour (KTD5).

set -u

# Discard stdin (SessionStart payload); we don't use it.
cat >/dev/null 2>&1 || true

REPO_ROOT="${TOOL_REGISTRY_ROOT:-$HOME/.dotfiles/ai}"
SERVER="$REPO_ROOT/mcp-servers/tool-registry/index.js"
CACHE_DIR="$HOME/.claude/cache"

if [ ! -f "$SERVER" ]; then
  exit 0
fi

mkdir -p "$CACHE_DIR" 2>/dev/null || exit 0

# 15s budget — matches the global discovery deadline in src/discovery.js.
# Run the refresh in the background, then wait with a timeout so the
# SessionStart hook never blocks for more than the budget.
if command -v timeout >/dev/null 2>&1; then
  timeout 15s node "$SERVER" "$REPO_ROOT" --refresh-and-exit >/dev/null 2>&1 || true
elif command -v gtimeout >/dev/null 2>&1; then
  gtimeout 15s node "$SERVER" "$REPO_ROOT" --refresh-and-exit >/dev/null 2>&1 || true
else
  # No timeout binary available — best-effort run. The server's own internal
  # 15s deadline (src/discovery.js GLOBAL_DEADLINE_MS) prevents runaway.
  node "$SERVER" "$REPO_ROOT" --refresh-and-exit >/dev/null 2>&1 || true
fi

exit 0
