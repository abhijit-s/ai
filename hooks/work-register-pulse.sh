#!/usr/bin/env bash
# SessionStart nudge — surface the work register only when it has stopped being true.
#
# The register drifts silently: cards sit in a lane while the work behind them finishes
# elsewhere, and a week worked without captures reads as a week idle. Nothing fails, so
# nothing prompts you to look. This fires at the one moment you would act on it — the
# start of a session — and stays quiet otherwise.
#
# Silent by design when healthy. A hook that speaks every session is a hook you learn to
# ignore, which would cost more than the drift it reports.

set -uo pipefail

ENGINE="${HOME}/ai/skills/work-register/scripts/sync_board.py"
[ -f "$ENGINE" ] || exit 0

# Never let a nudge delay or break a session: no config, no python, no network, no matter.
verdict="$(timeout 10 python3 "$ENGINE" --status --brief 2>/dev/null | head -1)" || exit 0
[ -n "$verdict" ] || exit 0

# Only speak when something is actually stale. The healthy verdict starts with ✅.
case "$verdict" in
  *"⚠️"*) ;;
  *) exit 0 ;;
esac

cat <<EOF
$verdict
   Take stock with \`sync_board.py --status\` (detail) or \`--probe\` (resolve each card's
   own pull-request / issue / canon references and propose status changes).
EOF
