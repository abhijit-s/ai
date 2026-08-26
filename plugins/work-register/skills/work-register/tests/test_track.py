"""Track resolution: the precedence ladder, and the grammar that keeps `::` out of prose.

A track is the one field that partitions the board by who is asking, so getting it wrong
is not cosmetic — it decides which board a card renders to once scopes exist.
"""

from __future__ import annotations

import json

from harness import RegisterCase, engine

RULES = """
[[track_rules]]
pattern = "sync|board"
track   = "work-register"

[[track_rules]]
pattern = "deploy|argocd"
track   = "prod-setup"
"""


class Precedence(RegisterCase):
    """item `::token` beats heading `::token` beats `[[track_rules]]` beats empty."""

    CONTRACT_EXTRA = RULES

    def rows(self) -> dict[str, dict]:
        return {r["text"]: r for r in json.loads(self.reg.run("--list", "--json").stdout)}

    def test_the_full_ladder(self):
        self.reg.day(
            "2026-08-01",
            """## Section ::heading-track

- [ ] ▶ item declares its own ::item-track
- [ ] ▶ item inherits the heading

## Plain heading

- [ ] ▶ nothing declared, but it mentions the board
- [ ] ▶ nothing declared and nothing recognised either
- [ ] ▶ a deploy to argocd
""",
        )
        self.reg.run()
        rows = self.rows()
        self.assertEqual("item-track", rows["item declares its own"]["track"])
        self.assertEqual("heading-track", rows["item inherits the heading"]["track"])
        self.assertEqual(
            "work-register",
            rows["nothing declared, but it mentions the board"]["track"],
            "a heading token must not reach a section it does not head",
        )
        self.assertEqual("", rows["nothing declared and nothing recognised either"]["track"])
        self.assertEqual("prod-setup", rows["a deploy to argocd"]["track"])

    def test_the_declaration_is_peeled_off_the_card_face(self):
        """`::name` is a declaration, not prose, so it must not reach the board."""
        self.reg.day("2026-08-01", "## S\n\n- [ ] ▶ mid ::item-track sentence token\n")
        self.reg.run()
        self.assertEqual("mid sentence token", self.rows()["mid sentence token"]["text"])
        self.assertNotIn("::item-track", self.reg.board_text())

    def test_first_matching_rule_wins_and_the_rest_are_not_consulted(self):
        """A card carries several tags because it touches several concerns; it belongs to
        one track, so the rules are an ordered decision rather than an accumulation."""
        self.reg.day("2026-08-01", "## S\n\n- [ ] ▶ sync the board before the argocd deploy\n")
        self.reg.run()
        self.assertEqual("work-register", list(self.rows().values())[0]["track"])


class Grammar(RegisterCase):
    """`::` is a token, not a substring. The guard is what keeps it out of ordinary text."""

    def test_the_token_does_not_match_inside_a_url_or_a_qualified_name(self):
        split = engine().split_track
        for prose in [
            "see https://example.com/a::b for the write-up",
            "the C++ call Foo::bar() is not a track",
            "http://[::1]/health returns 200",
            "a bare :: on its own",
            "::UPPERCASE is not a token",
            "::-leading-hyphen is not one either",
        ]:
            with self.subTest(prose=prose):
                track, text = split(prose)
                self.assertEqual("", track, f"{prose!r} was read as a track declaration")
                self.assertEqual(prose, text, "the text was rewritten by a non-match")

    def test_the_token_does_match_when_it_stands_alone(self):
        split = engine().split_track
        for prose, want in [
            ("::work-register", "work-register"),
            ("fix the sync ::work-register", "work-register"),
            ("::bc5-performance and then some", "bc5-performance"),
            ("wrapped ::a1 in the middle", "a1"),
        ]:
            with self.subTest(prose=prose):
                self.assertEqual(want, split(prose)[0])

    def test_a_url_in_a_card_survives_the_round_trip(self):
        self.reg.day(
            "2026-08-01",
            "## S\n\n- [ ] ▶ read https://example.com/a::b and Foo::bar ::real-track\n",
        )
        self.reg.run()
        rows = json.loads(self.reg.run("--list", "--json").stdout)
        self.assertEqual("real-track", rows[0]["track"])
        self.assertEqual("read https://example.com/a::b and Foo::bar", rows[0]["text"])
