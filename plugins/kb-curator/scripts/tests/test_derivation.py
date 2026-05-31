"""Tests for derivation: canonical filenames, classification, placement, kind."""

from pathlib import Path

from kb_curator.derivation import (
    canonical_filename,
    classify_text,
    derive_kind,
    derive_placement,
)
from kb_curator.model import load_taxonomy


# ---------------------------------------------------------------------------
# canonical_filename
# ---------------------------------------------------------------------------

NAMING = {"replace_underscores": True, "strip_emoji_prefix": True, "max_length": 120}


class TestCanonicalFilename:
    def test_collapses_whitespace(self):
        assert canonical_filename("Foo   Bar", NAMING) == "Foo Bar"

    def test_underscore_to_hyphen_preserves_tokens(self):
        # The motivating case: io_uring should not become "io uring".
        assert canonical_filename("io_uring deep dive", NAMING) == "io-uring deep dive"

    def test_strips_leading_emoji(self):
        assert canonical_filename("🚀 Rockets", NAMING) == "Rockets"

    def test_drops_terminal_question_mark(self):
        assert canonical_filename("Where's My Lock?", NAMING) == "Where's My Lock"

    def test_max_length_caps(self):
        long_title = "a" * 200
        result = canonical_filename(long_title, {**NAMING, "max_length": 50})
        assert len(result) <= 50


# ---------------------------------------------------------------------------
# derive_kind
# ---------------------------------------------------------------------------

class TestDeriveKind:
    def test_readme_is_index(self, tmp_path):
        p = tmp_path / "README.md"
        assert derive_kind({}, p) == "index"

    def test_tag_priority_order(self, tmp_path):
        # When multiple kind-tags are present, the listed priority wins.
        p = tmp_path / "note.md"
        kind = derive_kind({"tags": ["deep-dive", "crash-course"]}, p)
        # Priority is: crash-course, deep-dive, reference, index → crash-course wins.
        assert kind == "crash-course"

    def test_filename_heuristic(self, tmp_path):
        assert derive_kind({}, tmp_path / "Foo - Deep Dive.md") == "deep-dive"

    def test_returns_none_when_uncertain(self, tmp_path):
        # No tag, no filename hint — better to leave unset than guess.
        assert derive_kind({"tags": ["foo"]}, tmp_path / "note.md") is None


# ---------------------------------------------------------------------------
# derive_placement
# ---------------------------------------------------------------------------

class TestDerivePlacement:
    def test_simple_pillar_area_note(self, vault):
        vault.config()
        tax = load_taxonomy(vault.config_path)
        out = derive_placement(Path("01-Foo/Alpha/note.md"), tax)
        assert out == {"pillar": "foo"}

    def test_numbered_sub_area_extracted(self, vault):
        vault.config(pillars=[{
            "slug": "foo", "path": "01-Foo", "areas": [{"slug": "alpha", "path": "Alpha"}],
        }])
        tax = load_taxonomy(vault.config_path)
        out = derive_placement(Path("01-Foo/Alpha/02-Sub/note.md"), tax)
        assert out["sub_area"] == "sub"
        assert out["pillar"] == "foo"

    def test_topic_when_no_numbered_subarea(self, vault):
        vault.config(pillars=[{
            "slug": "foo", "path": "01-Foo", "areas": [{"slug": "alpha", "path": "Alpha"}],
        }])
        tax = load_taxonomy(vault.config_path)
        out = derive_placement(Path("01-Foo/Alpha/Topic Group/note.md"), tax)
        assert out["topic"] == "topic-group"
        assert "sub_area" not in out

    def test_configurable_pillar_pattern(self, vault):
        vault.config(
            pillars=[{
                "slug": "foundations", "path": "A-Foundations",
                "areas": [{"slug": "stuff", "path": "Stuff"}],
            }],
            path_conventions={
                "pillar_pattern": r"^[A-Z]-(.+)$",
                "sub_area_pattern": r"^[A-Z]-(.+)$",
                "readme_filename": "README.md",
            },
        )
        tax = load_taxonomy(vault.config_path)
        out = derive_placement(Path("A-Foundations/Stuff/note.md"), tax)
        assert out["pillar"] == "foundations"


# ---------------------------------------------------------------------------
# classify_text
# ---------------------------------------------------------------------------

class TestClassifyText:
    def test_strong_filename_signal_wins(self, vault):
        vault.config()
        tax = load_taxonomy(vault.config_path)
        # The stem 'alpha' is the area slug; it should score the top.
        result = classify_text(body="some body text", stem="alpha-thoughts", tax=tax)
        assert result["category"] == "alpha"

    def test_returns_score_and_alternates(self, vault):
        vault.config()
        tax = load_taxonomy(vault.config_path)
        result = classify_text(body="random text", stem="random", tax=tax)
        assert "score" in result and "alternates" in result
