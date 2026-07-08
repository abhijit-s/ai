#!/usr/bin/env python3
"""
PreToolUse hook: registry-driven tool-selection nudge.

For every Bash and `mcp__*` call:
  1. Resolve the manifest tool name from the call (Bash → verb regex,
     MCP → tool_name directly).
  2. Resolve the active profile (subagent → that profile; else session-default).
  3. If the call is out-of-profile or known-unhealthy, emit an
     additionalContext nudge naming the registry-derived alternative.

v1 always returns permissionDecision: allow (KTD5 safety claim). A bug
produces noise, never blocks. block_unhealthy is forward-compat only.

On registry-unhealthy (manifest missing/unparseable), fall back to a
small embedded copy of the current hard-coded chain so behavior never
regresses below today's bash-script baseline.
"""

from __future__ import annotations

import json
import os
import re
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HOOKS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from hooks.lib.tool_registry_client import (
    load_manifest,
    load_profiles,
    resolve_profile,
)

# Matches the audit-search-tools.sh verb inventory so audit and nudge agree.
# Order: check `git status` before bare `git`.
BASH_VERB_PATTERNS = [
    ("git-status", re.compile(r"(^|[|;&(\s]+)git\s+status(\s|$)")),
    ("rg", re.compile(r"(^|[|;&(\s]+)rg(\s|$)")),
    ("fd", re.compile(r"(^|[|;&(\s]+)fd(\s|$)")),
    ("grep", re.compile(r"(^|[|;&(\s]+)grep(\s|$)")),
    ("find", re.compile(r"(^|[|;&(\s]+)find(\s|$)")),
    ("ls", re.compile(r"(^|[|;&(\s]+)ls(\s|$)")),
    ("eza", re.compile(r"(^|[|;&(\s]+)eza(\s|$)")),
    ("ast-grep", re.compile(r"(^|[|;&(\s]+)ast-grep(\s|$)")),
]

# Single pipe `|`, not part of `||` (logical-or). Splits a shell command into
# pipeline segments so we can tell a stdin-consuming grep from a file search.
PIPE_SPLIT = re.compile(r"(?<!\|)\|(?!\|)")

# grep/find are the documented LAST-RESORT tier. Used as a file search (not a
# stream filter) they are DENIED — a real block, since auto mode auto-approves
# `ask` for safe reads. The genuine escape hatch (outside indexed roots, find
# -exec) stays open via the FFF_ESCAPE marker appended to the command.
FFF_ESCAPE = "fff-ok"
GREP_FIND_DENY = {
    "grep": (
        "Shell grep is the LAST-RESORT tier for file search. Re-issue with "
        "mcp__fff__grep (indexed roots) or rg — rg searches ANY path, including "
        "outside indexed roots, so it covers virtually every case. Only for a real "
        f"grep-specific need append ' # {FFF_ESCAPE}' to force this call through."
    ),
    "find": (
        "Shell find is the LAST-RESORT tier for file discovery. Re-issue with "
        "mcp__fff__find_files (indexed roots) or fd — fd searches ANY path. Only for "
        f"a real find-specific need (-exec, complex predicates) append ' # {FFF_ESCAPE}'."
    ),
}

# Embedded fallback when the registry is unavailable. Intentionally minimal
# (per KTD5) — the canonical chains live in the registry.
EMBEDDED_FALLBACK = {
    "grep": "Per CLAUDE.md tool hierarchy: prefer mcp__fff__grep (frecency-ranked) first, then ast-grep, then rg. grep is a last resort.",
    "find": "Per CLAUDE.md tool hierarchy: prefer mcp__fff__find_files (frecency-ranked) first, then fd. find is a last resort.",
    "rg": "Inside a git-indexed project, mcp__fff__grep is the higher-tier choice (frecency-ranked, indexed). Load via ToolSearch (select:mcp__fff__grep,mcp__fff__find_files,mcp__fff__multi_grep).",
    "fd": "Inside a git-indexed project, mcp__fff__find_files is the higher-tier choice (frecency-ranked). Load via ToolSearch (select:mcp__fff__find_files).",
    "git-status": "Prefer mcp__fff__get_git_status — frecency-enriched, grouped by status. Load via ToolSearch (select:mcp__fff__get_git_status).",
    "ls": "For orienting in a project, prefer mcp__fff__list_directories or mcp__fff__list_recent_files. Load via ToolSearch (select:mcp__fff__list_directories,mcp__fff__list_recent_files).",
    "eza": "For orienting in a project, prefer mcp__fff__list_directories or mcp__fff__list_recent_files. Load via ToolSearch (select:mcp__fff__list_directories,mcp__fff__list_recent_files).",
}


def detect_bash_verb(command: str) -> str | None:
    """Return the first manifest-known verb in the command, or None."""
    if not command:
        return None
    for verb, pat in BASH_VERB_PATTERNS:
        if pat.search(command):
            return verb
    return None


def is_stream_filter(command: str, verb: str) -> bool:
    """True if grep/find consumes piped stdin (a legit filter fff/rg can't replace).

    `ps aux | grep foo` and `git log | grep fix` filter another command's
    output — untouchable. A recursive grep (`-r`/`-R`) is always a file search.
    A verb that heads the pipeline (first segment) is a file search; one that
    appears only downstream of a single `|` is a stream filter.
    """
    verb_pat = dict(BASH_VERB_PATTERNS)[verb]
    if verb == "grep" and re.search(r"(^|\s)-[a-zA-Z]*[rR]\b|--recursive\b", command):
        return False
    segments = PIPE_SPLIT.split(command)
    if len(segments) <= 1:
        return False  # no pipe → cannot be a downstream filter
    if verb_pat.search(segments[0]):
        return False  # heads the pipeline → file search
    return any(verb_pat.search(seg) for seg in segments[1:])


def resolve_active_profile_name(hook_input: dict, profiles_doc: dict) -> str:
    tool_input = hook_input.get("tool_input") or {}
    subagent = tool_input.get("subagent_type")
    profiles = profiles_doc.get("profiles", {})
    if subagent and subagent in profiles:
        return subagent
    if "session-default" in profiles:
        return "session-default"
    return "default"


def emit(reminder: str | None, decision: str = "allow"):
    if reminder is None:
        sys.exit(0)
    hook_output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "additionalContext": reminder,
    }
    # deny/ask surface their rationale to the user via permissionDecisionReason.
    if decision != "allow":
        hook_output["permissionDecisionReason"] = reminder
    print(json.dumps({"hookSpecificOutput": hook_output}))
    sys.exit(0)


def pick_alternatives(tool: dict, category: str, allowed: set[str], manifest: dict) -> list[str]:
    """Return the in-profile, healthy alternatives that outrank `tool` for category."""
    # The head of the chain is the tool whose prefer_over[category] LISTS the
    # other tools. We want the names that prefer over `tool` (i.e., where
    # `tool.name` appears in their prefer_over chain for `category`).
    tools_map = manifest.get("tools", {})
    alternatives: list[str] = []
    name = tool.get("name")
    for other_name, other in tools_map.items():
        if other_name == name:
            continue
        if other_name not in allowed:
            continue
        if other.get("health", {}).get("state") != "healthy":
            continue
        chain = (other.get("prefer_over") or {}).get(category, []) or []
        if name in chain:
            alternatives.append(other_name)
    # Stable order: by chain length (longer chain = closer to head).
    alternatives.sort(
        key=lambda n: len((tools_map[n].get("prefer_over") or {}).get(category, []) or []),
        reverse=True,
    )
    return alternatives


def main():
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = input_data.get("tool_name") or ""
    tool_input = input_data.get("tool_input") or {}
    command = tool_input.get("command") or ""

    # Decide which manifest tool name to look up.
    manifest_name: str | None = None
    fallback_key: str | None = None
    if tool_name == "Bash":
        verb = detect_bash_verb(command)
        if not verb:
            sys.exit(0)
        # Deny the substitutable form of the last-resort tools. A piped
        # grep/find is a stream filter fff/rg can't replace — leave it untouched.
        # An explicit FFF_ESCAPE marker forces a genuine last-resort call through.
        if verb in GREP_FIND_DENY:
            if is_stream_filter(command, verb) or FFF_ESCAPE in command:
                sys.exit(0)
            emit(f"TOOL GUIDELINE (last-resort): {GREP_FIND_DENY[verb]}", decision="deny")
        manifest_name = verb
        fallback_key = verb
    elif tool_name.startswith("mcp__"):
        manifest_name = tool_name
    else:
        # Other tool kinds (Read/Edit/Write/etc.) are not gated here.
        sys.exit(0)

    manifest = load_manifest()
    profiles_doc = load_profiles()

    # Registry-unhealthy fallback (KTD5).
    if not manifest.get("tools"):
        if fallback_key and fallback_key in EMBEDDED_FALLBACK:
            emit(f"TOOL GUIDELINE NUDGE (fallback): {EMBEDDED_FALLBACK[fallback_key]}")
        sys.exit(0)

    tool = manifest["tools"].get(manifest_name)
    profile_name = resolve_active_profile_name(input_data, profiles_doc)
    profile = profiles_doc.get("profiles", {}).get(profile_name, {})
    block_unhealthy = bool(profile.get("block_unhealthy", False))
    allowed = resolve_profile(profile_name, profiles_doc, manifest)

    # Out-of-manifest tool (e.g., mcp__chartmogul__list, which isn't in our
    # exploration-slice manifest yet). No-op — nothing to nudge against.
    if not tool:
        sys.exit(0)

    health = (tool.get("health") or {}).get("state")
    in_profile = manifest_name in allowed

    # Healthy + in-profile + no manifested higher-tier alternative → no nudge.
    if health == "healthy" and in_profile:
        # Even healthy + in-profile tools can have a better alternative
        # (e.g. `rg` is healthy but `mcp__fff__grep` is preferred).
        categories = tool.get("category") or []
        for cat in categories:
            alts = pick_alternatives(tool, cat, allowed, manifest)
            if alts:
                preferred = ", ".join(alts)
                emit(
                    f"TOOL GUIDELINE NUDGE (registry): You used {manifest_name}. "
                    f"For category '{cat}', the registry prefers: {preferred}. "
                    f"Profile: {profile_name}."
                )
        sys.exit(0)

    # Unhealthy path — block if profile opts in.
    if health != "healthy" and block_unhealthy:
        emit(
            f"TOOL GUIDELINE BLOCK: {manifest_name} is currently {health}. "
            f"This profile ({profile_name}) blocks unhealthy tools. "
            f"Detail: {(tool.get('health') or {}).get('detail', 'no detail')}",
            decision="deny",
        )

    # Otherwise (out-of-profile OR unhealthy nudge) — emit a soft nudge.
    categories = tool.get("category") or []
    for cat in categories:
        alts = pick_alternatives(tool, cat, allowed, manifest)
        if alts:
            preferred = ", ".join(alts)
            reason = []
            if not in_profile:
                reason.append("out-of-profile")
            if health != "healthy":
                reason.append(f"health={health}")
            why = " ".join(reason) or "policy"
            emit(
                f"TOOL GUIDELINE NUDGE (registry): {manifest_name} ({why}). "
                f"For category '{cat}', try: {preferred}. Profile: {profile_name}."
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
