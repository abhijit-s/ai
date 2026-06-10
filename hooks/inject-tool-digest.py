#!/usr/bin/env python3
"""
SubagentStart hook: inject a profile-filtered tool digest into the sub-agent's
additionalContext.

Runs alongside inject-guidelines.py — the two hooks honour independent
`<!-- inject: ... -->` and `<!-- tools: ... -->` prefixes (KTD1, U6).

Resolution order for the allowed tool set:
  1. `<!-- tools: ... -->` override comment in the prompt (U6 semantics)
  2. profiles.json[subagent_type]
  3. profiles.json["default"]

If the manifest cache is missing/stale, the hook exits 0 silently — the
agent still functions, just without the digest (KTD5 safety claim).
"""

from __future__ import annotations

import json
import os
import sys

# Make the hooks/ package importable when this script runs from anywhere.
HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HOOKS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from hooks.lib.override_queue import consume_override
from hooks.lib.tool_registry_client import (
    load_manifest,
    load_profiles,
    parse_override,
    resolve_override,
    resolve_profile,
)


def build_digest(allowed: set[str], manifest: dict, profile_label: str) -> str | None:
    """Group healthy allowed tools by category, ordered by prefer_over."""
    tools_map = manifest.get("tools", {})
    by_cat: dict[str, list[dict]] = {}
    for name in allowed:
        tool = tools_map.get(name)
        if not tool:
            continue
        if tool.get("health", {}).get("state") != "healthy":
            continue
        for cat in tool.get("category", []) or ["uncategorized"]:
            by_cat.setdefault(cat, []).append(tool)

    if not by_cat:
        return None

    # Order tools in each category by prefer_over chain head.
    sections: list[str] = []
    for cat in sorted(by_cat.keys()):
        tools = by_cat[cat]
        # Score = chain length for this category (head of chain ranks first).
        def score(t):
            return len(((t.get("prefer_over") or {}).get(cat)) or [])

        tools.sort(key=score, reverse=True)
        bullets = []
        for t in tools:
            desc = t.get("description") or ""
            line = f"- {t['name']}"
            if desc:
                line += f"  — {desc}"
            bullets.append(line)
        header = f"### {cat}"
        # Add a hint when the category has a meaningful prefer_over chain.
        if any(score(t) > 0 for t in tools):
            header += " (prefer in this order)"
        sections.append(header + "\n" + "\n".join(bullets))

    body = "\n\n".join(sections)
    return f"## Tool Digest (profile: {profile_label})\n\n{body}"


def main():
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # Live Claude Code SubagentStart event puts `agent_type` at the top level
    # (alongside `session_id`, `agent_id`, `hook_event_name`). The nested
    # `tool_input.subagent_type` path is the legacy/test-fixture shape and is
    # kept as a fallback so synthetic test inputs continue to work.
    #
    # The live SubagentStart event does NOT carry the spawn prompt, so
    # `<!-- tools: ... -->` overrides are read from the per-session
    # override queue populated by `pretool-stash-override.py`. The legacy
    # prompt-based path stays in place for test inputs that pass the
    # prompt directly via `tool_input.prompt`.
    tool_input = input_data.get("tool_input") or {}
    agent_type = (
        input_data.get("agent_type")
        or tool_input.get("subagent_type")
        or "default"
    ).strip()
    prompt = input_data.get("prompt") or tool_input.get("prompt") or ""
    session_id = input_data.get("session_id") or ""

    manifest = load_manifest()
    if not manifest.get("tools"):
        # Cache missing or empty — soft-fail; downstream tools still work.
        sys.exit(0)

    profiles_doc = load_profiles()

    # Resolve allowed set. Queue takes precedence over prompt-based parsing
    # because the live event has no prompt at all.
    override: list[str] | None = None
    if session_id:
        try:
            queued = consume_override(session_id, agent_type, "tools")
        except Exception:
            queued = None
        if queued:
            override = queued
    if override is None:
        override = parse_override(prompt)

    if override is not None:
        allowed = resolve_override(override, profiles_doc, manifest)
        profile_label = "override: " + ", ".join(override)
    else:
        # Try the agent's profile first; fall back to default if unknown.
        profile_name = (
            agent_type if agent_type in profiles_doc.get("profiles", {}) else "default"
        )
        allowed = resolve_profile(profile_name, profiles_doc, manifest)
        profile_label = profile_name

    digest = build_digest(allowed, manifest, profile_label)
    if not digest:
        sys.exit(0)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": digest,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
