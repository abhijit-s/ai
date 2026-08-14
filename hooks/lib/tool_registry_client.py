"""
Shared client for reading the tool-registry manifest and profiles.

The manifest lives at ~/.claude/cache/tool-registry-manifest.json, written
atomically by the tool-registry MCP server (Node). Python hooks read it
directly — no MCP round-trip — per KTD3.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Iterable

# Lazy/optional: a missing PyYAML must degrade load_code_repo_roots() to an
# empty list, never crash module import — every hook sharing this module
# (manifest/profile reads included) would go down with it otherwise, which
# would violate this file's own "nudge, never break the call" contract.
try:
    import yaml
except ImportError:
    yaml = None


def _manifest_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "cache", "tool-registry-manifest.json")


# Profile catalog lives next to this file (hooks/lib/ → hooks/profiles.json),
# so it resolves correctly regardless of HOME or CWD. The override path
# argument on load_profiles() lets tests pin to a fixture.
def _default_profiles_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "profiles.json"))


# Vault root under which every project canon's own code-repo registry lives
# (each as <project>/.knowledge/local/repos.local.yaml — see
# load_code_repo_roots). Override via CLAUDE_VAULT_ROOT for other machines.
def _vault_root() -> str:
    return os.path.expanduser(os.environ.get("CLAUDE_VAULT_ROOT") or "~/vaults/workspace")


# Manual supplement for repos with no project-canon repos.local.yaml (e.g. a
# scratch clone, a repo you haven't wired into a knowledge base yet). Empty
# by default — knowledge-base discovery is the primary source; this file
# only needs entries for what that discovery genuinely can't see.
def _manual_code_repo_roots_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "tools", "code-repo-roots.json"))


# Backwards-compatible module-level constants (evaluated at import time).
MANIFEST_PATH = _manifest_path()
PROFILES_PATH = _default_profiles_path()
SCHEMA_VERSION = 1

OVERRIDE_RE = re.compile(r"<!--\s*tools:\s*([\s\S]*?)\s*-->", re.IGNORECASE)


def load_manifest(path: str | None = None) -> dict:
    """Return the parsed manifest dict, or an empty manifest on any read error."""
    # Recompute on each call so tests overriding HOME after import still work.
    p = path or _manifest_path()
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
    p = path or _default_profiles_path()
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, IsADirectoryError):
        return {"version": 1, "profiles": {}}


def _discovered_code_repo_roots(vault_root: str | None) -> list[str]:
    """Code-repo paths from every project canon's own repos.local.yaml.

    Globbing under the vault root means a new project canon is picked up
    with zero changes here — no per-project path needs adding anywhere.
    Best-effort: a missing PyYAML, a missing vault root, an unparseable
    YAML file, or a malformed `repos:` block is skipped rather than
    raised, so neither a broken project canon nor a missing dependency
    can take down the nudge for every other repo (or the hook call
    itself).
    """
    if yaml is None:
        return []
    root = vault_root or _vault_root()
    pattern = os.path.join(root, "**", ".knowledge", "local", "repos.local.yaml")
    roots: list[str] = []
    for overlay_path in glob.glob(pattern, recursive=True):
        try:
            with open(overlay_path) as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        for value in (data.get("repos") or {}).values():
            if isinstance(value, str) and value:
                roots.append(value)
    return roots


def _manual_code_repo_roots(path: str | None) -> list[str]:
    """Code-repo paths from the manual supplement file, or [] if absent/invalid."""
    p = path or _manual_code_repo_roots_path()
    try:
        with open(p) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, IsADirectoryError):
        return []
    return [r for r in (data.get("roots") or []) if isinstance(r, str) and r]


def _normalize_path(p: str) -> str:
    return os.path.normpath(os.path.expanduser(os.path.expandvars(p))).rstrip("/") or "/"


def load_code_repo_roots(vault_root: str | None = None, manual_path: str | None = None) -> list[str]:
    """Return every code-repo path this machine knows about, deduped.

    Hybrid source, in priority order (first-seen wins on duplicates):
      1. Knowledge-base discovery — each project canon's own
         `<project>/.knowledge/local/repos.local.yaml` (see
         _discovered_code_repo_roots). Primary source; covers anything
         already tracked by a knowledge base with zero config here.
      2. Manual supplement — `hooks/tools/code-repo-roots.json`. For
         repos with no knowledge-base canon at all (a scratch clone, a
         repo not yet wired into any project canon).
    """
    raw = _discovered_code_repo_roots(vault_root) + _manual_code_repo_roots(manual_path)
    seen: dict[str, str] = {}
    for r in raw:
        key = _normalize_path(r)
        seen.setdefault(key, r)
    return list(seen.values())


def is_under_code_repo_root(cwd: str, roots: Iterable[str]) -> bool:
    """True if cwd is at or under any configured code-repo root."""
    if not cwd:
        return False
    cwd = _normalize_path(cwd)
    return any(cwd == (r := _normalize_path(root)) or cwd.startswith(r + "/") for root in roots)


# turbo-rag's own registration file — the source of truth for which paths are
# semantically indexed, read directly so a cold engine never costs a hook its
# time budget. Profile follows the engine's own TURBO_RAG_PROFILE convention.
def _turbo_rag_roots_path() -> str:
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    profile = os.environ.get("TURBO_RAG_PROFILE") or "default"
    return os.path.join(config_home, "turbo-rag", profile, "corpus_roots.json")


def load_turbo_rag_roots(path: str | None = None) -> list[dict]:
    """Return turbo-rag's registered corpus roots, or [] if unreadable."""
    p = path or _turbo_rag_roots_path()
    try:
        with open(p) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, IsADirectoryError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("path")] if isinstance(data, list) else []


def resolve_turbo_rag_root(cwd: str, roots: Iterable[dict]) -> dict | None:
    """Return the most-specific corpus root containing cwd, or None.

    Most-specific wins for the same reason it does for fff MCP roots: a nested
    corpus (surge.easygo.io) is registered separately from the umbrella that
    contains it, and its handle is the one worth naming.
    """
    if not cwd:
        return None
    cwd = _normalize_path(cwd)
    matches = [r for r in roots if cwd == (p := _normalize_path(r["path"])) or cwd.startswith(p + "/")]
    return max(matches, key=lambda r: len(_normalize_path(r["path"])), default=None)


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
