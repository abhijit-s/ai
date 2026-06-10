"""
Shared client for reading the tool-registry manifest and profiles.

The manifest lives at ~/.claude/cache/tool-registry-manifest.json, written
atomically by the tool-registry MCP server (Node). Python hooks read it
directly — no MCP round-trip — per KTD3.
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterable

MANIFEST_PATH = os.path.expanduser("~/.claude/cache/tool-registry-manifest.json")
DEFAULT_DOTFILES_ROOT = os.path.expanduser("~/.dotfiles/ai")
PROFILES_PATH = os.path.join(DEFAULT_DOTFILES_ROOT, "hooks", "profiles.json")
SCHEMA_VERSION = 1

OVERRIDE_RE = re.compile(r"<!--\s*tools:\s*([\s\S]*?)\s*-->", re.IGNORECASE)


def load_manifest(path: str | None = None) -> dict:
    """Return the parsed manifest dict, or an empty manifest on any read error."""
    p = path or MANIFEST_PATH
    try:
        with open(p) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, IsADirectoryError):
        return _empty_manifest()
    if data.get("schema_version") != SCHEMA_VERSION:
        return _empty_manifest()
    return data


def load_profiles(path: str | None = None) -> dict:
    """Return the parsed hooks/profiles.json dict, or an empty profile catalog."""
    p = path or PROFILES_PATH
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, IsADirectoryError):
        return {"version": 1, "profiles": {}}


def _empty_manifest() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": None,
        "last_success": None,
        "tools": {},
        "discovery_errors": [],
    }


def resolve_profile(name: str, profiles_doc: dict, manifest: dict) -> set[str]:
    """Return the set of allowed tool names for the named profile."""
    profiles = (profiles_doc or {}).get("profiles", {})
    profile = profiles.get(name)
    if not profile:
        return set()
    explicit = set(profile.get("tools", []) or [])
    categories = set(profile.get("categories", []) or [])
    allowed: set[str] = set(explicit)
    for tool_name, tool in (manifest or {}).get("tools", {}).items():
        cats = (tool or {}).get("category", []) or []
        if any(c in categories for c in cats):
            allowed.add(tool_name)
    return allowed


def parse_override(prompt: str | None) -> list[str] | None:
    """Return the override token list parsed from a `<!-- tools: ... -->` comment, or None."""
    if not prompt:
        return None
    m = OVERRIDE_RE.search(prompt)
    if not m:
        return None
    items = [s.strip() for s in m.group(1).split(",")]
    return [s for s in items if s]


def resolve_override(tokens: Iterable[str], profiles_doc: dict, manifest: dict) -> set[str]:
    """Resolve an override token list to an allowed tool set.

    If a single token names a known profile, use that profile. Otherwise treat
    each token as either a tool name or a category name; result = explicit
    tool names ∪ tools in any of the listed categories.
    """
    tokens = list(tokens)
    profiles = (profiles_doc or {}).get("profiles", {})
    if len(tokens) == 1 and tokens[0] in profiles:
        return resolve_profile(tokens[0], profiles_doc, manifest)
    tools_map = (manifest or {}).get("tools", {})
    allowed: set[str] = set()
    categories: set[str] = set()
    for tok in tokens:
        if tok in tools_map:
            allowed.add(tok)
        else:
            categories.add(tok)
    for name, tool in tools_map.items():
        cats = (tool or {}).get("category", []) or []
        if any(c in categories for c in cats):
            allowed.add(name)
    return allowed
