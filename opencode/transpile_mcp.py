#!/usr/bin/env python3
"""Transpile the shared mcp.json into OpenCode's `mcp` config block.

Claude/Codex use ``{"mcpServers": {name: {command, args, env}}}``. OpenCode uses
``{"mcp": {name: {type: "local"|"remote", command: [...], ...}}}``. This merges
the transpiled servers into the target config as a UNION with whatever is already
there (providers, the pencil MCP), so nothing hand-added is lost. Idempotent:
re-running only updates the servers sourced from mcp.json.

Target: set ``OPENCODE_CONFIG`` to the file to merge into. opencode.json is now
chezmoi-managed, so ``make opencode-mcp`` points this at the chezmoi SOURCE (not
the symlinked target, which would drift) and then runs ``chezmoi apply``. Because
commands are resolved to absolute paths per machine, run it on the work machine
(the canonical superset of installed tools) and commit the source via chezmoi.
Absent tools on another machine simply surface as non-fatal failed MCP entries.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REMOTE_TYPES = {"http", "sse", "remote"}


def to_opencode(spec: dict) -> dict | None:
    """Project one Claude mcpServers entry onto OpenCode's schema.

    Returns None for a local server whose executable is not on PATH -- there is
    no point emitting a config entry OpenCode can only fail to spawn. The command
    is resolved to an absolute path (like the fff entry) so it works regardless
    of the slim environment OpenCode spawns MCP servers with. Run `make` from a
    shell where your CLIs are on PATH so they resolve.
    """
    if spec.get("url") or spec.get("type") in REMOTE_TYPES:
        entry: dict = {"type": "remote", "url": spec["url"], "enabled": True}
        if spec.get("headers"):
            entry["headers"] = spec["headers"]
        return entry

    resolved = shutil.which(spec["command"])
    if resolved is None:
        return None

    entry = {"type": "local", "command": [resolved, *spec.get("args", [])],
             "enabled": True}
    if spec.get("env"):
        entry["environment"] = spec["env"]
    return entry


def main() -> int:
    ai_dir = Path(__file__).resolve().parent.parent
    src = ai_dir / "mcp.json"
    dst = Path(os.environ.get(
        "OPENCODE_CONFIG",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ))

    servers = json.loads(src.read_text()).get("mcpServers", {})
    transpiled = {n: e for n, spec in servers.items()
                  if (e := to_opencode(spec)) is not None}
    skipped = servers.keys() - transpiled.keys()

    config = json.loads(dst.read_text()) if dst.exists() else {
        "$schema": "https://opencode.ai/config.json"
    }
    mcp = config.setdefault("mcp", {})
    # Prune servers we own (present in mcp.json) but are no longer emitting,
    # so a now-uninstalled CLI drops out. Hand-added servers (e.g. pencil) that
    # mcp.json never declared are left untouched.
    for name in servers.keys() & mcp.keys():
        if name in skipped:
            del mcp[name]
    mcp.update(transpiled)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(config, indent=2) + "\n")

    for name in sorted(transpiled):
        print(f"  mcp/{name} -> {dst}")
    for name in sorted(skipped):
        print(f"  mcp/{name} SKIPPED (executable not on PATH)", file=sys.stderr)
    print(f"merged {len(transpiled)} MCP server(s), skipped {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
