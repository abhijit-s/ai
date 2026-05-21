"""Derivation logic: classify content, infer kind/placement, read git dates.

Pure functions wherever possible — caller passes a Taxonomy and a path or
body, gets back a dict of suggested/derived fields. The mutating side
(writing frontmatter) lives in commands.py.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .model import Area, Taxonomy
from .slugs import slugify


# ---------------------------------------------------------------------------
# Filename normalisation
# ---------------------------------------------------------------------------

_LEADING_EMOJI_RE = re.compile(
    r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    r"\U0001F900-\U0001F9FF⌀-⏿⬀-⯿]+\s*"
)


def canonical_filename(title: str, naming: dict) -> str:
    """Apply naming rules to a title to produce a canonical filename stem.

    `naming` is the `naming:` section from taxonomy.yaml.

    Quirks worth knowing before you edit this:
      - Underscores become hyphens (not spaces). This preserves identifier
        meaning: `io_uring` is a recognisable token; `io uring` is not.
      - Terminal `?` / `!` are *dropped*, not replaced — a trailing hyphen
        in a filename looks like a hand-typed mistake.
      - Structural separators (`/ \\ : * " < > |`) become hyphens to keep
        the rest of the title readable. A multi-arrow `->` chain is
        further collapsed into an em-dash sentence break.
      - Leading emoji prefixes are stripped here so `emojis apply` can
        re-add the configured sigil idempotently.
    """
    name = re.sub(r"\s+", " ", title.strip())
    if naming.get("replace_underscores", True):
        # Hyphen (not space) preserves token boundaries: io_uring → io-uring.
        name = name.replace("_", "-")
    if naming.get("strip_emoji_prefix", True):
        name = _LEADING_EMOJI_RE.sub("", name)
    # Drop terminal punctuation; translate structural separators to hyphens.
    name = re.sub(r"[?!]+", "", name)
    name = re.sub(r"[/\\:*\"<>|]", "-", name)
    # Multi-hyphen produced by `>` translation becomes an em-dash sentence
    # break, which reads better than `- - -`.
    name = re.sub(r"\s*-\s*-\s*", " — ", name)
    name = re.sub(r"\s{2,}", " ", name)
    name = name.rstrip(" -.,")
    cap = naming.get("max_length", 120)
    if len(name) > cap:
        name = name[:cap].rstrip()
    return name


# ---------------------------------------------------------------------------
# Classification (path/category/tags suggestion for a free-text note)
# ---------------------------------------------------------------------------

def classify_text(body: str, stem: str, tax: Taxonomy) -> dict:
    """Score every category against filename + body keywords.

    Crude bag-of-words scoring — intentionally simple so behaviour is
    predictable and an AI caller can override.
    """
    haystack = f"{stem}\n{body}".lower()
    scores: list[tuple[float, Area]] = []
    for area in tax.categories().values():
        score = 0.0
        if area.slug in stem.lower() or area.path.lower() in stem.lower():
            score += 3.0
        for token in area.slug.split("-"):
            if len(token) < 3:
                continue
            score += haystack.count(token) * 0.5
        for word in re.findall(r"[a-zA-Z]{4,}", area.central_question.lower()):
            if word in haystack:
                score += 0.3
        scores.append((score, area))

    scores.sort(key=lambda s: s[0], reverse=True)
    top_score, top = scores[0]
    alternates = [{"category": a.slug, "score": s} for s, a in scores[1:4] if s > 0]

    tag_hits: list[str] = []
    for t in sorted(tax.controlled_tags):
        for token in t.split("-"):
            if len(token) >= 3 and token in haystack:
                tag_hits.append(t)
                break
    tags = [top.slug] + [t for t in tag_hits if t != top.slug][:4]

    return {
        "category": top.slug,
        "central_question": top.central_question,
        "pillar": top.pillar_slug,
        "target_dir": f"{top.pillar_slug_path}/{top.path}",
        "tags": tags,
        "score": top_score,
        "alternates": alternates,
    }


# ---------------------------------------------------------------------------
# Derived frontmatter fields
# ---------------------------------------------------------------------------

def derive_kind(fm: dict, path: Path) -> str | None:
    """Infer note kind from tags or filename. Returns None when uncertain."""
    if path.name == "README.md":
        return "index"
    tags = set(fm.get("tags") or [])
    for k in ("crash-course", "deep-dive", "reference", "index"):
        if k in tags:
            return k
    stem_l = path.stem.lower()
    if "crash course" in stem_l: return "crash-course"
    if "deep dive" in stem_l: return "deep-dive"
    return None


def derive_placement(rel_path: Path, tax: Taxonomy) -> dict:
    """Map a vault-relative path to {pillar, sub_area, topic} slugs.

    Notes:
      pillar    = first path segment, looked up in taxonomy.pillars
      sub_area  = third segment if it matches `path_conventions.sub_area_pattern`
      topic     = the first non-numbered intermediate segment, otherwise
                  the second intermediate
    """
    out: dict[str, str] = {}
    parts = rel_path.parts
    if len(parts) < 2:
        return out
    pillar_dir = parts[0]
    for p in tax.pillars:
        if p.path == pillar_dir:
            out["pillar"] = p.slug
            break
    area = tax.area_for_path(rel_path)
    if not area:
        return out

    intermediates = parts[2:-1]
    if not intermediates:
        return out

    sub_pattern = tax.path_conventions.get("sub_area_pattern")
    sub_re = re.compile(sub_pattern) if sub_pattern else None
    first = intermediates[0]
    m = sub_re.match(first) if sub_re else None
    if m:
        name = m.group(1) if m.groups() else first
        out["sub_area"] = slugify(name, tax.slug_case)
        if len(intermediates) >= 2:
            out["topic"] = slugify(intermediates[1], tax.slug_case)
    else:
        out["topic"] = slugify(first, tax.slug_case)
    return out


# ---------------------------------------------------------------------------
# Git dates
# ---------------------------------------------------------------------------

def git_dates(file_path: Path, vault_root: Path) -> tuple[str | None, str | None]:
    """Return (created, updated) ISO dates from `git log --follow`.

    `--follow` tracks across renames so a file's creation date stays
    correct after a `mv`. The fallback path (`git ls-files`) handles a
    real-world bug: on case-insensitive filesystems with
    `core.ignorecase=true`, `git ls-files` reports paths under their
    *indexed* spelling, which may differ in case from the on-disk path.
    Passing the on-disk path to `git log` returns nothing in that case;
    looking up the canonical name first recovers history.

    Returns `(None, None)` when the file isn't in git (e.g., newly
    created, or the vault isn't a repo).
    """
    try:
        rel = file_path.relative_to(vault_root.parent)
    except ValueError:
        return None, None
    repo = str(vault_root.parent)

    def _log(path_arg: str) -> list[str]:
        r = subprocess.run(
            ["git", "log", "--follow", "--format=%aI", "--", path_arg],
            cwd=repo, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip().splitlines() if r.returncode == 0 else []

    try:
        lines = _log(str(rel))
        if not lines:
            r = subprocess.run(
                ["git", "ls-files", "--", str(rel.parent)],
                cwd=repo, capture_output=True, text=True, timeout=5,
            )
            stem_lower = rel.name.lower()
            for tracked in r.stdout.splitlines():
                if tracked.lower().endswith(stem_lower):
                    lines = _log(tracked)
                    if lines:
                        break
        if not lines:
            return None, None
        return lines[-1].split("T")[0], lines[0].split("T")[0]
    except (subprocess.TimeoutExpired, OSError):
        return None, None
