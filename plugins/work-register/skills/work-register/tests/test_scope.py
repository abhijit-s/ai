"""Scope: one capture stream, one board per scope, every card on exactly one of them.

Disjointness is the property the split rests on rather than a tidy detail. The board owns
a card's column, so a card on two boards would have two owners for that one field and they
would diverge the moment either copy was dragged.
"""

from __future__ import annotations

import json

from harness import RegisterCase

SPLIT = """
[[track_rules]]
pattern = "sync|board"
track   = "work-register"

[[track_rules]]
pattern = "hallway|kitchen"
track   = "house-move"

[scope]
default          = "work"
suppress_default = true
track.house-move = "personal"
board.personal   = "PERSONAL-REGISTER.md"
"""

# The same vocabulary with one track reclassified — appended as a WHOLE contract, because
# TOML refuses a second `[scope]` header and the tables here are already declared once.
RECLASSIFIED = SPLIT.replace(
    'track.house-move = "personal"',
    'track.house-move = "personal"\ntrack.work-register = "personal"',
)
COLLIDING = SPLIT.replace(
    'board.personal   = "PERSONAL-REGISTER.md"',
    'board.personal   = "PERSONAL-REGISTER.md"\nboard.third      = "PERSONAL-REGISTER.md"',
)

DAY = """## Mixed

- [ ] ▶ fix the sync
- [ ] ▶ paint the hallway
- [ ] ▶ something with no track at all
- [ ] ⏳ repaint the kitchen
"""


class Partition(RegisterCase):
    CONTRACT_EXTRA = SPLIT

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def test_cards_are_disjoint_across_boards(self):
        work = set(self.reg.ids_on())
        personal = set(self.reg.ids_on("PERSONAL-REGISTER.md"))
        self.assertTrue(work and personal, "both boards should hold cards")
        self.assertEqual(set(), work & personal, "a card is on two boards — two owners for one column")
        self.assertEqual(4, len(work | personal))

    def test_a_personal_track_renders_only_to_its_own_board(self):
        personal = self.reg.board_text("PERSONAL-REGISTER.md")
        self.assertIn("paint the hallway", personal)
        self.assertIn("repaint the kitchen", personal)
        self.assertNotIn("fix the sync", personal)
        self.assertNotIn("paint the hallway", self.reg.board_text())

    def test_a_trackless_card_lands_on_the_default_board(self):
        """`board_for` is total: nothing can fall off every board and become invisible."""
        self.assertIn("something with no track at all", self.reg.board_text())
        self.assertNotIn(
            "something with no track at all", self.reg.board_text("PERSONAL-REGISTER.md")
        )

    def test_the_default_scope_tag_is_suppressed_and_the_exception_is_not(self):
        self.assertNotIn("#scope/work", self.reg.board_text())
        self.assertIn("#scope/personal", self.reg.board_text("PERSONAL-REGISTER.md"))

    def test_a_second_sync_adds_nothing_and_keeps_the_partition(self):
        out = self.reg.run()
        self.assertIn("0 card(s) added", out.stdout)
        self.assertEqual(set(), set(self.reg.ids_on()) & set(self.reg.ids_on("PERSONAL-REGISTER.md")))


class Migration(RegisterCase):
    """Reclassifying a track moves cards BETWEEN files, which is a status write — so it
    takes `--migrate`, and then `--apply` as well. Sync never does it on its own."""

    CONTRACT_EXTRA = SPLIT

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def test_sync_reports_a_reclassified_track_but_does_not_move_it(self):
        self.reg.write_contract(self.init_contract + RECLASSIFIED)
        out = self.reg.run()
        self.assertIn("render to a different board", out.stdout)
        self.assertIn("fix the sync", self.reg.board_text(), "sync relocated a card on its own")

    def test_migrate_apply_moves_the_card_and_keeps_its_column(self):
        self.reg.run("--move", "20260801-01=Blocked")
        self.assertEqual("🔴 Blocked", self.reg.column_of("20260801-01"))
        self.reg.write_contract(self.init_contract + RECLASSIFIED)

        report = self.reg.run("--migrate")
        self.assertIn("nothing has been moved", report.stdout)
        self.assertIn("fix the sync", self.reg.board_text())

        self.reg.run("--migrate", "--apply")
        self.assertNotIn("20260801-01", self.reg.ids_on())
        self.assertEqual(
            "🔴 Blocked",
            self.reg.column_of("20260801-01", "PERSONAL-REGISTER.md"),
            "the column did not travel with the card",
        )

    def test_two_scopes_naming_one_board_is_refused(self):
        """A collision cannot be told apart again, so a later config change could not know
        which cards to move."""
        self.reg.write_contract(self.init_contract + COLLIDING)
        out = self.reg.run(check=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn("give each its own file", out.stderr)


class SingleBoardIsUnchanged(RegisterCase):
    """A register naming no second scope renders exactly one file, as it always did."""

    def test_one_board_and_no_scope_tags(self):
        self.reg.day("2026-08-01", DAY)
        self.reg.run()
        self.assertTrue(self.reg.board.is_file())
        self.assertFalse((self.reg.root / "PERSONAL-REGISTER.md").exists())
        self.assertNotIn("#scope/", self.reg.board_text())
        rows = json.loads(self.reg.run("--list", "--json").stdout)
        self.assertEqual(4, len(rows))
