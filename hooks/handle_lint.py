#!/usr/bin/env python3
"""
handle-lint — detector for un-expanded project taxonomy handles.

Shared engine behind three fronts:
  - full-corpus audit          (no args)          — dry-run report over the canon vault
  - diff-scoped file gate       (--diff [repo])    — changed *.md only, for a commit/CI check
  - text scan                  (import scan_text)  — one message/document, for Stop / write hooks

Detects the ce-plan / Surge identifier family in Markdown/prose and flags each
FIRST use in a document that lacks an adjacent expansion AND has no end-glossary.
Stable handles (ADR/BC/CD) resolve to their real title from on-disk tables; the
plan-local family (U/F/R/AE/KTD/KD) is detection-only.

Engine is generic; everything project-specific lives in CONFIG (config-not-fork).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- CONFIG ----
CONFIG = {
    "families": {
        "ADR": r"ADR-(\d{1,4})",   # stable — authoritative table on disk
        "BC":  r"BC-(\d{1,3})",
        "CD":  r"CD-?(\d{1,3})",
        "SD":  r"SD-?(\d{1,3})",
        "U":   r"U(\d{1,3})",      # plan-local — meaning lives in the owning plan
        "F":   r"F(\d{1,3})",
        "R":   r"R(\d{1,3})",
        "AE":  r"AE(\d{1,3})",
        "KTD": r"KTD-?(\d{1,3})",
        "KD":  r"KD-?(\d{1,3})",
    },
    "stable": {"ADR", "BC", "CD", "SD"},
    "inline_window": 45,
    # a line that (after optional markdown markers) starts with a glossary-ish
    # word = author used the sanctioned end-glossary form → whole doc satisfied.
    "glossary_re": re.compile(
        r"(?im)^\s{0,3}[#>\-*_ ]{0,6}"
        r"(glossary|legend|acronyms?|abbreviations?|expansions?)\b"
    ),
    "corpus": Path.home() / "vaults/workspace/surge.easygo.io/Projects/Surge",
    "adr_index": "40_decisions/adr/ADR-000-index.md",
    "bc_card_dir": "30_architecture/reference/bounded-contexts",
    "cd_card_dir": "30_architecture/reference/code-design",
    "skip_globs": [
        "40_decisions/adr/ADR-000-index.md",
        "30_architecture/reference/bounded-contexts/*",
        "30_architecture/reference/code-design/*",
        "10_product/domain-model/*",
    ],
}

# Only treat a handle as such when it is NOT glued to a longer word — this alone
# kills the bulk of plan-local (U8 / R2) false positives.
HANDLE_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(
        f"(?P<{fam}>{pat})" for fam, pat in CONFIG["families"].items()
    ) + r")(?![A-Za-z0-9])"
)

_TABLES = None  # lazy singleton so repeated hook invocations reuse one load


def load_tables(cfg=CONFIG):
    global _TABLES
    if _TABLES is not None:
        return _TABLES
    tables = {"ADR": {}, "BC": {}, "CD": {}, "SD": {}}
    root = cfg["corpus"]
    idx = root / cfg["adr_index"]
    if idx.exists():
        for m in re.finditer(r"\[ADR-(\d+)\]\([^)]*\)\s*\|\s*([^|]+?)\s*\|", idx.read_text()):
            tables["ADR"][int(m.group(1))] = m.group(2).strip()

    def cards(dirname, fam, prefix):
        d = root / dirname
        if not d.exists():
            return
        for f in d.glob(f"{prefix}-*.card.md"):
            m = re.match(rf"{prefix}-0*(\d+)-(.+)\.card", f.name)
            if m:
                tables[fam][int(m.group(1))] = m.group(2).replace("-", " ").title()

    cards(cfg["bc_card_dir"], "BC", "bc")
    cards(cfg["cd_card_dir"], "CD", "cd")
    _TABLES = tables
    return tables


def prose_spans(text):
    """Blank out non-prose regions so handles there don't count: frontmatter,
    fenced/inline code, markdown link+path targets, and filename-glued handles."""
    masked = list(text)

    def blank(a, b):
        for i in range(a, b):
            if masked[i] != "\n":
                masked[i] = "\x00"

    if text.startswith("---\n"):
        end = text.find("\n---", 3)
        if end != -1:
            blank(0, end + 4)
    for m in re.finditer(r"```.*?```", text, re.DOTALL):
        blank(*m.span())
    for m in re.finditer(r"`[^`\n]+`", text):
        blank(*m.span())
    for m in re.finditer(r"\]\([^)]*\)", text):
        blank(*m.span())
    for m in re.finditer(r"[A-Za-z]*[-/][A-Za-z0-9]*\d+-[A-Za-z][\w-]*", text):
        blank(*m.span())
    return "".join(masked)


def scan_text(raw, tables=None, cfg=CONFIG):
    """Core detector. Returns a list of findings for one document/message."""
    tables = tables or load_tables(cfg)
    prose = prose_spans(raw)
    has_glossary = bool(cfg["glossary_re"].search(raw))
    seen, findings = set(), []
    for m in HANDLE_RE.finditer(prose):
        fam = next(f for f in cfg["families"] if m.group(f) is not None)
        num = int(re.search(r"\d+", m.group()).group())
        key = (fam, num)
        if key in seen:
            continue
        seen.add(key)
        tail = raw[m.end(): m.end() + cfg["inline_window"]]
        if re.match(r"\s*\(", tail) or re.match(r"\s*[—-]\s*\S", tail) or has_glossary:
            continue
        suggestion = tables.get(fam, {}).get(num) if fam in cfg["stable"] else None
        findings.append({
            "handle": m.group(),
            "family": fam,
            "line": raw.count("\n", 0, m.start()) + 1,
            "kind": "stable" if fam in cfg["stable"] else "plan-local",
            "suggestion": suggestion,
            "resolvable": suggestion is not None,
        })
    return findings


def scan_file(path, tables=None, cfg=CONFIG):
    return scan_text(Path(path).read_text(errors="replace"), tables, cfg)


def in_skip(rel, cfg=CONFIG):
    return any(Path(rel).match(g) for g in cfg["skip_globs"])  # .match is right-anchored


# --------------------------------------------------------------- CLI modes --
def changed_md(repo):
    def git(*a):
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
        return r.stdout.splitlines() if r.returncode == 0 else []
    files = set()
    files |= set(git("diff", "--name-only", "--diff-filter=ACM", "HEAD"))
    files |= set(git("diff", "--name-only", "--cached", "--diff-filter=ACM"))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in files if f.endswith(".md"))


def full_scan(cfg=CONFIG):
    root, scope = cfg["corpus"], (Path(sys.argv[1]) if len(sys.argv) > 1 else cfg["corpus"])
    tables = load_tables(cfg)
    findings, scanned, flagged = [], 0, 0
    for path in sorted(scope.rglob("*.md")):
        rel = path.relative_to(root) if (root in path.parents or path == root) else path
        if in_skip(str(rel), cfg):
            continue
        scanned += 1
        fnd = scan_file(path, tables, cfg)
        if fnd:
            flagged += 1
            for f in fnd:
                f["file"] = str(rel); findings.append(f)
    out = Path(__file__).with_name("handle_lint_findings.jsonl")
    out.write_text("\n".join(json.dumps(f) for f in findings))
    by_fam = {}
    for f in findings:
        by_fam[f["family"]] = by_fam.get(f["family"], 0) + 1
    print(f"tables loaded: ADR={len(tables['ADR'])} BC={len(tables['BC'])} CD={len(tables['CD'])}")
    print(f"scanned {scanned} md  |  flagged {flagged} files  |  {len(findings)} findings")
    print("by family: " + "  ".join(f"{k}={v}" for k, v in sorted(by_fam.items())))
    print(f"auto-resolvable: {sum(f['resolvable'] for f in findings)}  ->  {out}")


def diff_scan(repo, cfg=CONFIG):
    tables = load_tables(cfg)
    total = 0
    for rel in changed_md(repo):
        if in_skip(rel, cfg):
            continue
        path = repo / rel
        if not path.exists():
            continue
        fnd = scan_file(path, tables, cfg)
        if not fnd:
            continue
        if total == 0:
            print("\n\033[33m⚠ handle-lint\033[0m — un-expanded taxonomy handles on first use:\n",
                  file=sys.stderr)
        print(f"  \033[1m{rel}\033[0m", file=sys.stderr)
        for f in fnd:
            hint = f"  → {f['suggestion']}" if f["suggestion"] else "  → plan-local: expand from the owning plan"
            print(f"    L{f['line']:<4} {f['handle']:<7}{hint}", file=sys.stderr)
        total += len(fnd)
    if total:
        print(f"\n  {total} handle(s) — warning only, not blocked.\n", file=sys.stderr)
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "--diff":
        sys.exit(diff_scan(Path(args[1]) if len(args) > 1 else Path.cwd()))
    full_scan()


if __name__ == "__main__":
    main()
