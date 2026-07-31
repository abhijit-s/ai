#!/usr/bin/env python3
"""Fix characters inside Mermaid blocks that break rendering.

Inside ```mermaid ... ``` fences, this tool applies four fixes:

1. Escape bare `*` (path/cron wildcards, glob patterns) -> `#42;`
2. Escape a `<number>. ` prefix at the start of a label fragment -> `<number>#46; `
3. Replace a literal `\n` used to force a line break inside a label/note ->
   `<br>` (a literal backslash-n does not render as a line break)
4. Replace a bare `;` inside connector text, a node label, or a note block ->
   ` |` (a semicolon there breaks Mermaid's parser)

Fix 1 earns its place because Mermaid runs label text through a markdown
tokenizer and emits `<strong>`/`<em>` nodes, so two or more asterisks in one
label pair into emphasis and the asterisks are silently eaten - exactly the
multi-asterisk shape of a glob or cron label. A lone unpaired `*` is harmless.

Fix 4 is the only fix backed by a genuine grammar-level parse error; the
others defend against silent mangling.

No `#` fix. A bare `#` in a label renders correctly (verified 2026-07-30 on
Obsidian 1.13.4 / mermaid 11.13.0), so escaping it bought nothing - and an
earlier `# -> #35;` rule destroyed diagram colouring across ~97 files by
rewriting `fill:#bbf7d0` to `fill:#35;bbf7d0`. That damage could not be
undone by re-running the script, because `#35;bbf7d0` then reads as a valid
entity and is skipped; only a git-HEAD restore recovered it. Do not re-add it.

Preserved, never touched:
- `[*]` (state diagram start/end), `*--`/`--*`/`o--*` composition arrows,
  and `"0..*"`/`"1..*"` multiplicity (fix 1's existing exclusions)
- `classDef`/`class`/`style`/`linkStyle` statement lines - their trailing
  `;` is a legitimate Mermaid statement terminator, not label text
- a `;` that is part of an HTML numeric entity, e.g. the `#42;` fix 1 itself
  produces, or a pre-existing `#46;` / `#35;`
- anything after `%%` on a line (Mermaid comment - inert, never parsed)

Fix 3 and 4 track quote-open state *across* lines within a block (a
`note for X "..."` or bracket label can legitimately wrap several
physical lines) and recognize both single-line colon-labeled statements
(`A-->>B: text`, `S1 --> S2: text`) and multi-line `note over/left of/
right of X ... end note` blocks.

Usage:
    fix_mermaid.py <root> [file ...]

With no file args, walks <root> for all *.md files. Prints each modified
file and a final count. Always applies in place - no dry-run flag; use
git to review the diff before committing.
"""
import re
import sys
from pathlib import Path

MERMAID_OPEN = re.compile(r'^```mermaid\b')
MERMAID_CLOSE = re.compile(r'^```\s*$')

STATEMENT_PREFIXES = ('classDef ', 'class ', 'linkStyle ', 'style ')
CONNECTOR_TOKENS = ('-->>', '->>', '-->', '--x', '--)', '..>', '==>')
NOTE_PREFIX_RE = re.compile(r'^(note\s+(over|left of|right of))\b', re.IGNORECASE)
NOTE_BLOCK_START_RE = re.compile(r'^note\s+(over|left of|right of)\s+\S.*$', re.IGNORECASE)

# --- Fix 1: bare `*` -> `#42;` --------------------------------------------


def escape_in_mermaid_line(line: str) -> str:
    out = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '*':
            prev2 = line[i - 2:i] if i >= 2 else ''
            prev1 = line[i - 1] if i >= 1 else ''
            next1 = line[i + 1] if i + 1 < n else ''
            next2 = line[i + 1:i + 3] if i + 2 < n else ''
            if prev1 == '[' and next1 == ']':
                out.append(ch)
                i += 1
                continue
            if next2 == '--' or next2 == '..':
                out.append(ch)
                i += 1
                continue
            if prev2 == '--' or prev2 == '..':
                out.append(ch)
                i += 1
                continue
            out.append('#42;')
            i += 1
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


# --- Fix 2: `<digits>. ` -> `<digits>#46; ` -------------------------------

NUMDOT_RE = re.compile(r'(?<=[\s\[">])(\d+)\. ')
NUMDOT_LINESTART_RE = re.compile(r'^(\s*)(\d+)\. ')


def escape_numdot_in_mermaid_line(line: str) -> str:
    line = NUMDOT_LINESTART_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}#46; ', line)
    line = NUMDOT_RE.sub(lambda m: f'{m.group(1)}#46; ', line)
    return line


# --- Fix 3 + 4: literal `\n` -> `<br>`, bare `;` -> ` |` ------------------


def is_entity_semicolon(text: str, idx: int) -> bool:
    """True if the ';' at idx terminates a numeric-entity-looking token, e.g. '#42;'."""
    j = idx - 1
    digits = 0
    while j >= 0 and text[j].isdigit():
        digits += 1
        j -= 1
    return digits > 0 and j >= 0 and text[j] == '#'


def find_label_start(line: str):
    """For an unquoted diagram-statement line (sequence message / state transition /
    single-line note), return the index where free-text label content begins - right
    after the first ': ' on a line carrying a recognized connector token or a leading
    'note over/left of/right of'. Returns None if the line isn't such a statement."""
    stripped = line.lstrip()
    has_connector = any(tok in line for tok in CONNECTOR_TOKENS)
    is_note = bool(NOTE_PREFIX_RE.match(stripped))
    if not (has_connector or is_note):
        return None
    colon_idx = line.find(': ')
    if colon_idx == -1:
        colon_idx = line.rfind(':')
        if colon_idx == -1:
            return None
        return colon_idx + 1
    return colon_idx + 2


class BlockState:
    """Quote-open and note-block state carried across lines within one mermaid block."""

    def __init__(self):
        self.quote_open = False
        self.in_note_block = False


def fix_breaks_and_semicolons_in_line(line: str, state: 'BlockState') -> str:
    stripped = line.strip()

    if stripped.lower() == 'end note':
        state.in_note_block = False
        return line

    if not state.in_note_block and NOTE_BLOCK_START_RE.match(stripped) and ':' not in stripped:
        state.in_note_block = True
        return line

    if stripped.startswith(STATEMENT_PREFIXES):
        return line

    if '\\n' in line:
        line = line.replace('\\n', '<br>')

    if ';' not in line:
        return line

    comment_at = line.find('%%')
    label_start = 0 if state.in_note_block else find_label_start(line)

    chars = list(line)
    qopen = state.quote_open
    j = 0
    while j < len(chars):
        ch = chars[j]
        if ch == '"':
            qopen = not qopen
            j += 1
            continue
        if ch == ';' and not is_entity_semicolon(''.join(chars), j):
            in_comment = comment_at != -1 and j >= comment_at
            in_quote = qopen
            in_unquoted_label = (
                not in_quote and label_start is not None and j >= label_start and not in_comment
            )
            if in_quote or in_unquoted_label:
                chars[j] = ' |'
        j += 1
    state.quote_open = qopen
    return ''.join(chars)


# --- Driver -----------------------------------------------------------------


def process_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return False
    lines = text.split('\n')
    changed = False
    in_mermaid = False
    block_state = None
    out_lines = []
    for line in lines:
        if not in_mermaid:
            if MERMAID_OPEN.match(line):
                in_mermaid = True
                block_state = BlockState()
                out_lines.append(line)
                continue
            out_lines.append(line)
            continue
        if MERMAID_CLOSE.match(line):
            in_mermaid = False
            block_state = None
            out_lines.append(line)
            continue
        new_line = escape_in_mermaid_line(line)
        new_line = escape_numdot_in_mermaid_line(new_line)
        new_line = fix_breaks_and_semicolons_in_line(new_line, block_state)
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
