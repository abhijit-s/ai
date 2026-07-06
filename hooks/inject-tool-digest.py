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
import tomllib

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


def _fff_config_path() -> str | None:
    """Locate the fff-mcp roots config (config-not-fork: roots live here)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    for name in ("config.toml", "mcp.toml"):
        path = os.path.join(base, "fff", name)
        if os.path.isfile(path):
            return path
    return None


def fff_root_hint(cwd: str) -> str | None:
    """Map the session cwd to its most-specific fff root.

    fff-mcp is a shared singleton with ONE global default root, so an agent
    that omits `base_path` searches that default — the personal vault — not
    its own working tree. This resolves the correct root from cwd and tells
    the agent exactly what `base_path` to pass. Returns None when the default
    already matches (nothing to warn about) or config/cwd are unavailable.
    """
    if not cwd:
        return None
    config_path = _fff_config_path()
    if not config_path:
        return None
    try:
        with open(config_path, "rb") as fh:
            cfg = tomllib.load(fh)
    except Exception:
        return None

    mcp = cfg.get("mcp") or {}
    default_name = mcp.get("default")
    roots = mcp.get("roots") or []
    if not roots:
        return None

    try:
        real_cwd = os.path.realpath(cwd)
    except Exception:
        real_cwd = cwd

    # Longest matching root path wins (most specific — a nested corpus repo
    # beats the umbrella root that also contains it).
    match = None
    for root in roots:
        path = root.get("path")
        name = root.get("name")
        if not path or not name:
            continue
        real_path = os.path.realpath(os.path.expanduser(path))
        if real_cwd == real_path or real_cwd.startswith(real_path + os.sep):
            if match is None or len(real_path) > len(match[1]):
                match = (name, real_path)

    if match is None:
        # cwd is outside every indexed root — fff would silently serve the
        # default (personal vault). Steer to the lexical ladder instead.
        return (
            "⚠️ fff MCP has no root covering this working tree "
            f"(`{real_cwd}`). An unqualified fff search would hit the default "
            f"root (`{default_name}`, the personal vault), not your files — "
            "either pass an explicit `base_path`, or drop to the lexical "
            "ladder (ast-grep → rg → fd)."
        )

    matched_name, matched_path = match
    if matched_name == default_name:
        return None  # default already matches — omitting base_path is correct.

    worktree_note = ""
    if f"{os.sep}.claude{os.sep}worktrees{os.sep}" in real_cwd + os.sep:
        worktree_note = (
            f" (you're in a worktree — pass `base_path={real_cwd}` to search "
            "the worktree copy directly)"
        )

    return (
        f"⚠️ fff MCP default root is `{default_name}` (the personal vault) — "
        "it will NOT match your working tree. This session's cwd maps to root "
        f"`{matched_name}`. Pass `base_path={matched_path}` on every "
        f"`mcp__fff__*` call{worktree_note}."
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
    cwd = input_data.get("cwd") or os.getcwd()

    # The fff root hint is independent of the tool-registry manifest — it must
    # still fire when the manifest cache is missing/stale.
    hint = fff_root_hint(cwd)

    manifest = load_manifest()
    if not manifest.get("tools"):
        # Cache missing or empty — emit the root hint alone if we have one.
        if hint:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "SubagentStart",
                            "additionalContext": hint,
                        }
                    }
                )
            )
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
    context = "\n\n".join(part for part in (hint, digest) if part)
    if not context:
        sys.exit(0)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": context,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
