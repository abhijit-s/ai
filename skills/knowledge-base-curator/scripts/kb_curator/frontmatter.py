"""Note model + frontmatter parsing + vault walking.

A `Note` packages a single Markdown file's path, frontmatter dict, body,
and H1. `walk_notes(taxonomy)` returns every note in the vault honouring
the configured exclude directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .model import Taxonomy
from .yaml_io import yaml_load, yaml_dump_frontmatter

FM_DELIM = "---"


@dataclass
class Note:
    path: Path
    rel_path: Path
    has_frontmatter: bool
    frontmatter: dict
    body: str
    h1: str | None


def read_note(path: Path, vault_root: Path) -> Note:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _split_frontmatter(text)
    h1 = _first_h1(body)
    rel = path.relative_to(vault_root) if str(path).startswith(str(vault_root)) else path
    return Note(
        path=path,
        rel_path=rel,
        has_frontmatter=fm is not None,
        frontmatter=fm or {},
        body=body,
        h1=h1,
    )


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    if not text.startswith(FM_DELIM):
        return None, text
    end = text.find("\n" + FM_DELIM, len(FM_DELIM))
    if end < 0:
        return None, text
    yaml_block = text[len(FM_DELIM):end].lstrip("\n")
    after = text[end + len(FM_DELIM) + 1:]
    if after.startswith("\n"):
        after = after[1:]
    data = yaml_load(yaml_block) or {}
    if not isinstance(data, dict):
        return None, text
    return data, after


def _first_h1(body: str) -> str | None:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def write_frontmatter(path: Path, fm: dict, body: str) -> None:
    yaml_block = yaml_dump_frontmatter(fm)
    out = f"{FM_DELIM}\n{yaml_block}\n{FM_DELIM}\n\n{body.lstrip()}"
    path.write_text(out, encoding="utf-8")


def walk_notes(tax: Taxonomy) -> list[Note]:
    notes: list[Note] = []
    for p in sorted(tax.vault_root.rglob("*.md")):
        rel = p.relative_to(tax.vault_root)
        if any(part in tax.exclude_dirs for part in rel.parts):
            continue
        notes.append(read_note(p, tax.vault_root))
    return notes
