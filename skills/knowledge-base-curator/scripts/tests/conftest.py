"""Pytest fixtures: ergonomic builders for synthetic vaults.

Each test gets a fresh `tmp_path`-rooted vault — no shared state, no real
git, no real network. The `vault` fixture returns a `VaultBuilder` whose
`.note()`, `.readme()`, and `.config()` methods compose a complete vault
in 4-6 lines per test.

Why this style:
  - Tests double as executable documentation: reading a test shows the
    exact frontmatter shape the code expects.
  - Refactors that change behavior surface immediately; refactors that
    move code internally don't churn the tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

import pytest

# Make `kb_curator` importable without installing the package.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# VaultBuilder — DSL for assembling test vaults.
# ---------------------------------------------------------------------------

@dataclass
class VaultBuilder:
    """Compose a synthetic vault on disk under `root`.

    Example::

        v = vault(tmp_path)
        v.config()                                # default taxonomy.yaml
        v.note("01-Foo/Bar/note.md", category="bar", tags=["bar", "x"],
               body="# Heading\\n\\nbody text")
        v.readme("01-Foo/Bar")                   # auto-generates an index README
    """

    root: Path
    config_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.root.parent / "taxonomy.yaml"

    # -- File creation -------------------------------------------------

    def note(self, rel_path: str, *, title: str | None = None,
              category: str | None = None, tags: list[str] | None = None,
              extra_fm: dict | None = None, body: str = "") -> Path:
        """Write a note with the given frontmatter at `root/rel_path`."""
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        fm_lines = ["---"]
        if title is not None:
            fm_lines.append(f"title: {title}")
        if category is not None:
            fm_lines.append(f"category: {category}")
        if tags is not None:
            fm_lines.append("tags:")
            for t in tags:
                fm_lines.append(f"  - {t}")
        for k, v in (extra_fm or {}).items():
            fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        p.write_text("\n".join(fm_lines) + "\n\n" + body, encoding="utf-8")
        return p

    def raw(self, rel_path: str, content: str) -> Path:
        """Write a file verbatim — bypasses frontmatter helpers."""
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def readme(self, rel_dir: str, *, title: str | None = None,
                category: str = "uncategorised") -> Path:
        """Create a README.md acting as the index for `rel_dir`."""
        d = self.root / rel_dir
        d.mkdir(parents=True, exist_ok=True)
        title = title or d.name
        return self.note(
            f"{rel_dir}/README.md",
            title=title, category=category, tags=[category, "index"],
            body=f"# {title}\n\nIndex for {title}.",
        )

    # -- Config -------------------------------------------------------

    def config(self, *, pillars: list[dict] | None = None,
                slug_case: str = "kebab",
                link_syntax: str = "obsidian",
                dates_source: str = "none",
                tags: list[str] | None = None,
                inference_rules: list[dict] | None = None,
                naming: dict | None = None,
                emojis: dict | None = None,
                path_conventions: dict | None = None) -> Path:
        """Write a taxonomy.yaml suitable for the synthetic vault.

        Defaults to two stock pillars (`01-Foo`, `02-Bar`) so most tests
        don't need to specify them. Pass `pillars=...` to override.
        """
        pillars = pillars or DEFAULT_PILLARS
        path_conv = path_conventions or {"pillar_pattern": r"^\d{2}-(.+)$",
                                          "sub_area_pattern": r"^\d{2}-(.+)$",
                                          "readme_filename": "README.md"}
        out = dedent(f"""\
            vault:
              root: "{self.root}"
              exclude_dirs: [.git]
            path_conventions:
              pillar_pattern: '{path_conv["pillar_pattern"]}'
              sub_area_pattern: '{path_conv["sub_area_pattern"]}'
              readme_filename: {path_conv["readme_filename"]}
            link_syntax:
              type: {link_syntax}
            slug_case: {slug_case}
            dates:
              source: {dates_source}
            frontmatter:
              required: [title, category, tags]
              derived: [pillar, sub_area, topic, kind, created, updated]
              optional: [aliases]
              rules:
                first_tag_mirrors_category: true
                readme_extra_tag: index
              kind_from_tag: [index, crash-course, deep-dive, reference]
            pillars:
            """)
        for p in pillars:
            out += f'  - slug: {p["slug"]}\n'
            out += f'    path: "{p["path"]}"\n'
            out += f'    central_question: "{p.get("central_question","")}"\n'
            out += f'    areas:\n'
            for a in p.get("areas", []):
                out += f'      - slug: {a["slug"]}\n'
                out += f'        path: "{a["path"]}"\n'
                out += f'        central_question: "{a.get("central_question","")}"\n'
        out += "tags:\n"
        for t in tags or []:
            out += f"  - {t}\n"
        out += "emojis:\n"
        for cat, e in (emojis or {}).items():
            out += f"  {cat}: \"{e}\"\n"
        out += "inference_rules:\n"
        for r in inference_rules or []:
            out += f"  - tag: {r['tag']}\n    keywords: [{', '.join(repr(k) for k in r['keywords'])}]\n"
        out += "naming:\n"
        for k, v in (naming or {"replace_underscores": True,
                                 "strip_emoji_prefix": True,
                                 "max_length": 120}).items():
            v_str = "true" if v is True else "false" if v is False else v
            out += f"  {k}: {v_str}\n"
        self.config_path.write_text(out, encoding="utf-8")
        return self.config_path


DEFAULT_PILLARS = [
    {
        "slug": "foo",
        "path": "01-Foo",
        "central_question": "What is Foo?",
        "areas": [
            {"slug": "alpha", "path": "Alpha", "central_question": "A?"},
            {"slug": "beta", "path": "Beta", "central_question": "B?"},
        ],
    },
    {
        "slug": "bar",
        "path": "02-Bar",
        "central_question": "What is Bar?",
        "areas": [
            {"slug": "gamma", "path": "Gamma", "central_question": "C?"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path: Path) -> VaultBuilder:
    """Empty vault rooted at a tmp path. Call `.config()` and `.note()` to populate."""
    return VaultBuilder(root=tmp_path / "vault")


@pytest.fixture
def populated_vault(vault: VaultBuilder) -> VaultBuilder:
    """A small but representative vault — useful for cross-cutting tests."""
    vault.config()
    vault.readme("01-Foo/Alpha", category="alpha")
    vault.note("01-Foo/Alpha/note-one.md",
                title="Note One", category="alpha", tags=["alpha", "x"],
                body="# Note One\n\nThis note mentions performance and p99 latency.")
    vault.note("01-Foo/Alpha/note-two.md",
                title="Note Two", category="alpha", tags=["alpha"],
                # Wiki-links resolve by filename stem, not by frontmatter title.
                body="# Note Two\n\nA companion note. See [[note-one]] for context.")
    vault.readme("02-Bar/Gamma", category="gamma")
    vault.note("02-Bar/Gamma/note-three.md",
                title="Note Three", category="gamma", tags=["gamma"],
                body="# Note Three\n\nUnrelated.")
    return vault
