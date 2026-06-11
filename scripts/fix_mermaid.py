#!/usr/bin/env python3
"""Escape markdown-triggering characters inside Mermaid blocks.

Inside ```mermaid ... ``` fences, replace:
- bare `*` (path/cron wildcards, glob patterns) → `#42;`
- `<number>. ` prefix at start of a label fragment → `<number>#46; `

Preserve valid Mermaid syntax:
- `[*]` (state diagram start/end)
- `*--`, `--*`, `o--*`, `*--o`, etc. (class diagram composition arrows)
- multiplicity inside quotes like `"0..*"`, `"1..*"`
"""
import re
import sys
from pathlib import Path

# Pattern: bare `*` that is NOT part of state syntax, composition arrow, or multiplicity
# Strategy: find every `*`, check context.

MERMAID_OPEN = re.compile(r'^```mermaid\b')
MERMAID_CLOSE = re.compile(r'^```\s*$')


def escape_in_mermaid_line(line: str) -> str:
    # Skip lines that are obviously class-diagram composition: `*--`, `--*`
    # We'll process char-by-char with lookaround context.
    out = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '*':
            # Check context
            prev2 = line[i - 2:i] if i >= 2 else ''
            prev1 = line[i - 1] if i >= 1 else ''
            next1 = line[i + 1] if i + 1 < n else ''
            next2 = line[i + 1:i + 3] if i + 2 < n else ''
            # [*] state
            if prev1 == '[' and next1 == ']':
                out.append(ch)
                i += 1
                continue
            # composition arrows: `*--`, `*..`, `o..*`, `--*`, `..*`, `o--*`
            if next2 == '--' or next2 == '..':
                out.append(ch)
                i += 1
                continue
            if prev2 == '--' or prev2 == '..':
                out.append(ch)
                i += 1
                continue
            # multiplicity inside quotes: ..* or "..*"
            # already handled by prev2 == '..' above
            # Escape
            out.append('#42;')
            i += 1
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


# Pattern to find `<digits>. ` at start of fragment (after `"`, `[`, `>`, space, or line start)
NUMDOT_RE = re.compile(r'(?<=[\s\[">])(\d+)\. ')
NUMDOT_LINESTART_RE = re.compile(r'^(\s*)(\d+)\. ')


def escape_numdot_in_mermaid_line(line: str) -> str:
    # Don't escape arrow labels like `-->|"1. text"|` outside; we operate inside mermaid only
    line = NUMDOT_LINESTART_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}#46; ', line)
    line = NUMDOT_RE.sub(lambda m: f'{m.group(1)}#46; ', line)
    return line


def process_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return False
    lines = text.split('\n')
    changed = False
    in_mermaid = False
    out_lines = []
    for line in lines:
        if not in_mermaid:
            if MERMAID_OPEN.match(line):
                in_mermaid = True
                out_lines.append(line)
                continue
            out_lines.append(line)
            continue
        # in mermaid
        if MERMAID_CLOSE.match(line):
            in_mermaid = False
            out_lines.append(line)
            continue
        new_line = escape_in_mermaid_line(line)
        new_line = escape_numdot_in_mermaid_line(new_line)
        if new_line != line:
            changed = True
        out_lines.append(new_line)
    if changed:
        path.write_text('\n'.join(out_lines), encoding='utf-8')
    return changed


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    targets = sys.argv[2:] if len(sys.argv) > 2 else None
    if targets:
        files = [Path(t) for t in targets]
    else:
        files = list(root.rglob('*.md'))
    modified = []
    for f in files:
        if process_file(f):
            modified.append(str(f))
    for m in modified:
        print(m)
    print(f'\n{len(modified)} files modified')


if __name__ == '__main__':
    main()
