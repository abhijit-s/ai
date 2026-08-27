#!/usr/bin/env python3
"""Shared fff-mcp root resolution for the SessionStart and SubagentStart hooks.

fff-mcp is a shared singleton with ONE global default root, so any call that
omits `base_path` searches that default -- the personal vault -- rather than
the caller's working tree, and an empty result reads as "not found". Both
entry points need the same answer:

  * `preload-search-tools.sh` (SessionStart, main thread) embeds the roots
    listing plus the cwd hint, mirroring how it lists turbo-rag corpus roots.
  * `inject-tool-digest.py` (SubagentStart) prepends the cwd hint to the
    tool digest.

Roots are read from fff's own `config.toml` (config-not-fork: fff owns that
surface, these hooks only read it) rather than by asking the engine -- a cold
engine takes seconds to answer and SessionStart hooks run on a 5s budget.

Run as a script to print the block the shell hook embeds:

    python3 fff_roots.py [--cwd PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib

INDENT = "     "
DEFAULT_MARK = "(default)"


def config_path() -> str | None:
    """Locate the fff-mcp roots config."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    for name in ("config.toml", "mcp.toml"):
        path = os.path.join(base, "fff", name)
        if os.path.isfile(path):
            return path
    return None


def load_roots() -> tuple[str | None, list[dict]]:
    """Return `(default_root_name, roots)` from the fff config.

    Each root is `{"name", "path", "real"}` -- `path` as configured (user
    expanded, what you pass as `base_path`) and `real` fully resolved for
    containment checks. Returns `(None, [])` when the config is absent or
    unparseable; callers stay silent rather than guessing.
    """
    path = config_path()
    if not path:
        return None, []
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except Exception:
        return None, []

    mcp = cfg.get("mcp") or {}
    roots = []
    for root in mcp.get("roots") or []:
        name, raw = root.get("name"), root.get("path")
        if not name or not raw:
            continue
        expanded = os.path.expanduser(raw)
        try:
            real = os.path.realpath(expanded)
        except Exception:
            real = expanded
        roots.append({"name": name, "path": expanded, "real": real})
    return mcp.get("default"), roots


def resolve_root(cwd: str, roots: list[dict]) -> dict | None:
    """Return the most-specific root containing `cwd`, or None.

    Longest matching path wins: a nested corpus repo beats the umbrella root
    that also contains it.
    """
    try:
        real_cwd = os.path.realpath(cwd)
    except Exception:
        real_cwd = cwd

    match = None
    for root in roots:
        real = root["real"]
        if real_cwd == real or real_cwd.startswith(real + os.sep):
            if match is None or len(real) > len(match["real"]):
                match = root
    return match


def roots_listing() -> str | None:
    """Aligned `@name (default) path` lines, default root first."""
    default_name, roots = load_roots()
    if not roots:
        return None

    roots = sorted(roots, key=lambda r: r["name"] != default_name)
    name_w = max(len(r["name"]) for r in roots)
    mark_w = len(DEFAULT_MARK)
    lines = []
    for root in roots:
        mark = DEFAULT_MARK if root["name"] == default_name else ""
        lines.append(
            f"{INDENT}@{root['name']:<{name_w}}  {mark:<{mark_w}}  {root['path']}"
        )
    return "\n".join(lines)


def root_hint(cwd: str) -> str | None:
    """Tell the agent which `base_path` its cwd needs.

    Returns None when the default root already covers cwd (nothing to warn
    about) or when config/cwd are unavailable.
    """
    if not cwd:
        return None
    default_name, roots = load_roots()
    if not roots:
        return None

    try:
        real_cwd = os.path.realpath(cwd)
    except Exception:
        real_cwd = cwd

    match = resolve_root(real_cwd, roots)
    if match is None:
        # cwd is outside every indexed root — fff would silently serve the
        # default (personal vault). Steer to the lexical ladder instead.
        return (
            "⚠️ fff MCP has no root covering this working tree "
            f"(`{real_cwd}`). An unqualified fff search would hit the default "
            f"root (`{default_name}`, the personal vault), not your files — "
            "either pass an explicit `base_path`, or drop to the lexical "
            "ladder (rg → fd) — or ast-grep, if the query is structural."
        )

    if match["name"] == default_name:
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
        f"`{match['name']}`. Pass `base_path={match['real']}` on every "
        f"`mcp__fff__*` call{worktree_note}."
    )


def preload_block(cwd: str) -> str | None:
    """Roots listing plus the cwd hint, as embedded by the SessionStart hook."""
    listing = roots_listing()
    if not listing:
        return None
    hint = root_hint(cwd)
    parts = [f"{INDENT[:3]}Indexed fff roots — pass `base_path`:", listing]
    if hint:
        parts.append(f"{INDENT[:3]}{hint}")
    return "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default="", help="working directory to resolve")
    args = parser.parse_args()

    block = preload_block(args.cwd or os.getcwd())
    if not block:
        return 1  # caller substitutes its own fallback line
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
