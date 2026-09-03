#!/usr/bin/env python3
"""Transpile the shared mcp.json into pi-mcp-adapter's config schema.

Source of truth is ``mcp.json`` at the repo root (Claude's
``{"mcpServers": {name: {command, args, env, url}}}``). pi-mcp-adapter reads the
same top-level ``mcpServers`` key at ``~/.pi/agent/mcp.json`` (Pi's "global
override" config layer), with a couple of adapter-specific fields layered on:
``lifecycle`` (connection strategy) and ``directTools`` (skip its proxy-tool
indirection for a server). This merges as a UNION with whatever is already at
the destination, so anything hand-added there is preserved. Idempotent:
re-running only updates the servers sourced from mcp.json.

Target: set ``PI_MCP_CONFIG`` to the file to merge into. Defaults to
``~/.pi/agent/mcp.json``. The ``pi-mcp`` Makefile target points this at the
chezmoi SOURCE counterpart of that file (after a one-time ``chezmoi add``) and
runs ``chezmoi apply`` afterwards -- see ai/Makefile.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REMOTE_TYPES = {"http", "sse", "remote"}

# Per-server tuning the source mcp.json has no notion of. Keyed by server name;
# merged into the transpiled entry. Default lifecycle "lazy" matches the
# adapter's own default, so most servers need no entry here at all.
SERVER_TUNING: dict[str, dict] = {}


def to_pi(name: str, spec: dict) -> dict | None:
    """Project one Claude mcpServers entry onto pi-mcp-adapter's schema.

    Returns None for a local server whose executable is not on PATH -- same
    reasoning as the OpenCode transpiler: no point emitting an entry the
    adapter can only fail to spawn.
    """
    tuning = SERVER_TUNING.get(name, {})

    if spec.get("url") or spec.get("type") in REMOTE_TYPES:
        entry: dict = {"url": spec["url"], **tuning}
        if spec.get("headers"):
            entry["headers"] = spec["headers"]
        return entry

    resolved = shutil.which(spec["command"])
    if resolved is None:
        return None

    # pi-mcp-adapter spawns commands directly (no shell), so a literal
    # "${HOME}"-style placeholder in args/env would never be expanded --
    # unlike Claude Code's own mcp.json reader, which does this itself.
    args = [os.path.expandvars(a) for a in spec.get("args", [])]
    entry = {"command": resolved, "args": args, "lifecycle": "lazy", **tuning}
    if spec.get("env"):
        entry["env"] = {k: os.path.expandvars(v) for k, v in spec["env"].items()}
    return entry


def main() -> int:
    ai_dir = Path(__file__).resolve().parent.parent
    src = ai_dir / "mcp.json"
    dst = Path(os.environ.get(
        "PI_MCP_CONFIG",
        Path.home() / ".pi" / "agent" / "mcp.json",
    ))

    servers = json.loads(src.read_text()).get("mcpServers", {})
    transpiled = {n: e for n, spec in servers.items()
                  if (e := to_pi(n, spec)) is not None}
    skipped = servers.keys() - transpiled.keys()

    config = json.loads(dst.read_text()) if dst.exists() else {}
    mcp = config.setdefault("mcpServers", {})
    # Prune servers we own (present in mcp.json) but are no longer emitting,
    # so a now-uninstalled CLI drops out. Hand-added servers mcp.json never
    # declared are left untouched.
    for name in servers.keys() & mcp.keys():
        if name in skipped:
            del mcp[name]
    mcp.update(transpiled)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(config, indent=2) + "\n")

    for name in sorted(transpiled):
        print(f"  mcpServers/{name} -> {dst}")
    for name in sorted(skipped):
        print(f"  mcpServers/{name} SKIPPED (executable not on PATH)", file=sys.stderr)
    print(f"merged {len(transpiled)} MCP server(s), skipped {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
