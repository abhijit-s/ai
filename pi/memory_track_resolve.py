#!/usr/bin/env python3
"""Resolve the memory-kit corpus (if any) that claims a given cwd.

Stdlib-only reader of the per-machine corpus registry at
``~/.config/memory-kit/config.toml`` (ADR-020) -- the SAME file memory-kit's
own Claude Code hooks read. Deliberately does NOT import the Claude plugin's
own `memory_kit_common` module: that lives under a versioned, Claude-managed
plugin cache directory, not a stable path a Pi extension should couple to.
Reading the shared TOML registry directly keeps this harness-agnostic and
automatically picks up any new corpus (repo or vault) the registry grows,
with no code change here.

Usage: memory_track_resolve.py <cwd>
Prints one JSON object to stdout: {} if no writable corpus claims the cwd,
else {"corpus": name, "native_auto_dir": path, "identity_dir": path|None}.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "memory-kit" / "config.toml"


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return path == root


def _excluded(cwd_path: Path, data_root: str | None, exclude: list[str]) -> bool:
    """True when cwd falls under one of the corpus's `exclude` globs (patterns
    are `<subpath>/**`, relative to data_root). A corpus that excludes a
    subtree is signaling that subtree belongs to a DIFFERENT (often
    not-yet-declared) corpus -- never misattribute a write there."""
    if not exclude or not data_root:
        return False
    for pattern in exclude:
        prefix = pattern[:-3] if pattern.endswith("/**") else pattern
        if _under(cwd_path, Path(data_root) / prefix):
            return True
    return False


def resolve(cwd: str) -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cwd_path = Path(cwd).resolve()

    best_name: str | None = None
    best_root_len = -1
    best_corpus: dict = {}

    for name, corpus in config.get("corpus", {}).items():
        if not corpus.get("writable", False):
            continue  # a read-only corpus (e.g. engineering-library) never owns a write reroute
        if _excluded(cwd_path, corpus.get("data_root"), corpus.get("exclude") or []):
            continue
        roots = corpus.get("read_roots") or (
            [corpus["data_root"]] if corpus.get("data_root") else []
        )
        for root in roots:
            root_path = Path(root)
            if _under(cwd_path, root_path) and len(str(root_path)) > best_root_len:
                best_name, best_root_len, best_corpus = name, len(str(root_path)), corpus

    if best_name is None:
        return {}

    data_root = best_corpus.get("data_root")
    memory_dir = best_corpus.get("memory_dir")
    native_auto_dir = (
        str(Path(data_root) / memory_dir / "auto") if data_root and memory_dir else None
    )
    identity_dir = (best_corpus.get("layers") or {}).get("identity")

    return {
        "corpus": best_name,
        "native_auto_dir": native_auto_dir,
        "identity_dir": identity_dir,
    }


def main() -> int:
    cwd = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        result = resolve(cwd)
    except Exception:
        result = {}  # fail-open: a config fault must never break the caller
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
