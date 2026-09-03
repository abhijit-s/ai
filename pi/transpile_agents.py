#!/usr/bin/env python3
"""Transpile Claude Code agent files into pi-subagents agent files.

Source of truth is the Claude-format agents in ``agents/*.md`` (frontmatter
``tools`` is a comma-separated string of PascalCase tool names). pi-subagents
does not read that format directly -- it wants its own frontmatter schema
(lowercase built-in tool names, `model:` as a bare provider id or fuzzy name).
One markdown file cannot satisfy both harnesses, so the pi-subagents variants
are *derived* here and never hand-edited.

Engine vs config: the algorithm (parse frontmatter, project onto pi-subagents'
schema, preserve the body verbatim) is generic. Everything harness-specific
lives in the declared table below -- edit that, not the code.
"""

from __future__ import annotations

import sys
from pathlib import Path

# --- config surface (edit this, not the engine) -----------------------------

# Claude tool name -> pi-subagents built-in tool name. None = no analog,
# dropped silently. Pi's 7 built-ins: read, grep, find, bash, ls, write, edit.
# `model:` needs no mapping table: pi-subagents accepts the same fuzzy names
# Claude's agents already use ("opus", "sonnet", "haiku") directly.
TOOL_TO_BUILTIN: dict[str, str | None] = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Grep": "grep",
    "Glob": "find",
    "LS": "ls",
    "Bash": "bash",
    "WebFetch": None,
    "WebSearch": None,
    "NotebookRead": None,
    "TodoWrite": None,
    "AskUserQuestion": None,
    "KillShell": None,
    "BashOutput": None,
}

# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """Return (frontmatter dict, body). Frontmatter is flat `key: value`."""
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, text
    fm: dict[str, str] = {}
    for ln in lines[1:end]:
        if not ln.strip() or ln.lstrip().startswith("#") or ":" not in ln:
            continue
        key, val = ln.split(":", 1)
        fm[key.strip()] = val.strip()
    return fm, "\n".join(lines[end + 1:])


def mapped_tools(tools_field: str) -> list[str]:
    """Map a Claude `tools` string to an ordered, deduped list of Pi built-ins.

    MCP tools (`mcp__*`) are dropped, not translated: pi-subagents' default
    `extensions: true` already lets a generated agent reach every connected
    MCP (Model Context Protocol) server, so no per-tool `ext:` selector is
    needed to preserve that access.
    """
    seen: list[str] = []
    for raw in tools_field.split(","):
        tool = raw.strip()
        if not tool or tool.startswith("mcp__"):
            continue
        if tool not in TOOL_TO_BUILTIN:
            print(f"  ! unknown tool {tool!r} -- dropped", file=sys.stderr)
            continue
        mapped = TOOL_TO_BUILTIN[tool]
        if mapped and mapped not in seen:
            seen.append(mapped)
    return seen


def render(fm: dict[str, str], body: str) -> str:
    out = ["---"]

    if "description" in fm:
        out.append(f"description: {fm['description'].strip()}")

    model = fm.get("model", "").strip()
    if model:
        out.append(f"model: {model}")

    if "tools" in fm:
        tools = mapped_tools(fm["tools"])
        # An empty result (e.g. only mcp__* entries) means "leave `tools:`
        # unset" -- pi-subagents' default is all 7 built-ins, same as an
        # unrestricted Claude agent gets full tool access.
        if tools:
            out.append(f"tools: {', '.join(tools)}")

    out.append("---")
    return "\n".join(out) + "\n" + body


def transpile_all(src_dir: Path) -> list[tuple[str, str]]:
    """Return (filename, rendered pi-subagents markdown) for every source agent."""
    out: list[tuple[str, str]] = []
    for src in sorted(src_dir.glob("*.md")):
        fm, body = split_frontmatter(src.read_text())
        if fm is None:
            print(f"  ! {src.name}: no frontmatter, skipped", file=sys.stderr)
            continue
        out.append((src.name, render(fm, body)))
    return out


def main() -> int:
    check = "--check" in sys.argv[1:]
    ai_dir = Path(__file__).resolve().parent.parent
    src_dir = ai_dir / "agents"
    dst_dir = ai_dir / "pi" / "agents"

    rendered = transpile_all(src_dir)
    if not rendered:
        print(f"no agents found in {src_dir}", file=sys.stderr)
        return 1

    if check:
        drifted = [
            name for name, text in rendered
            if not (dst_dir / name).exists()
            or (dst_dir / name).read_text() != text
        ]
        for name in drifted:
            print(f"  DRIFT: pi/agents/{name} is stale", file=sys.stderr)
        if drifted:
            print(f"{len(drifted)} agent(s) drifted -- run `make pi-agents`",
                  file=sys.stderr)
            return 1
        print(f"in sync: {len(rendered)} agent(s) match source")
        return 0

    dst_dir.mkdir(parents=True, exist_ok=True)
    for name, text in rendered:
        (dst_dir / name).write_text(text)
        print(f"  agents/{name} -> pi/agents/{name}")
    print(f"transpiled {len(rendered)} agent(s) -> {dst_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
