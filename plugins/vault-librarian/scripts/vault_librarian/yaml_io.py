"""YAML reader + frontmatter writer.

PyYAML is preferred when present; otherwise a minimal scoped parser handles
the subset of YAML this skill produces (block mappings, sequences of mappings,
quoted/coerced scalars). The frontmatter writer is hand-rolled in both cases
so output is stable and matches the vault's existing style.
"""

from __future__ import annotations

from typing import Any

try:
    import yaml as _pyyaml  # type: ignore
    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False


def yaml_load(text: str) -> Any:
    """Load YAML, using PyYAML if available, else the scoped reader."""
    if HAVE_PYYAML:
        return _pyyaml.safe_load(text)
    return _MiniYaml(text).parse()


def yaml_dump_frontmatter(data: dict) -> str:
    """Serialise a frontmatter dict to canonical block YAML.

    Field order is fixed (title → placement → provenance → tags → other)
    so re-writes produce stable diffs regardless of which YAML lib loaded
    the input. **Changing this order will cause every note in every user's
    vault to show a diff on the next write.** Don't do it casually.

    Pinned by `tests/test_yaml_io.TestFrontmatterDump`.
    """
    ORDERED = [
        "title",
        "pillar", "category", "sub_area", "topic", "kind",
        "created", "updated",
        "aliases",
        "tags",
    ]
    lines: list[str] = []
    for key in ORDERED:
        if key not in data: continue
        v = data[key]
        if v in (None, "", []): continue
        if key in {"aliases", "tags"}:
            lines.append(f"{key}:")
            for item in v:
                lines.append(f"  - {_scalar(item)}")
        else:
            lines.append(f"{key}: {_scalar(v)}")
    seen = set(ORDERED)
    for key in sorted(data.keys()):
        if key in seen: continue
        v = data[key]
        if isinstance(v, list):
            lines.append(f"{key}:")
            for item in v:
                lines.append(f"  - {_scalar(item)}")
        elif isinstance(v, dict):
            lines.append(f"{key}:")
            for k2, v2 in v.items():
                lines.append(f"  {k2}: {_scalar(v2)}")
        else:
            lines.append(f"{key}: {_scalar(v)}")
    return "\n".join(lines)


def _scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in (":", "#", "[", "]", "{", "}", "&", "*", "!", "|", ">", "%", "@", "`")):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _is_blank_or_comment(ln: str) -> bool:
    s = ln.strip()
    return s == "" or s.startswith("#")


def _coerce_scalar(s: str) -> Any:
    if s == "":
        return None
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if s in {"true", "True"}:
        return True
    if s in {"false", "False"}:
        return False
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    return s


class _MiniYaml:
    """Scoped YAML reader for this tool's config and frontmatter shape.

    Supported: block mappings, block sequences, sequences of mappings,
    comments, string/int/bool scalars (quoted or bare).
    """

    def __init__(self, text: str) -> None:
        self.lines = [ln for ln in text.splitlines() if not _is_blank_or_comment(ln)]
        self.i = 0

    def parse(self) -> Any:
        return self._parse_block(indent=0)

    def _peek_indent(self) -> int:
        if self.i >= len(self.lines):
            return -1
        ln = self.lines[self.i]
        return len(ln) - len(ln.lstrip(" "))

    def _parse_block(self, indent: int) -> Any:
        if self.i >= len(self.lines):
            return None
        first = self.lines[self.i].lstrip(" ")
        if first.startswith("- "):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_mapping(self, indent: int) -> dict:
        out: dict[str, Any] = {}
        while self.i < len(self.lines):
            cur_indent = self._peek_indent()
            if cur_indent < indent or cur_indent > indent:
                break
            ln = self.lines[self.i].lstrip(" ")
            if ln.startswith("- "):
                break
            if ":" not in ln:
                self.i += 1
                continue
            key, _, rest = ln.partition(":")
            key = key.strip()
            value = rest.strip()
            self.i += 1
            if value == "":
                next_indent = self._peek_indent()
                if next_indent > indent:
                    out[key] = self._parse_block(next_indent)
                else:
                    out[key] = None
            else:
                out[key] = _coerce_scalar(value)
        return out

    def _parse_sequence(self, indent: int) -> list:
        out: list[Any] = []
        while self.i < len(self.lines):
            cur_indent = self._peek_indent()
            if cur_indent < indent or cur_indent != indent:
                break
            ln = self.lines[self.i].lstrip(" ")
            if not ln.startswith("- "):
                break
            after_dash = ln[2:]
            if ":" in after_dash and not after_dash.startswith('"'):
                key, _, rest = after_dash.partition(":")
                item: dict[str, Any] = {key.strip(): _coerce_scalar(rest.strip())}
                self.i += 1
                child_indent = indent + 2
                while self.i < len(self.lines):
                    ci = self._peek_indent()
                    if ci < child_indent:
                        break
                    if ci == indent and self.lines[self.i].lstrip(" ").startswith("- "):
                        break
                    nested = self._parse_mapping(child_indent)
                    item.update(nested)
                    break
                out.append(item)
            else:
                out.append(_coerce_scalar(after_dash.strip()))
                self.i += 1
        return out
