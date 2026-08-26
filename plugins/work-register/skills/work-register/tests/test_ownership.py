"""Field ownership, and the two properties that follow from it.

    existence · text · grouping · track  ->  the day file owns
    status: column · checkbox            ->  the board owns

Nothing has two owners, so there is never a conflict to resolve — but only as long as each
verb stays on its own side of the line. `--reconcile` crossing into text would silently
rewrite the record; sync crossing into placement would undo every drag.
"""

from __future__ import annotations

from harness import RegisterCase

DAY = """## Tooling

- [ ] ▶ fix the sync — it drops `^anchors`, per ADR-079 ::work-register
- [ ] ⏳ write the tests
- [x] ship the thing
"""


class Reconcile(RegisterCase):
    """The board owns two fields, and `--reconcile` may write back exactly those two."""

    def setUp(self) -> None:
        super().setUp()
        self.day = self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def test_reconcile_rewrites_only_the_checkbox_and_the_lane_marker(self):
        line = next(l for l in self.day.read_text().splitlines() if "fix the sync" in l)
        self.assertIn("▶", line)

        # Drag it in the Obsidian interface: the board moves, the day file does not.
        card = self.reg.card_for("20260801-01")
        text = self.reg.board_text().replace(card + "\n", "")
        self.reg.board.write_text(
            text.replace("## 🔴 Blocked\n", "## 🔴 Blocked\n\n" + card + "\n", 1)
        )
        self.reg.run("--reconcile")

        after = next(l for l in self.day.read_text().splitlines() if "fix the sync" in l)
        self.assertIn("🔴", after, "the lane marker was not stamped back")
        self.assertNotIn("▶", after, "the old lane marker was left behind")
        self.assertIn(
            "fix the sync — it drops `^anchors`, per ADR-079", after, "the item text was rewritten"
        )
        self.assertIn("::work-register", after, "reconcile ate the ::track declaration")
        self.assertTrue(after.strip().startswith("- [ ]"))

    def test_reconcile_ticks_the_checkbox_when_a_card_reaches_done(self):
        self.reg.run("--move", "20260801-01=Done")
        after = next(l for l in self.day.read_text().splitlines() if "fix the sync" in l)
        self.assertTrue(after.strip().startswith("- [x]"))
        # Done is the absence of a lane, so the marker is removed rather than replaced.
        self.assertNotIn("▶", after)
        self.assertIn("::work-register", after)

    def test_reconcile_leaves_an_item_the_board_does_not_hold(self):
        """A card deleted off the board has no status to stamp, so its line must not move."""
        card = self.reg.card_for("20260801-02")
        self.reg.board.write_text(self.reg.board_text().replace(card + "\n", ""))
        before = self.day.read_text()
        self.reg.run("--reconcile")
        self.assertEqual(before, self.day.read_text())


class AdditiveSync(RegisterCase):
    """Sync adds what is new and never moves what is there. Drags have to survive it."""

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()
        self.reg.run("--move", "20260801-01=Blocked")

    def test_a_moved_card_stays_put_across_a_plain_sync(self):
        self.reg.day("2026-08-02", "## More\n\n- [ ] ▶ another thing\n")
        out = self.reg.run()
        self.assertEqual("🔴 Blocked", self.reg.column_of("20260801-01"))
        self.assertIn("20260802-01", out.stdout, "the new day file was not ingested")

    def test_a_moved_card_stays_put_across_a_refresh(self):
        day = self.reg.register_dir / "2026-08-01.md"
        day.write_text(day.read_text().replace("write the tests", "write the tests first"))
        self.reg.run("--refresh")
        self.assertEqual("🔴 Blocked", self.reg.column_of("20260801-01"))
        self.assertIn("write the tests first", self.reg.card_for("20260801-02"))


class Tombstones(RegisterCase):
    """The ledger records every id ever placed, so a removal is a decision that sticks."""

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def test_a_card_deleted_from_the_board_is_not_resurrected(self):
        card = self.reg.card_for("20260801-02")
        self.reg.board.write_text(self.reg.board_text().replace(card + "\n", ""))
        self.assertNotIn("20260801-02", self.reg.ids_on())

        out = self.reg.run()
        self.assertNotIn("20260801-02", self.reg.ids_on(), "sync resurrected a deleted card")
        self.assertIn("stay deleted", out.stdout)

    def test_an_archived_card_stays_off_and_is_reported_as_archived(self):
        """Archived and deleted are both 'in the ledger, on no board' — and must not be
        counted as the same thing: one is the owner throwing the card away, the other is
        the Done column being a recency window."""
        self.reg.run("--archive", "--keep", "0")
        self.assertNotIn("20260801-03", self.reg.ids_on())

        out = self.reg.run()
        self.assertNotIn("20260801-03", self.reg.ids_on(), "sync re-added an archived card")
        self.assertIn("archived off the board stay off", out.stdout)
        self.assertNotIn("stay deleted", out.stdout, "an archived card was counted as deleted")

    def test_archive_holds_back_an_anchored_card_and_names_it(self):
        self.reg.board.write_text(
            self.reg.board_text().replace("- [x] ship the thing", "- [x] ship the thing ^wr91b2", 1)
        )
        out = self.reg.run("--archive", "--keep", "0")
        self.assertIn("20260801-03", self.reg.ids_on(), "archive broke a live link target")
        self.assertIn("^wr91b2", out.stdout, "the held-back card was not named")
        self.assertIn("--include-anchored", out.stdout)
