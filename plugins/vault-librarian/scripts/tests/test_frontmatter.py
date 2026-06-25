"""Tests for the frontmatter module — splitting, walking, writing."""

from vault_librarian.frontmatter import (
    FM_DELIM,
    _first_h1,
    _split_frontmatter,
    walk_notes,
    write_frontmatter,
)
from vault_librarian.model import load_taxonomy


class TestSplitFrontmatter:
    def test_with_frontmatter(self):
        text = "---\ntitle: T\ncategory: foo\n---\n\nBody here\n"
        fm, body = _split_frontmatter(text)
        assert fm == {"title": "T", "category": "foo"}
        assert body.strip() == "Body here"

    def test_no_frontmatter(self):
        fm, body = _split_frontmatter("# Just a heading\n")
        assert fm is None
        assert body == "# Just a heading\n"

    def test_malformed_closing_delim(self):
        # No closing ---; treated as no frontmatter, body preserved.
        fm, body = _split_frontmatter("---\ntitle: T\nBody")
        assert fm is None

    def test_empty_frontmatter_returns_empty_dict(self):
        fm, body = _split_frontmatter("---\n---\n\nBody\n")
        # _split_frontmatter normalises None to {} only via read_note; raw
        # split returns whatever yaml_load gave us. Empty YAML → None,
        # but `... or {}` in the splitter coerces it.
        assert fm == {}


class TestFirstH1:
    def test_finds_first(self):
        assert _first_h1("intro\n# Main Title\n## Sub\n") == "Main Title"

    def test_returns_none_when_absent(self):
        assert _first_h1("## Sub only\nbody") is None

    def test_ignores_leading_whitespace(self):
        assert _first_h1("   # Title\n") == "Title"


class TestWriteFrontmatter:
    def test_round_trip_preserves_body(self, tmp_path):
        f = tmp_path / "n.md"
        f.write_text("---\ntitle: Old\n---\n\nbody text\n")
        write_frontmatter(f, {"title": "New", "category": "foo", "tags": ["foo"]},
                          "body text\n")
        text = f.read_text()
        fm, body = _split_frontmatter(text)
        assert fm["title"] == "New"
        assert fm["category"] == "foo"
        assert fm["tags"] == ["foo"]
        assert body.strip() == "body text"

    def test_writes_canonical_delimiter(self, tmp_path):
        f = tmp_path / "n.md"
        write_frontmatter(f, {"title": "T"}, "")
        assert f.read_text().startswith(f"{FM_DELIM}\n")


class TestWalkNotes:
    def test_returns_all_md_files(self, vault):
        vault.config()
        vault.note("01-Foo/Alpha/a.md", title="A", category="alpha", tags=["alpha"])
        vault.note("01-Foo/Alpha/b.md", title="B", category="alpha", tags=["alpha"])
        tax = load_taxonomy(vault.config_path)
        notes = walk_notes(tax)
        names = {n.path.name for n in notes}
        assert names == {"a.md", "b.md"}

    def test_excludes_configured_dirs(self, vault):
        vault.config()
        vault.note("01-Foo/Alpha/a.md", title="A", category="alpha", tags=["alpha"])
        # Exclude `.git` from config — and assert files under `.git` are skipped.
        (vault.root / ".git").mkdir()
        (vault.root / ".git" / "junk.md").write_text("nope")
        tax = load_taxonomy(vault.config_path)
        notes = walk_notes(tax)
        assert all(".git" not in n.path.parts for n in notes)
