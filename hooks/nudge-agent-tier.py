#!/usr/bin/env python3
"""
PreToolUse hook (Agent): agent-tier-selection nudge.

Per CLAUDE.md "Agent Tier Selection": substantive background work
(implementation builds, money-path or high-stakes code review, hard
multi-step reasoning) should dispatch `most-capable-agent` or
`most-capable-and-lean-agent`, not `general-purpose`. `general-purpose` is
appropriate only for cheap mechanical discovery.

This hook cannot judge "substantive" from the prompt alone, so — mirroring
the always-fire style of the tool-hierarchy nudges (ls/rg/fd/grep/find) —
it fires unconditionally whenever an Agent call resolves to
`general-purpose` (explicit or via the tool's own default), reminding the
model to reconsider tier. Always non-blocking: a bug here makes noise,
never blocks a call.
"""

from __future__ import annotations

import json
import sys


def emit(reminder: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": reminder,
        }
    }
    print(json.dumps(payload))
    sys.exit(0)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if (input_data.get("tool_name") or "") != "Agent":
        sys.exit(0)

    tool_input = input_data.get("tool_input") or {}
    subagent_type = (tool_input.get("subagent_type") or "general-purpose").strip()

    if subagent_type != "general-purpose":
        sys.exit(0)

    emit(
        "AGENT TIER NUDGE: dispatching general-purpose. Per CLAUDE.md Agent Tier "
        "Selection, reserve general-purpose for cheap mechanical discovery only. "
        "If this task is substantive — a build, a money-path/high-stakes review, "
        "or hard multi-step reasoning — re-issue with subagent_type: "
        "\"most-capable-agent\" (or \"most-capable-and-lean-agent\") instead. This "
        "applies to nested dispatches too, if this agent itself spawns sub-agents."
    )


if __name__ == "__main__":
    main()
