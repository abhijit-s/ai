"""Tests for link parsing, code-span filtering, and Levenshtein."""

from kb_curator.links import (
    _NON_LINK_TARGET,
    _code_span_ranges,
    find_wiki_links,
    levenshtein,
)


class TestFindWikiLinks:
    def test_basic_obsidian(self):
        body = "see [[Note Stem]] for details"
        assert find_wiki_links(body, "obsidian") == [("Note Stem", None)]

    def test_display_text(self):
        assert find_wiki_links("[[Stem|nicely shown]]", "obsidian") == \
            [("Stem", "nicely shown")]

    def test_section_anchor_stripped(self):
        # We resolve by stem; the section is ignored for indexing purposes.
        assert find_wiki_links("[[Stem#section]]", "obsidian") == [("Stem", None)]

    def test_skips_inline_code(self):
        # The wiki-link syntax inside backticks is documentation, not a link.
        body = "Use `[[Stem]]` syntax to link"
        assert find_wiki_links(body, "obsidian") == []

    def test_skips_fenced_code_blocks(self):
        body = "```md\n[[Stem]]\n```\nreal: [[Real]]\n"
        assert find_wiki_links(body, "obsidian") == [("Real", None)]

    def test_markdown_syntax(self):
        body = "see [the doc](Note%20Stem.md) for details"
        assert find_wiki_links(body, "markdown") == [("Note Stem", "the doc")]

    def test_markdown_ignores_external(self):
        body = "[OpenAI](https://openai.com) is not a link to a note"
        assert find_wiki_links(body, "markdown") == []

    def test_none_syntax_returns_empty(self):
        assert find_wiki_links("[[Stem]] and [other](Note.md)", "none") == []

    def test_drops_latex_lookalikes(self):
        # `[[M*k/N]]` looks like a wiki-link but is inline math.
        body = "compute [[M*k/N]] for each shard"
        assert find_wiki_links(body, "obsidian") == []


class TestCodeSpanRanges:
    def test_inline_span_detected(self):
        body = "before `code here` after"
        ranges = _code_span_ranges(body)
        assert len(ranges) == 1

    def test_fenced_span_detected(self):
        body = "before\n```\ncode\n```\nafter"
        ranges = _code_span_ranges(body)
        # At least one fenced range.
        assert any(e - s > 5 for s, e in ranges)


class TestLevenshtein:
    def test_zero_for_equal(self):
        assert levenshtein("foo", "foo") == 0

    def test_single_substitution(self):
        assert levenshtein("foo", "boo") == 1

    def test_empty_arg(self):
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3

    def test_smart_quote_drift(self):
        # The motivating case: a → b
        a = "The 'P' in Go's GMP Scheduler"
        b = "The ‘P’ in Go’s GMP Scheduler"
        # Three smart-quote substitutions away.
        assert levenshtein(a, b) == 3


class TestNonLinkTargetRegex:
    def test_matches_known_false_positives(self):
        assert _NON_LINK_TARGET.search("M*k")        # asterisk
        assert _NON_LINK_TARGET.search("m/L")        # single-letter math fraction
        assert _NON_LINK_TARGET.search("A*")         # trailing asterisk

    def test_lets_real_targets_through(self):
        assert not _NON_LINK_TARGET.search("Buffered vs Unbuffered Channels")
