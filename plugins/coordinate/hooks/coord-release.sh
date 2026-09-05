#!/usr/bin/env bash
# SessionEnd — deterministically close the exited-holder gap.
#
# The COORD protocol has two silent failure modes. The claim-and-ACK handshake
# handles a peer moving the resource under you; the lease TTL handles a holder
# that crashes or hangs. But the TTL leaves a window: after a session exits
# cleanly, its holds sit "held" until they lapse — up to 10 minutes of peers
# needlessly waiting on a resource nobody is using. This hook shuts that window:
# on session end it RELEASEs every lease this session holds, immediately, so a
# waiter is promoted at once instead of at lease expiry.
#
# It complements two other mechanisms, never replaces them. A hard kill
# (SIGKILL, OOM, power loss) fires NO hook — that gap is closed by LIVENESS
# REAPING: a claim records the session's messaging-socket path as a token, and
# query/claim/status/sweep connect-probe it, reaping a holder whose session is
# gone long before its TTL lapses. The lease TTL is the final backstop beneath
# both. So: this hook = clean exit; liveness reaping = unclean exit; TTL =
# everything else. Belt, braces, and a second belt.
#
# Silent and non-fatal by design: a coordination cleanup must never delay or
# break session teardown. No coord, no python, no ledger — it exits 0 quietly.

set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
COORD="${COORD_BIN:-${PLUGIN_ROOT}/scripts/coord.py}"

[ -f "$COORD" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# SessionEnd delivers a JSON payload on stdin carrying the session_id. That id
# is how holds are tagged, so releasing by it targets exactly this session's
# leases. If jq is missing or the payload lacks the field, fall back to the
# environment ($CLAUDE_SESSION_ID), then to a whole-ledger sweep — which frees
# only genuinely-lapsed holds, so it is always safe even without a session id.
payload=""
[ -t 0 ] || payload="$(cat)"

session=""
if [ -n "$payload" ] && command -v jq >/dev/null 2>&1; then
  session="$(printf '%s' "$payload" | jq -r '.session_id // ""' 2>/dev/null)"
fi
[ -n "$session" ] || session="${CLAUDE_SESSION_ID:-}"

if [ -n "$session" ]; then
  CLAUDE_SESSION_ID="$session" timeout 8 python3 "$COORD" release --all --session "$session" \
    >/dev/null 2>&1 || true
fi

# Always sweep lapsed holds too — cheap, and it promotes any queue whose holder
# expired while this session ran but no one has claimed since.
timeout 8 python3 "$COORD" sweep >/dev/null 2>&1 || true

exit 0
