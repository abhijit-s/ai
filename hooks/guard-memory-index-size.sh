#!/usr/bin/env bash
# PostToolUse(Write|Edit|MultiEdit) guard — warn when the native auto-memory
# index (MEMORY.md) approaches the recall size limit, so it is trimmed BEFORE
# the SessionStart loader silently truncates it.
#
# Why: Memory/auto/MEMORY.md is the hand-curated native auto-memory index. It is
# NOT governed by memory-kit — the umbrella's native dir is intentionally
# excluded from harvest/compaction (~/.config/memory-kit/config.toml), so nothing
# else watches its size. Entries drift over-length when session status is
# appended inline instead of pushed into the per-topic project_*.md file. Above
# the limit the loader truncates the index -> silent recall loss.
#
# Behavior: on a Write/Edit whose target is the auto-memory MEMORY.md, stat the
# file. At/above SOFT_LIMIT -> emit a trim nudge to stderr and exit 2 (surfaces
# to Claude; the edit already succeeded, so nothing is blocked). Fails open on
# any parse error so it can never wedge the editing tools.
#
# Config (env overrides): CLAUDE_AUTO_MEMORY_DIR, MEMORY_INDEX_SOFT_LIMIT_BYTES,
# MEMORY_INDEX_HARD_LIMIT_BYTES.

input=$(cat 2>/dev/null) || exit 0

fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.filePath // empty' 2>/dev/null)
[ -z "$fp" ] && exit 0

mem_dir="${CLAUDE_AUTO_MEMORY_DIR:-$HOME/vaults/workspace/Memory/auto}"
index="$mem_dir/MEMORY.md"

# Resolve both sides so relative paths / symlinks compare reliably.
resolved=$(realpath -- "$fp" 2>/dev/null) || resolved="$fp"
target=$(realpath -- "$index" 2>/dev/null) || target="$index"
[ "$resolved" = "$target" ] || exit 0

# 24.4 KiB hard limit (loader truncates above this); warn in a band below it.
soft=${MEMORY_INDEX_SOFT_LIMIT_BYTES:-22000}
hard=${MEMORY_INDEX_HARD_LIMIT_BYTES:-24985}

size=$(wc -c < "$index" 2>/dev/null | tr -d '[:space:]') || exit 0
[ -n "$size" ] || exit 0
[ "$size" -lt "$soft" ] && exit 0

band="approaching"
[ "$size" -ge "$hard" ] && band="OVER"

# Distinguish the pressure so the nudge names the right fix:
#   BLOAT — some entries have grown into paragraphs. A healthy hook targets ~200
#           chars but dense one-line facts here run ~360; genuine paragraph-bloat
#           (the original failure mode) was 700-2900. Flag lines above that band.
#   COUNT — entries are already reasonably sized, there are just too many.
long_max=${MEMORY_INDEX_LONG_LINE_BYTES:-700}
long=$(awk -v m="$long_max" 'BEGIN{n=0} /^- / && length($0) > m {n++} END{print n+0}' "$index" 2>/dev/null)
[ -n "$long" ] || long=0

if [ "$long" -gt 0 ]; then
  plural=ies; [ "$long" -eq 1 ] && plural=y
  advice=$(printf 'BLOAT: %s entr%s exceed ~%s chars. Collapse them to one-line hooks (~200 chars, "- [Title](file.md) — hook") and push status detail into the per-topic project_*.md file (the source of truth).' \
    "$long" "$plural" "$long_max")
else
  advice='COUNT: entries are already short but there are too many. Retire ✅-DONE / superseded tracks (no active "NEXT") to Memory/auto/ARCHIVE.md — the topic files stay in place and stay searchable via memory-kit, they just leave the hot recall index.'
fi

printf 'MEMORY.md is %s bytes — %s the ~%s-byte (24.4KB) recall limit; the SessionStart loader TRUNCATES above it (silent recall loss). %s See the Memory guidance in CLAUDE.md.\n' \
  "$size" "$band" "$hard" "$advice" >&2
exit 2
