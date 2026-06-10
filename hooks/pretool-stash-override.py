#!/usr/bin/env python3
"""
PreToolUse hook for Task/Agent spawns: parse the spawn prompt for
override comments and stash them in a per-session FIFO (First-In-First-Out)
queue so the corresponding SubagentStart hooks can read them.

The live SubagentStart event from Claude Code does NOT carry the spawn
prompt, so the ``<!-- tools: ... -->`` and ``<!-- inject: ... -->``
override mechanisms would otherwise be unreachable. This hook bridges
the gap. See ``hooks/lib/override_queue.py`` for the queue design and
documented FIFO limitation.

Contract:

* Reads stdin as JSON; non-JSON input → exit 0 silently.
* Only acts when ``tool_name == "Agent"``; other tools → exit 0
  silently.
* Parses both ``<!-- tools: ... -->`` and ``<!-- inject: ... -->``
  comment forms from ``tool_input.prompt``.
* If neither matched, exits 0 without writing — no override → no queue
  entry.
* Otherwise appends a single entry keyed by ``(session_id,
  agent_type)`` and atomically rewrites the per-session file.
* This hook NEVER blocks a tool call — always exits 0.
"""

from __future__ import annotations

import json
import os
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HOOKS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from hooks.lib.override_queue import append_override
from hooks.lib.tool_registry_client import parse_override

# Mirror the regex from hooks/inject-guidelines.py so the parsing rule
# stays in one place semantically even if the source modules differ.
import re

INJECT_RE = re.compile(r"<!--\s*inject:\s*([^>]+?)\s*-->", re.IGNORECASE)


def _parse_inject(prompt: str) -> list[str]:
    if not prompt:
        return []
    match = INJECT_RE.search(prompt)
    if not match:
        return []
    return [s.strip() for s in match.group(1).split(",") if s.strip()]


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") != "Agent":
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    prompt = tool_input.get("prompt") or ""
    agent_type = (tool_input.get("subagent_type") or "default").strip()
    session_id = data.get("session_id") or ""
    tool_use_id = data.get("tool_use_id") or ""

    if not session_id:
        # Without a session key we can't route the queue entry. Soft-fail.
        sys.exit(0)

    tools = parse_override(prompt) or []
    inject = _parse_inject(prompt)

    if not tools and not inject:
        sys.exit(0)

    try:
        append_override(session_id, agent_type, tool_use_id, tools, inject)
    except Exception:
        # Hook is best-effort; never block a tool call.
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
