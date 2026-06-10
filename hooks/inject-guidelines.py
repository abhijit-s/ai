#!/usr/bin/env python3
"""
SubagentStart hook: inject user-configured guidelines into sub-agents.

Slug resolution order:
  1. Explicit — prompt contains <!-- inject: slug1,slug2 -->
  2. Profile  — guidelines.json profiles[subagent_type]
  3. Default  — guidelines.json profiles["default"]

Output is grouped by category (in category_order) so injected prose is
structurally coherent rather than a flat concatenation.
"""
import json
import os
import re
import sys


HOOKS_DIR = os.path.expanduser("~/.claude/hooks")
CONFIG_FILE = os.path.join(HOOKS_DIR, "guidelines.json")
GUIDELINES_DIR = os.path.join(HOOKS_DIR, "guidelines")

INJECT_RE = re.compile(r"<!--\s*inject:\s*([^>]+?)\s*-->", re.IGNORECASE)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)


def resolve_slugs(config, agent_type, prompt):
    match = INJECT_RE.search(prompt)
    if match:
        return [s.strip() for s in match.group(1).split(",") if s.strip()]
    profiles = config.get("profiles", {})
    return profiles.get(agent_type) or profiles.get("default") or []


def build_output(config, slugs):
    slug_meta = config.get("slugs", {})
    categories = config.get("categories", [])

    sections = []
    emitted = set()

    # Emit slugs grouped by category in declared order
    for category in categories:
        cat_slugs = [
            s for s in slugs
            if s in slug_meta and slug_meta[s].get("category") == category
        ]
        cat_slugs.sort(key=lambda s: slug_meta[s].get("order", 99))
        for slug in cat_slugs:
            section = load_section(slug, slug_meta)
            if section:
                sections.append(section)
                emitted.add(slug)

    # Catch-all: slugs not assigned to any known category
    for slug in slugs:
        if slug not in emitted:
            section = load_section(slug, slug_meta)
            if section:
                sections.append(section)

    return sections


def load_section(slug, slug_meta):
    path = os.path.join(GUIDELINES_DIR, f"{slug}.txt")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        content = f.read().strip()
    title = slug_meta.get(slug, {}).get("title", slug)
    return f"### {title}\n\n{content}"


def main():
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # Live Claude Code SubagentStart event puts `agent_type` at the top level.
    # The nested `tool_input.subagent_type` path is the legacy shape and is
    # kept as a fallback. The live event does NOT carry the spawn prompt, so
    # `<!-- inject: ... -->` overrides are currently only reachable via the
    # legacy shape (tests + any future PreToolUse:Task plumbing).
    tool_input = input_data.get("tool_input") or {}
    agent_type = (
        input_data.get("agent_type")
        or tool_input.get("subagent_type")
        or "default"
    ).strip()
    prompt = input_data.get("prompt") or tool_input.get("prompt") or ""

    config = load_config()
    if not config:
        sys.exit(0)

    slugs = resolve_slugs(config, agent_type, prompt)
    if not slugs:
        sys.exit(0)

    sections = build_output(config, slugs)
    if not sections:
        sys.exit(0)

    body = "\n\n---\n\n".join(sections)
    output = f"## User Guidelines\n\n{body}"

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": output,
        }
    }))


if __name__ == "__main__":
    main()
