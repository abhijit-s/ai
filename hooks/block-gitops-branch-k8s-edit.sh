#!/usr/bin/env bash
# PreToolUse guard (Edit|Write|MultiEdit): block hand-edits to services/*/k8s/**
# on the chore/gitops-overlays branch.
#
# That branch is CI-composed from main (scripts/compose-gitops-overlays.sh: read-tree
# main + drift->adopt-main), so a hand-edit there is SILENTLY discarded on the next
# surge-ci-bot promotion. Arm in main SOURCE instead. See the memory note
# reference-gitops-overlays-composed-from-main. Exit 2 blocks and shows the reason.
set -euo pipefail
input="$(cat)"
fp="$(printf '%s' "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null || true)"
[ -z "$fp" ] && exit 0

# Only service k8s manifests are at risk.
case "$fp" in
  */services/*/k8s/*) ;;
  *) exit 0 ;;
esac

dir="$(dirname "$fp")"
[ -d "$dir" ] || dir="$(dirname "$dir")"
branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"

if [ "$branch" = "chore/gitops-overlays" ]; then
  cat >&2 <<EOF
BLOCKED: $fp is on the chore/gitops-overlays branch, which CI composes from main
(scripts/compose-gitops-overlays.sh). A hand-edit here is SILENTLY discarded on the
next surge-ci-bot "stamp" promotion — this is the recurring arm-revert trap.

Arm in main SOURCE instead: edit the dev overlay (+ base entry) on main and MERGE;
the stamp job then carries it into the gitops branch durably. Then rollout-restart any
pod that predates the arm (envFrom config/secret needs a restart; no reloader).

See memory: reference-gitops-overlays-composed-from-main.
EOF
  exit 2
fi
exit 0
