"""Wiki-link / Markdown-link parsing and broken-link detection.

Two link forms are supported:
  obsidian → [[Stem]], [[Stem#section|display]], [[Folder/Stem]]
  markdown → [display](file.md), [display](Folder/File%20Name.md)

Code-span aware: links inside `inline code` or ```fenced blocks``` are
ignored so README examples don't trigger broken-link findings.
"""

from __future__ import annotations

import re
from pathlib import Path

from .frontmatter import walk_notes
from .model import Taxonomy

WIKI_LINK_RE = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]+)?(?:\|([^\[\]]+))?\]\]")
MD_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^)\s#?][^)#]*?)(?:#[^)]*)?\)")

# Targets that look like wiki-links but are actually inline math or
# placeholders (e.g., LaTeX fractions). Skip during link checking.
_NON_LINK_TARGET = re.compile(r"[*\\^]|^[A-Za-z]/[A-Za-z]$|^[A-Za-z]\*")


def _code_span_ranges(body: str) -> list[tuple[int, int]]:
    """Return [(start, end)] character ranges occupied by code spans.

    Used to filter out wiki-link patterns that appear *inside* backticks
    or fenced blocks — those are documentation, not real links. The
    classic false positive this prevents: a README that includes
    `` `[[Note Name]]` `` as an example of the syntax.

    Important quirk: we report ranges as positions in the ORIGINAL body
    rather than stripping them out, because the position offsets are
    used to filter ``re.finditer`` matches against the original text.
    Earlier implementations blanked-out code spans length-preservingly,
    which broke wiki-link targets containing backticks (e.g. a note
    whose stem includes "Go", a backtick-wrapped "context", and
    "Package").

    Pinned by `tests/test_links.TestFindWikiLinks.test_skips_inline_code`
    and `test_skips_fenced_code_blocks`.
    """
    ranges: list[tuple[int, int]] = []
    fenced = list(re.finditer(r"```.*?```", body, flags=re.DOTALL))
    for m in fenced:
        ranges.append((m.start(), m.end()))
    fenced_spans = list(ranges)
    def _in_fence(pos: int) -> bool:
        return any(s <= pos < e for s, e in fenced_spans)
    for m in re.finditer(r"`[^`\n]+`", body):
        if not _in_fence(m.start()):
            ranges.append((m.start(), m.end()))
    return ranges


def find_wiki_links(body: str, syntax: str = "obsidian") -> list[tuple[str, str | None]]:
    """Return [(target, display_or_none)] for every link in `body`."""
    if syntax == "none":
        return []
    code_ranges = _code_span_ranges(body)
    def _in_code(pos: int) -> bool:
        return any(s <= pos < e for s, e in code_ranges)
    out: list[tuple[str, str | None]] = []
    if syntax == "obsidian":
        for m in WIKI_LINK_RE.finditer(body):
            target = m.group(1).strip()
            if _in_code(m.start()): continue
            if _NON_LINK_TARGET.search(target): continue
            out.append((target, m.group(2).strip() if m.group(2) else None))
    elif syntax == "markdown":
        for m in MD_LINK_RE.finditer(body):
            href = m.group(2).strip()
            if _in_code(m.start()): continue
            if re.match(r"^[a-z]+://", href, re.IGNORECASE): continue
            if not href.lower().endswith(".md"): continue
            stem = href[:-3].replace("%20", " ")
            stem = stem.rsplit("/", 1)[-1]
            out.append((stem, m.group(1).strip()))
    return out


def build_link_index(tax: Taxonomy) -> dict[str, Path]:
    """Map note stem → path. Wiki-links resolve by stem in Obsidian."""
    return {n.path.stem: n.path for n in walk_notes(tax)}


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two strings."""
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    if len(a) > len(b): a, b = b, a
    prev = list(range(len(a) + 1))
    for _i, cb in enumerate(b, 1):
        curr = [_i]
        for j, ca in enumerate(a, 1):
            ins = curr[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(ins, dele, sub))
        prev = curr
    return prev[-1]
