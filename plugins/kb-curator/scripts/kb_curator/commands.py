"""CLI command handlers — one `cmd_<verb>` per subcommand.

Each handler takes `(args, taxonomy)` and returns an exit code. Mechanical
helpers live in sibling modules (`derivation`, `links`, `frontmatter`).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .derivation import (
    canonical_filename,
    classify_text,
    derive_kind,
    derive_placement,
    git_dates,
)
from .frontmatter import (
    FM_DELIM,
    Note,
    _first_h1,
    _split_frontmatter,
    walk_notes,
)
from .links import find_wiki_links, levenshtein
from .model import Taxonomy
from .slugs import is_slug, kebab, slugify
from .yaml_io import yaml_dump_frontmatter

# Regex shared by audit/emoji helpers — strips any Unicode emoji prefix.
_LEADING_EMOJI_RE = re.compile(
    r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    r"\U0001F900-\U0001F9FF⌀-⏿⬀-⯿]+\s*"
)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace, tax: Taxonomy) -> int:
    notes = walk_notes(tax)
    if args.json:
        payload = {
            "vault_root": str(tax.vault_root),
            "note_count": len(notes),
            "notes": [
                {
                    "path": str(n.rel_path),
                    "title": n.frontmatter.get("title"),
                    "category": n.frontmatter.get("category"),
                    "tags": n.frontmatter.get("tags") or [],
                    "h1": n.h1,
                    "has_frontmatter": n.has_frontmatter,
                }
                for n in notes
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    by_area: dict[str, list[Note]] = defaultdict(list)
    for n in notes:
        area = tax.area_for_path(n.rel_path)
        by_area[area.slug if area else "unmapped"].append(n)

    print(f"Vault: {tax.vault_root}")
    print(f"Total notes: {len(notes)}\n")
    print(f"{'Area':<35} {'Notes':>6}")
    print("-" * 44)
    for slug, group in sorted(by_area.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"{slug:<35} {len(group):>6}")
    print()
    tag_counts = Counter(t for n in notes for t in (n.frontmatter.get("tags") or []))
    print(f"Distinct tags: {len(tag_counts)}")
    print(f"Singleton tags: {sum(1 for _, c in tag_counts.items() if c == 1)}")
    return 0


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def cmd_audit(args: argparse.Namespace, tax: Taxonomy) -> int:
    notes = walk_notes(tax)
    findings: list[tuple[str, str, str]] = []
    cats = tax.categories()
    tag_counts = Counter(t for n in notes for t in (n.frontmatter.get("tags") or []))

    # Every non-root directory containing notes should have a README index.
    dirs_with_md = {n.path.parent for n in notes}
    readme_paths = {n.path.parent for n in notes if n.path.name == "README.md"}
    for d in sorted(dirs_with_md):
        if d == tax.vault_root: continue
        if d not in readme_paths:
            findings.append(("warn", str(d.relative_to(tax.vault_root)),
                              "directory missing README.md"))

    for n in notes:
        rel = str(n.rel_path)
        if not n.has_frontmatter:
            findings.append(("error", rel, "missing frontmatter"))
            continue
        fm = n.frontmatter

        for req in tax.required_fm:
            if req not in fm or fm[req] in (None, "", []):
                findings.append(("error", rel, f"frontmatter missing required field: {req}"))

        cat = fm.get("category")
        if cat and cat not in cats:
            findings.append(("error", rel, f"category '{cat}' not in taxonomy"))
        if cat and not is_slug(str(cat), tax.slug_case):
            findings.append(("error", rel, f"category '{cat}' is not {tax.slug_case}-case"))

        expected_area = tax.area_for_path(n.rel_path)
        if expected_area and cat and cat != expected_area.slug:
            findings.append(("warn", rel,
                              f"path implies category '{expected_area.slug}', frontmatter says '{cat}'"))

        tags = fm.get("tags") or []
        if not isinstance(tags, list):
            findings.append(("error", rel, "tags is not a list"))
        else:
            for t in tags:
                if not is_slug(str(t), tax.slug_case):
                    findings.append(("error", rel, f"tag '{t}' is not {tax.slug_case}-case"))
            if tax.rules.get("first_tag_mirrors_category") and tags and cat and tags[0] != cat:
                findings.append(("warn", rel,
                                  f"first tag '{tags[0]}' should mirror category '{cat}'"))
            extra = tax.rules.get("readme_extra_tag")
            if n.path.name == "README.md" and extra and extra not in tags:
                findings.append(("warn", rel, f"README missing '{extra}' tag"))

        if n.h1 and fm.get("title"):
            h1_stripped = _LEADING_EMOJI_RE.sub("", n.h1)
            if h1_stripped != fm.get("title") and n.h1 != fm.get("title"):
                findings.append(("info", rel,
                                  f"H1 '{n.h1}' differs from frontmatter title '{fm.get('title')}'"))

    for tag, count in tag_counts.items():
        if count == 1 and tag not in tax.controlled_tags:
            findings.append(("info", "(vault)", f"singleton tag: '{tag}'"))

    severities = Counter(s for s, _, _ in findings)

    if args.json:
        print(json.dumps({
            "findings": [{"severity": s, "path": p, "message": m} for s, p, m in findings],
            "summary": dict(severities),
        }, indent=2, ensure_ascii=False))
        return 1 if severities["error"] else 0

    print(f"Audit: {len(findings)} findings "
          f"(errors={severities['error']}, warnings={severities['warn']}, info={severities['info']})\n")
    for sev in ("error", "warn", "info"):
        rows = [f for f in findings if f[0] == sev]
        if not rows: continue
        print(f"[{sev.upper()}] {len(rows)}")
        for _, path, msg in rows[:50]:
            print(f"  {path}\n    → {msg}")
        if len(rows) > 50:
            print(f"  …and {len(rows) - 50} more")
        print()
    return 1 if severities["error"] else 0


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

def cmd_classify(args: argparse.Namespace, tax: Taxonomy) -> int:
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    _, body = _split_frontmatter(text)
    suggestion = classify_text(body, path.stem, tax)
    if args.json:
        print(json.dumps(suggestion, indent=2, ensure_ascii=False))
        return 0
    print(f"Suggested category : {suggestion['category']}")
    print(f"  central question : {suggestion['central_question']}")
    print(f"  pillar           : {suggestion['pillar']}")
    print(f"  target directory : {suggestion['target_dir']}")
    print(f"Suggested tags     : {', '.join(suggestion['tags']) or '(none)'}")
    print(f"Match score        : {suggestion['score']:.2f}")
    if suggestion["alternates"]:
        print("Alternates:")
        for alt in suggestion["alternates"]:
            print(f"  - {alt['category']} (score {alt['score']:.2f})")
    if suggestion["score"] < 1.5:
        print("\nNote: weak match. Consider Workflow D (propose taxonomy extension).")
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def cmd_apply(args: argparse.Namespace, tax: Taxonomy) -> int:
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _split_frontmatter(text)
    fm = dict(fm or {})
    h1 = _first_h1(body)
    cats = tax.categories()

    if args.category:
        cat = slugify(args.category, tax.slug_case)
        if cat not in cats:
            print(f"error: category '{cat}' not in taxonomy", file=sys.stderr)
            return 2
        fm["category"] = cat
    elif "category" not in fm:
        try:
            rel = path.relative_to(tax.vault_root)
            area = tax.area_for_path(rel)
            if area:
                fm["category"] = area.slug
        except ValueError:
            pass

    if args.title:
        fm["title"] = args.title
    elif "title" not in fm:
        fm["title"] = h1 or path.stem

    if args.tags:
        fm["tags"] = [slugify(t, tax.slug_case) for t in args.tags.split(",") if t.strip()]
    else:
        tags = [slugify(str(t), tax.slug_case) for t in (fm.get("tags") or [])]
        if tax.rules.get("first_tag_mirrors_category") and fm.get("category"):
            if not tags or tags[0] != fm["category"]:
                tags = [fm["category"]] + [t for t in tags if t != fm["category"]]
        if path.name == "README.md":
            extra = tax.rules.get("readme_extra_tag")
            if extra and extra not in tags:
                tags.append(extra)
        fm["tags"] = tags

    new_yaml = yaml_dump_frontmatter(fm)
    new_text = f"{FM_DELIM}\n{new_yaml}\n{FM_DELIM}\n\n{body.lstrip()}"
    if args.dry_run:
        print("--- proposed frontmatter ---")
        print(FM_DELIM); print(new_yaml); print(FM_DELIM)
        return 0
    path.write_text(new_text, encoding="utf-8")
    print(f"updated: {path}")
    return 0


# ---------------------------------------------------------------------------
# taxonomy show / refresh / init
# ---------------------------------------------------------------------------

def cmd_taxonomy(args: argparse.Namespace, tax: Taxonomy) -> int:
    if args.taxonomy_action == "init":
        return _taxonomy_init(args, tax)
    if args.taxonomy_action == "show":
        for p in tax.pillars:
            print(f"{p.path}   [{p.slug}]")
            print(f"  ? {p.central_question}")
            for a in p.areas:
                print(f"    {a.path:<30}  [{a.slug}]")
                if a.central_question:
                    print(f"      ? {a.central_question}")
            print()
        return 0
    if args.taxonomy_action == "refresh":
        observed = _observed_structure(tax)
        diff = _diff_taxonomy(tax, observed)
        if not diff["new_areas"] and not diff["new_pillars"]:
            print("Taxonomy is up to date with the filesystem.")
            return 0
        print("Proposed taxonomy additions (dry-run):")
        for pn in diff["new_pillars"]:
            print(f"  + pillar: {pn}")
        for pp, ap in diff["new_areas"]:
            print(f"  + area: {pp} / {ap}  (slug: {slugify(ap, tax.slug_case)})")
        if not args.apply:
            print("\nRe-run with --apply to write changes (manual edit may still be preferable).")
            return 0
        print("\nApply not implemented for automatic writes — please edit taxonomy.yaml manually.")
        return 0
    print("error: unknown taxonomy action", file=sys.stderr)
    return 2


def _observed_structure(tax: Taxonomy) -> dict[str, list[str]]:
    """Return `{pillar_dir: [area_dir,…]}` as observed on disk."""
    out: dict[str, list[str]] = {}
    root = tax.vault_root
    pillar_re = re.compile(tax.path_conventions.get("pillar_pattern", r"^\d{2}-(.+)$"))
    for pillar_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if pillar_dir.name in tax.exclude_dirs or not pillar_re.match(pillar_dir.name):
            continue
        areas = [a.name for a in sorted(pillar_dir.iterdir())
                 if a.is_dir() and a.name not in tax.exclude_dirs]
        out[pillar_dir.name] = areas
    return out


def _diff_taxonomy(tax: Taxonomy, observed: dict[str, list[str]]) -> dict:
    declared = {p.path: {a.path for a in p.areas} for p in tax.pillars}
    new_pillars: list[str] = []
    new_areas: list[tuple[str, str]] = []
    for pillar_dir, areas in observed.items():
        if pillar_dir not in declared:
            new_pillars.append(pillar_dir)
            new_areas.extend((pillar_dir, a) for a in areas)
            continue
        new_areas.extend((pillar_dir, a) for a in areas if a not in declared[pillar_dir])
    return {"new_pillars": new_pillars, "new_areas": new_areas}


def _taxonomy_init(args: argparse.Namespace, tax: Taxonomy) -> int:
    """Auto-discover vault structure and emit a starter taxonomy.yaml."""
    root = Path(args.root or tax.vault_root).expanduser().resolve()
    if not root.exists():
        print(f"error: vault root does not exist: {root}", file=sys.stderr)
        return 2

    pattern = args.pillar_pattern or tax.path_conventions.get("pillar_pattern", r"^\d{2}-(.+)$")
    pillar_re = re.compile(pattern)
    exclude = set(tax.exclude_dirs) | {".git"}

    discovered: list[dict] = []
    for pillar_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if pillar_dir.name in exclude: continue
        m = pillar_re.match(pillar_dir.name)
        if not m: continue
        pname = m.group(1) if m.groups() else pillar_dir.name
        pillar = {
            "slug": slugify(pname, tax.slug_case),
            "path": pillar_dir.name,
            "central_question": f"TODO: one sentence describing {pname}.",
            "areas": [],
        }
        for area_dir in sorted(p for p in pillar_dir.iterdir() if p.is_dir()):
            if area_dir.name in exclude: continue
            pillar["areas"].append({
                "slug": slugify(area_dir.name, tax.slug_case),
                "path": area_dir.name,
                "central_question": f"TODO: one sentence describing {area_dir.name}.",
            })
        discovered.append(pillar)

    tag_counts: Counter[str] = Counter()
    for md in root.rglob("*.md"):
        try:
            rel = md.relative_to(root)
        except ValueError:
            continue
        if any(part in exclude for part in rel.parts): continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = _split_frontmatter(text)
        for t in (fm or {}).get("tags") or []:
            if isinstance(t, str):
                tag_counts[t] += 1
    starter_tags = [t for t, c in tag_counts.most_common() if c >= 2][:50]

    out = _emit_starter_yaml(root, discovered, starter_tags, tax.slug_case)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}  (pillars: {len(discovered)}, "
              f"areas: {sum(len(p['areas']) for p in discovered)}, "
              f"starter tags: {len(starter_tags)})")
    else:
        sys.stdout.write(out)
    return 0


def _emit_starter_yaml(root: Path, pillars: list[dict],
                       starter_tags: list[str], slug_case: str) -> str:
    """Hand-emit a starter taxonomy.yaml — human-friendly, not YAML lib output."""
    lines: list[str] = [
        "# Auto-generated by `kb_curator.py taxonomy init`.",
        "# Review the central_question lines (TODO) and edit before use.",
        "",
        "vault:",
        f'  root: "{root}"',
        "  exclude_dirs: [zAttachments, .obsidian, .trash, .git]",
        "",
        "path_conventions:",
        r"  pillar_pattern: '^\d{2}-(.+)$'",
        r"  sub_area_pattern: '^\d{2}-(.+)$'",
        "  readme_filename: README.md",
        "",
        "link_syntax:",
        "  type: obsidian   # obsidian | markdown | none",
        "",
        f"slug_case: {slug_case}",
        "",
        "dates:",
        "  source: git      # git | mtime | none",
        "",
        "frontmatter:",
        "  required: [title, category, tags]",
        "  derived: [pillar, sub_area, topic, kind, created, updated]",
        "  optional: [aliases]",
        "  rules:",
        "    first_tag_mirrors_category: true",
        "    readme_extra_tag: index",
        "  kind_from_tag: [index, crash-course, deep-dive, reference]",
        "",
        "pillars:",
    ]
    for p in pillars:
        lines.append(f"  - slug: {p['slug']}")
        lines.append(f'    path: "{p["path"]}"')
        lines.append(f'    central_question: "{p["central_question"]}"')
        if p["areas"]:
            lines.append("    areas:")
            for a in p["areas"]:
                lines.append(f"      - slug: {a['slug']}")
                lines.append(f'        path: "{a["path"]}"')
                lines.append(f'        central_question: "{a["central_question"]}"')
        else:
            lines.append("    areas: []")
        lines.append("")
    lines.append("# Starter tag vocabulary — tags observed at least twice in the vault.")
    lines.append("# Curate: drop noise, promote useful cross-cutting tags.")
    lines.append("tags:")
    if starter_tags:
        for t in starter_tags:
            lines.append(f"  - {t}")
    else:
        lines.append("  []")
    lines.extend([
        "",
        "# Optional: emoji sigil per category for `emojis apply`.",
        "emojis: {}",
        "",
        "# Optional: content keyword → tag inference for `tags suggest`.",
        "inference_rules: []",
        "",
        "# Optional: filename conventions for `naming check` / `rename`.",
        "naming:",
        "  replace_underscores: true",
        "  strip_emoji_prefix: true",
        "  max_length: 120",
    ])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# links check / repair
# ---------------------------------------------------------------------------

_ATTACHMENT_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf",
                    ".webp", ".mp4", ".mp3"}


def cmd_links(args: argparse.Namespace, tax: Taxonomy) -> int:
    if tax.link_syntax == "none":
        print("Link checking is disabled in config (link_syntax.type = none).")
        return 0
    notes = walk_notes(tax)
    index = {n.path.stem: n for n in notes}
    attachments: set[str] = set()
    for f in tax.vault_root.rglob("*"):
        if f.is_file() and f.suffix.lower() in _ATTACHMENT_EXTS:
            attachments.add(f.stem)
            attachments.add(f.name)

    broken: list[dict] = []
    for n in notes:
        for target, _disp in find_wiki_links(n.body, tax.link_syntax):
            target = target.rstrip("\\")
            if target in index: continue
            tail = target.rsplit("/", 1)[-1]
            if tail in index: continue
            if tail in attachments or target in attachments: continue
            if any(target.lower().endswith(ext) for ext in _ATTACHMENT_EXTS):
                continue
            # Closest stem match by Levenshtein distance
            candidates = sorted(
                ((levenshtein(target.lower(), stem.lower()), stem) for stem in index),
                key=lambda t: t[0],
            )
            candidates = [c for c in candidates if c[0] <= max(2, len(target) // 6)]
            broken.append({
                "source": str(n.rel_path),
                "target": target,
                "suggestions": [c[1] for c in candidates[:3]],
            })

    if args.json:
        print(json.dumps({"broken": broken, "count": len(broken)}, indent=2, ensure_ascii=False))
        return 0 if not broken else 1
    if not broken:
        print(f"All wiki-links resolve ({len(notes)} notes scanned).")
        return 0
    print(f"Broken wiki-links: {len(broken)}\n")
    if args.action == "check":
        for b in broken:
            sug = ", ".join(b["suggestions"]) if b["suggestions"] else "(no close match)"
            print(f"  {b['source']}\n    [[{b['target']}]] -> {sug}")
        return 1

    # repair
    repaired, skipped = 0, 0
    for b in broken:
        if not b["suggestions"]:
            skipped += 1; continue
        if len(b["suggestions"]) > 1 and not args.aggressive:
            skipped += 1; continue
        replacement = b["suggestions"][0]
        path = tax.vault_root / b["source"]
        text = path.read_text(encoding="utf-8", errors="replace")
        pat = re.compile(
            r"\[\[" + re.escape(b["target"]) +
            r"((?:#[^\[\]|]+)?(?:\|[^\[\]]+)?)\]\]"
        )
        new_text, n_sub = pat.subn(f"[[{replacement}\\1]]", text)
        if n_sub:
            if not args.dry_run:
                path.write_text(new_text, encoding="utf-8")
            repaired += n_sub
            print(f"  {'(dry-run) ' if args.dry_run else ''}{b['source']}: "
                  f"[[{b['target']}]] -> [[{replacement}]]")
    print()
    print(f"Repairs: {repaired}, skipped (ambiguous/no-match): {skipped}")
    return 0


# ---------------------------------------------------------------------------
# naming check / rename
# ---------------------------------------------------------------------------

def cmd_naming(args: argparse.Namespace, tax: Taxonomy) -> int:
    naming = tax.naming
    notes = walk_notes(tax)
    issues: list[dict] = []
    for n in notes:
        if n.path.name == "README.md": continue
        if n.path.name.endswith(".excalidraw.md"): continue
        title = n.frontmatter.get("title") or n.h1 or n.path.stem
        proposed = canonical_filename(title, naming)
        if proposed and proposed != n.path.stem:
            issues.append({
                "path": str(n.rel_path),
                "current_stem": n.path.stem,
                "proposed_stem": proposed,
                "title": title,
            })

    if args.json:
        print(json.dumps({"issues": issues, "count": len(issues)}, indent=2, ensure_ascii=False))
        return 0

    if args.action == "check":
        print(f"Files whose name differs from their title (proposed renames): {len(issues)}")
        for i in issues[:50]:
            print(f"  {i['path']}\n    {i['current_stem']!r} -> {i['proposed_stem']!r}")
        if len(issues) > 50:
            print(f"  …and {len(issues) - 50} more")
        return 0

    # rename
    if not args.path:
        print("error: rename requires --path <file>", file=sys.stderr)
        return 2
    src = Path(args.path).resolve()
    if not src.exists():
        print(f"error: not found: {src}", file=sys.stderr)
        return 2
    text = src.read_text(encoding="utf-8", errors="replace")
    fm, _ = _split_frontmatter(text)
    title = (fm or {}).get("title") or src.stem
    new_stem = canonical_filename(title, naming)
    if new_stem == src.stem:
        print("Filename already canonical.")
        return 0
    new_path = src.with_name(new_stem + src.suffix)
    print(f"Rename: {src.name!r} -> {new_path.name!r}")
    if args.dry_run:
        return 0

    # Rewrite inbound + self-references *before* the rename. Self-refs would
    # become broken otherwise since walk_notes finds the source file under
    # its old name and writes back to that path before the rename happens.
    # Two link shapes both resolve to a note by stem in Obsidian:
    #   [[Stem]]             — bare
    #   [[Folder/Sub/Stem]]  — path-style; we match either form.
    old_stem = src.stem
    pat = re.compile(
        r"\[\[((?:[^\[\]|#]+/)?)" + re.escape(old_stem) +
        r"((?:#[^\[\]|]+)?(?:\|[^\[\]]+)?)\]\]"
    )
    updated = 0
    for n in walk_notes(tax):
        new_body = pat.sub(f"[[\\1{new_stem}\\2]]", n.body)
        if new_body != n.body:
            yaml_block = yaml_dump_frontmatter(n.frontmatter)
            out = f"{FM_DELIM}\n{yaml_block}\n{FM_DELIM}\n\n{new_body.lstrip()}"
            n.path.write_text(out, encoding="utf-8")
            updated += 1

    # Case-only renames (`channels.md` → `Channels.md`) need a two-step on
    # case-insensitive filesystems (HFS+/APFS default, NTFS). A direct
    # `rename` is either a no-op (Path.resolve() reports identical paths)
    # or collides. Bouncing through a dot-prefixed temp filename forces
    # the rename to register at the inode level.
    if new_path.exists() and new_path.resolve() == src.resolve():
        tmp = src.with_name(f".__kbcur_tmp_{src.name}")
        src.rename(tmp)
        tmp.rename(new_path)
    else:
        src.rename(new_path)
    print(f"  renamed file; updated {updated} inbound link source(s).")
    return 0


# ---------------------------------------------------------------------------
# emojis apply
# ---------------------------------------------------------------------------

def cmd_emojis(args: argparse.Namespace, tax: Taxonomy) -> int:
    emap = tax.emojis
    if not emap:
        print("No emoji mappings configured. Add an 'emojis:' section to taxonomy.yaml.")
        return 0
    written = 0
    for n in walk_notes(tax):
        emoji = emap.get(n.frontmatter.get("category"))
        if not emoji: continue
        body_lines = n.body.splitlines()
        changed = False
        for i, ln in enumerate(body_lines):
            if not ln.startswith("# "): continue
            title_text = ln[2:].lstrip()
            if _LEADING_EMOJI_RE.match(title_text):
                break  # already has a sigil
            body_lines[i] = f"# {emoji} {title_text}"
            changed = True
            break
        if changed:
            new_body = "\n".join(body_lines)
            yaml_block = yaml_dump_frontmatter(n.frontmatter)
            out = f"{FM_DELIM}\n{yaml_block}\n{FM_DELIM}\n\n{new_body.lstrip()}"
            if not args.dry_run:
                n.path.write_text(out, encoding="utf-8")
            written += 1
            if args.dry_run:
                print(f"  (dry-run) {n.rel_path}: + {emoji}")
    print(f"Files {'(would be) ' if args.dry_run else ''}updated: {written}")
    return 0


# ---------------------------------------------------------------------------
# tags suggest
# ---------------------------------------------------------------------------

def cmd_tags(args: argparse.Namespace, tax: Taxonomy) -> int:
    """Keyword-rule-based tag suggestions for a single file."""
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"error: not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _split_frontmatter(text)
    title = (fm or {}).get("title", path.stem)
    haystack = f"{title}\n{body}".lower()
    current_tags = set((fm or {}).get("tags") or [])
    suggestions: list[str] = []
    for rule in tax.inference_rules:
        tag = rule["tag"]
        if tag in current_tags: continue
        for kw in rule.get("keywords") or []:
            if kw.lower() in haystack:
                suggestions.append(tag); break

    if args.json:
        print(json.dumps({"suggestions": suggestions, "current": sorted(current_tags)}, indent=2))
        return 0
    print(f"Current tags: {sorted(current_tags) or '(none)'}")
    print(f"Suggested additions: {suggestions or '(none)'}")
    if args.apply and suggestions:
        merged = list((fm or {}).get("tags") or []) + [s for s in suggestions if s not in current_tags]
        new_fm = dict(fm or {})
        new_fm["tags"] = merged
        yaml_block = yaml_dump_frontmatter(new_fm)
        out = f"{FM_DELIM}\n{yaml_block}\n{FM_DELIM}\n\n{body.lstrip()}"
        path.write_text(out, encoding="utf-8")
        print(f"Applied: {[s for s in suggestions if s not in current_tags]}")
    return 0


# ---------------------------------------------------------------------------
# themes detect
# ---------------------------------------------------------------------------

def cmd_themes(args: argparse.Namespace, tax: Taxonomy) -> int:
    """Cluster co-occurring tags into proposed themes (union-find on pairs).

    Why exclude `index`/`reference` and category slugs: those tags are
    *universal connectors* — they co-occur with everything. Including them
    collapses the whole graph into a single super-cluster and hides the
    real cross-cutting themes (`performance`, `security`, `reliability`).
    """
    universal = {"index", "reference"}
    excluded = universal | set(tax.categories().keys())

    pair_counts: Counter[tuple[str, str]] = Counter()
    tag_counts: Counter[str] = Counter()
    for n in walk_notes(tax):
        tags = [t for t in (n.frontmatter.get("tags") or [])
                if isinstance(t, str) and t not in excluded]
        for t in tags:
            tag_counts[t] += 1
        for i, a in enumerate(tags):
            for b in tags[i + 1:]:
                pair_counts[tuple(sorted((a, b)))] += 1

    threshold = args.min_cooccurrence
    strong = [(p, c) for p, c in pair_counts.items() if c >= threshold]
    strong.sort(key=lambda x: -x[1])

    parent: dict[str, str] = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for (a, b), _ in strong:
        parent.setdefault(a, a); parent.setdefault(b, b)
        union(a, b)

    clusters: dict[str, list[str]] = defaultdict(list)
    for tag in parent:
        clusters[find(tag)].append(tag)
    cluster_list = [sorted(tags) for tags in clusters.values() if len(tags) >= 3]
    cluster_list.sort(key=lambda c: -sum(tag_counts[t] for t in c))

    if args.json:
        print(json.dumps({"clusters": cluster_list, "threshold": threshold}, indent=2))
        return 0
    print(f"Theme clusters (co-occurrence >= {threshold}, size >= 3):")
    for c in cluster_list:
        counts = ", ".join(f"{t}({tag_counts[t]})" for t in c)
        print(f"  • {counts}")
    if not cluster_list:
        print("  (none above threshold)")
    return 0


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------

def cmd_enrich(args: argparse.Namespace, tax: Taxonomy) -> int:
    """Backfill pillar / sub_area / topic / kind / created / updated.

    Idempotent: only writes when a field would actually change.
    """
    notes = walk_notes(tax)
    only_path = Path(args.path).resolve() if args.path else None
    updated = 0
    for n in notes:
        if only_path and n.path.resolve() != only_path: continue
        new_fm = dict(n.frontmatter)

        for k, v in derive_placement(n.rel_path, tax).items():
            if args.force or k not in new_fm:
                new_fm[k] = v

        kind = derive_kind(new_fm, n.path)
        if kind and (args.force or "kind" not in new_fm):
            new_fm["kind"] = kind

        if not args.no_dates and tax.dates_source != "none":
            created, last = _dates_for(n.path, tax)
            if created and (args.force or "created" not in new_fm):
                new_fm["created"] = created
            if last and (args.force or "updated" not in new_fm or new_fm.get("updated") != last):
                new_fm["updated"] = last

        if new_fm == n.frontmatter:
            continue
        yaml_block = yaml_dump_frontmatter(new_fm)
        out = f"{FM_DELIM}\n{yaml_block}\n{FM_DELIM}\n\n{n.body.lstrip()}"
        if not args.dry_run:
            n.path.write_text(out, encoding="utf-8")
        updated += 1
    print(f"Notes {'(would be) ' if args.dry_run else ''}enriched: {updated}")
    return 0


def _dates_for(path: Path, tax: Taxonomy) -> tuple[str | None, str | None]:
    """Return `(created, updated)` per the configured source. Internal helper."""
    if tax.dates_source == "git":
        return git_dates(path, tax.vault_root)
    if tax.dates_source == "mtime":
        st = path.stat()
        last = _dt.date.fromtimestamp(st.st_mtime).isoformat()
        created = _dt.date.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime)).isoformat()
        return created, last
    return None, None
