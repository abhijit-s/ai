#!/usr/bin/env python3
"""Transpile Claude Code agent files into OpenCode agent files.

Source of truth is the Claude-format agents in ``agents/*.md`` (frontmatter
``tools`` is a comma-separated string of PascalCase tool names). OpenCode wants
a different frontmatter schema: ``permission`` (allow/deny per capability),
``mode``, and no ``name`` field (the filename is the identifier). One markdown
file cannot satisfy both harnesses, so the OpenCode variants are *derived* here
and never hand-edited.

Engine vs config: the algorithm (parse frontmatter, project onto OpenCode's
schema, preserve the body verbatim) is generic. Everything harness-specific
lives in the declared tables below -- edit those, not the code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# --- config surface (edit these, not the engine) ---------------------------

# Claude tool name -> OpenCode permission key. None = no analog, drop silently.
# OpenCode has no separate "write" permission: file creation/edits are all
# governed by `edit`. MCP tools (mcp__*) are not governed by these keys at all.
TOOL_TO_PERMISSION: dict[str, str | None] = {
    "Read": "read",
    "Write": "edit",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Grep": "grep",
    "Glob": "glob",
    "Bash": "bash",
    "WebFetch": "webfetch",
    "WebSearch": "websearch",
    "LS": "list",
    "TodoWrite": "todowrite",
    "AskUserQuestion": "question",
    "NotebookRead": None,
    "KillShell": None,
    "BashOutput": None,
}

# The permission keys this transpiler manages. A whitelist means: allow the
# keys the Claude agent listed, and DENY the rest of this set (OpenCode defaults
# unlisted permissions to allow, so an allow-only block would not actually
# gate). Keys outside this set (lsp, skill, doom_loop, external_directory) have
# no Claude analog, so they are left at OpenCode's defaults rather than denied.
MANAGED_PERMISSION_KEYS: list[str] = [
    "read", "edit", "grep", "glob", "bash",
    "webfetch", "websearch", "list", "todowrite", "question", "task",
]

# Claude short model alias -> OpenCode "provider/model" ref. EMPTY by default:
# this machine's OpenCode routes through a LiteLLM provider with no `anthropic`
# provider, so emitting `anthropic/...` would break agent loading. Unmapped
# models are dropped, and the agent inherits OpenCode's default model. To pin a
# tier, add e.g. {"opus": "litellm/opus"} once a LiteLLM alias exists.
MODEL_MAP: dict[str, str] = {}

# Every agent in agents/ is a Task-spawned helper, not a primary driver.
DEFAULT_MODE = "subagent"

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


def allowed_permissions(tools_field: str) -> set[str]:
    """Map a Claude `tools` string to the set of allowed OpenCode perm keys."""
    allowed: set[str] = set()
    for raw in tools_field.split(","):
        tool = raw.strip()
        if not tool or tool.startswith("mcp__"):
            continue
        if tool not in TOOL_TO_PERMISSION:
            print(f"  ! unknown tool {tool!r} -- dropped", file=sys.stderr)
            continue
        perm = TOOL_TO_PERMISSION[tool]
        if perm is not None:
            allowed.add(perm)
    return allowed


def render(fm: dict[str, str], body: str) -> str:
    out = ["---", f"description: {json.dumps(fm.get('description', '').strip())}",
           f"mode: {DEFAULT_MODE}"]

    model = fm.get("model", "").strip()
    if model in MODEL_MAP:
        out.append(f"model: {MODEL_MAP[model]}")

    if "tools" in fm:
        allowed = allowed_permissions(fm["tools"])
        out.append("permission:")
        for key in MANAGED_PERMISSION_KEYS:
            out.append(f"  {key}: {'allow' if key in allowed else 'deny'}")

    out.append("---")
    return "\n".join(out) + "\n" + body


def main() -> int:
    ai_dir = Path(__file__).resolve().parent.parent
    src_dir = ai_dir / "agents"
    dst_dir = ai_dir / "opencode" / "agents"
    dst_dir.mkdir(parents=True, exist_ok=True)

    sources = sorted(src_dir.glob("*.md"))
    if not sources:
        print(f"no agents found in {src_dir}", file=sys.stderr)
        return 1

    for src in sources:
        fm, body = split_frontmatter(src.read_text())
        if fm is None:
            print(f"  ! {src.name}: no frontmatter, skipped", file=sys.stderr)
            continue
        (dst_dir / src.name).write_text(render(fm, body))
        print(f"  agents/{src.name} -> opencode/agents/{src.name}")

    print(f"transpiled {len(sources)} agent(s) -> {dst_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
