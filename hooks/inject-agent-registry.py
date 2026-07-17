#!/usr/bin/env python3
"""
SubagentStart hook: inject the specialized agent-type registry into any
sub-agent that itself carries Agent-tool access, so a nested spawn (e.g.
most-capable-agent -> Agent tool) can pick a specialized `subagent_type`
instead of collapsing to `general-purpose` for lack of visibility into the
catalog.

A sub-agent does not inherit the main thread's native "Available agent
types" system-reminder (see subagent-context-propagation-gap memory) —
this hook is the SubagentStart-side re-emission, mirroring the existing
inject-guidelines.py / inject-tool-digest.py pattern.

Config-not-fork: the four CLI-native agent types (general-purpose, claude,
claude-code-guide, statusline-setup) have no `.claude/agents/*.md` file to
discover, so their name/description/tools are declared in
builtin-agents.json. Everything else is discovered generically from
frontmatter.
"""
import glob
import json
import os
import re
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.expanduser("~/.claude/agents")
BUILTINS_FILE = os.path.join(HOOKS_DIR, "builtin-agents.json")

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
AGENT_TOOL_NAMES = {"task", "agent"}


def parse_frontmatter(path):
    try:
        with open(path) as f:
            text = f.read()
    except Exception:
        return None
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm = {}
    key = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and key:
            fm[key] += " " + line.strip()
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        fm[key] = v.strip()
    return fm


def has_agent_access(tools_field):
    # Absent `tools:` frontmatter key means the agent inherits every tool,
    # Agent included.
    if tools_field is None:
        return True
    names = {t.strip().lower() for t in tools_field.split(",") if t.strip()}
    if names == {"*"}:
        return True
    return bool(names & AGENT_TOOL_NAMES)


def load_builtins():
    try:
        with open(BUILTINS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def discover_registry():
    entries = []
    for name, meta in load_builtins().items():
        entries.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "has_agent_access": has_agent_access(meta.get("tools")),
            }
        )
    for path in sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md"))):
        fm = parse_frontmatter(path)
        if not fm or not fm.get("name"):
            continue
        entries.append(
            {
                "name": fm["name"],
                "description": fm.get("description", ""),
                "has_agent_access": has_agent_access(fm.get("tools")),
            }
        )
    return entries


def build_digest(entries, current_agent_type):
    others = [e for e in entries if e["name"] != current_agent_type]
    if not others:
        return None
    lines = [
        "## Nested Agent-Type Registry",
        "",
        (
            "You have Agent-tool access, so you may spawn your own "
            "sub-agents. Sub-agents do not natively receive the main "
            "thread's agent-type catalog, so choose `subagent_type` from "
            "this list when a specialized type fits, rather than "
            "defaulting to `general-purpose`:"
        ),
        "",
    ]
    for e in sorted(others, key=lambda e: e["name"]):
        lines.append(f"- **{e['name']}** — {e['description']}")
    return "\n".join(lines)


def main():
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # Same event shape as inject-guidelines.py / inject-tool-digest.py: the
    # live SubagentStart event carries `agent_type` at the top level; the
    # nested `tool_input.subagent_type` path is kept for test fixtures.
    tool_input = input_data.get("tool_input") or {}
    agent_type = (
        input_data.get("agent_type") or tool_input.get("subagent_type") or ""
    ).strip()

    entries = discover_registry()
    current = next((e for e in entries if e["name"] == agent_type), None)
    # Only worth injecting when this agent type can itself spawn sub-agents —
    # skip the digest entirely for e.g. code-reviewer, skeptic, etc.
    if current is None or not current["has_agent_access"]:
        sys.exit(0)

    digest = build_digest(entries, agent_type)
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
