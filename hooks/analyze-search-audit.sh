#!/usr/bin/env bash
# Summarize ~/.claude/logs/search-tool-audit.jsonl.
#
# Usage:
#   analyze-search-audit.sh                  # all-time summary
#   analyze-search-audit.sh --since 7d       # last 7 days
#   analyze-search-audit.sh --session <id>   # single session
#   analyze-search-audit.sh --raw            # last 20 raw entries
#
# Reports counts by tier (fff / hi / lo), per-tool breakdown, and the
# fff-vs-fallback ratio — the headline metric for whether the tool hierarchy
# is being followed.

set -euo pipefail

log_file="$HOME/.claude/logs/search-tool-audit.jsonl"

if [ ! -f "$log_file" ]; then
  echo "No audit log yet at $log_file"
  echo "It will be populated as you make search-class tool calls."
  exit 0
fi

mode="summary"
since=""
session=""

while [ $# -gt 0 ]; do
  case "$1" in
    --since) since="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    --raw) mode="raw"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# Build filter expression
filter='.'
if [ -n "$session" ]; then
  filter="$filter | select(.session == \"$session\")"
fi
if [ -n "$since" ]; then
  case "$since" in
    *d) days="${since%d}"; cutoff=$(date -u -v-"${days}"d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "${days} days ago" +%Y-%m-%dT%H:%M:%SZ) ;;
    *h) hours="${since%h}"; cutoff=$(date -u -v-"${hours}"H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "${hours} hours ago" +%Y-%m-%dT%H:%M:%SZ) ;;
    *) echo "Unsupported --since format: $since (use Nd or Nh)" >&2; exit 1 ;;
  esac
  filter="$filter | select(.ts >= \"$cutoff\")"
fi

if [ "$mode" = "raw" ]; then
  jq -c "$filter" "$log_file" | tail -20
  exit 0
fi

# Summary mode
total=$(jq -c "$filter" "$log_file" | wc -l | tr -d ' ')
if [ "$total" = "0" ]; then
  echo "No matching entries."
  exit 0
fi

echo "Search-tool audit summary"
[ -n "$since" ]   && echo "  Window:  last $since"
[ -n "$session" ] && echo "  Session: $session"
echo "  Total:   $total calls"
echo

echo "By tier:"
jq -c "$filter" "$log_file" \
  | jq -r '.tier' \
  | sort | uniq -c | sort -rn \
  | awk '{ printf "  %-6s %s\n", $2, $1 }'

echo
echo "By tool:"
jq -c "$filter" "$log_file" \
  | jq -r '.kind' \
  | sort | uniq -c | sort -rn \
  | awk '{ printf "  %-15s %s\n", $2, $1 }'

echo
fff_count=$(jq -c "$filter" "$log_file" | jq -r 'select(.tier == "fff") | .kind' | wc -l | tr -d ' ')
fallback_count=$(jq -c "$filter" "$log_file" | jq -r 'select(.tier != "fff") | .kind' | wc -l | tr -d ' ')

echo "Headline ratio:"
echo "  fff:       $fff_count"
echo "  fallback:  $fallback_count"
if [ "$fallback_count" != "0" ]; then
  ratio=$(awk "BEGIN { printf \"%.2f\", $fff_count / $fallback_count }")
  echo "  fff/fallback: $ratio  (higher = better adherence to tool hierarchy)"
elif [ "$fff_count" != "0" ]; then
  echo "  fff/fallback: ∞  (no fallback calls — perfect adherence)"
fi
