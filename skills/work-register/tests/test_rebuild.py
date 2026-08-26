"""`--rebuild`: the destructive verb, and the gate that makes it deliberate.

Rebuild discards the board and re-places every card at its day-file lane. That is a
re-derivation, not a repair, and it takes three things with it: every drag not yet
reconciled, every Obsidian block anchor (`^abc123`) the owner created by copying a link to
a card, and — because the sync path stamps `since` on everything it places — every
staleness clock, so the board reports itself healthy the moment after.

It already refused on a register rendering more than one board. It did NOT refuse on a
single-board one, which is how eleven live anchors on a real board were destroyed by
someone reaching for it as the obvious repair. These tests pin the gate that closes that.
"""

from __future__ import annotations

import json

from harness import RegisterCase

DAY = """## Tooling

- [ ] ▶ fix the sync
- [ ] ▶ write the tests
"""

SPLIT = """
[[track_rules]]
pattern = "hallway"
track   = "house-move"

[scope]
default          = "work"
track.house-move = "personal"
board.personal   = "PERSONAL-REGISTER.md"
"""


class Gate(RegisterCase):
    """A single-board register is the dangerous case, because nothing stopped it."""

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()
        self.reg.board.write_text(
            self.reg.board_text().replace("- [ ] fix the sync", "- [ ] fix the sync ^wr7f3a21", 1)
        )

    def test_a_bare_rebuild_refuses_and_exits_non_zero(self):
        before = self.reg.checksum()
        out = self.reg.run("--rebuild", check=False)
        self.assertNotEqual(0, out.returncode, "a refusal that exits 0 is not a refusal")
        self.assertUnchanged(before, "the refused --rebuild")

    def test_the_refusal_names_what_would_be_lost_and_how_to_mean_it(self):
        out = self.reg.run("--rebuild", check=False)
        message = out.stderr + out.stdout
        self.assertIn("--discard-placement", message)
        self.assertIn("anchor", message.lower())
        self.assertIn("--refresh", message, "the refusal should name the non-destructive verb")

    def test_the_flag_is_meaningless_without_rebuild(self):
        out = self.reg.run("--discard-placement", check=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn("--rebuild", out.stderr)


class Consented(RegisterCase):
    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()
        # A drag made in the Obsidian interface and never reconciled — the day file still
        # says ▶, so a re-derivation from it is what discards the drag.
        card = self.reg.card_for("20260801-02")
        text = self.reg.board_text().replace(card + "\n", "")
        self.reg.board.write_text(
            text.replace("## 🔴 Blocked\n", "## 🔴 Blocked\n\n" + card + "\n", 1)
        )
        # This card has been sitting in its lane since the 1st, not since today.
        led = json.loads(self.reg.ledger.read_text())
        for entry in led["placed"].values():
            entry["since"] = "2026-08-01"
        self.reg.ledger.write_text(json.dumps(led, indent=2))

    def since(self) -> dict:
        return {k: v.get("since") for k, v in json.loads(self.reg.ledger.read_text())["placed"].items()}

    def test_it_runs_and_re_places_every_card_at_its_day_file_lane(self):
        self.assertEqual("🔴 Blocked", self.reg.column_of("20260801-02"))
        out = self.reg.run("--rebuild", "--discard-placement")
        self.assertEqual(0, out.returncode)
        self.assertIn("rebuilding", out.stdout)
        self.assertEqual(
            "▶️ Next", self.reg.column_of("20260801-02"), "the re-derivation did not happen"
        )

    def test_it_does_not_reset_the_staleness_clock(self):
        """Consenting to lose placement is not consenting to be told the board is healthy.

        `since` answers 'how long has this sat here', and a rebuild re-derives placement —
        it does not make the work younger. Stamping the run date over it turns `--status`
        from a report into a reassurance.
        """
        self.assertEqual({"20260801-01": "2026-08-01", "20260801-02": "2026-08-01"}, self.since())
        before = self.reg.run("--status", "--brief").stdout
        self.assertIn("stale", before)

        self.reg.run("--rebuild", "--discard-placement")

        self.assertEqual(
            {"20260801-01": "2026-08-01", "20260801-02": "2026-08-01"},
            self.since(),
            "--rebuild stamped the run date over every ledger `since`",
        )
        self.assertIn("stale", self.reg.run("--status", "--brief").stdout)

    def test_a_card_first_seen_by_the_rebuild_is_stamped_today(self):
        """Preserving `since` is a carry-forward, not a refusal to record. A card the
        ledger has never seen still gets a clock."""
        self.reg.day("2026-08-02", "## More\n\n- [ ] ▶ brand new\n")
        self.reg.run("--rebuild", "--discard-placement")
        self.assertIsNotNone(self.since()["20260802-01"])
        self.assertNotEqual("2026-08-01", self.since()["20260802-01"])

    def test_a_consented_rebuild_still_discards_block_anchors(self):
        """DECISION, pinned: the gate is the whole remedy, and anchors are not rescued.

        Carrying them through would make `--rebuild` look safe while it still throws away
        every column placement — a half-rescue that invites exactly the mistaken reach this
        gate exists to stop. `--refresh` is the verb that carries an anchor, because it also
        keeps the placement; that pairing is the thing worth keeping unambiguous.
        """
        self.reg.board.write_text(
            self.reg.board_text().replace("- [ ] fix the sync", "- [ ] fix the sync ^wr7f3a21", 1)
        )
        self.reg.run("--rebuild", "--discard-placement")
        self.assertNotIn("^wr7f3a21", self.reg.board_text())

    def test_a_dry_run_rebuild_writes_nothing(self):
        before = self.reg.checksum()
        out = self.reg.run("--rebuild", "--discard-placement", "--dry-run")
        self.assertEqual(0, out.returncode)
        self.assertUnchanged(before, "--rebuild --dry-run")


class MultiBoard(RegisterCase):
    """The pre-existing refusal, pinned — including that the new flag does not unlock it.

    Re-partitioning cards across FILES in one pass is a different and worse hazard than
    re-placing them within one: a mis-scoped track silently relocates its whole slice into
    a file nobody was watching.
    """

    CONTRACT_EXTRA = SPLIT

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY + "- [ ] ▶ paint the hallway\n")
        self.reg.run()

    def test_it_refuses_and_exits_non_zero(self):
        before = self.reg.checksum()
        out = self.reg.run("--rebuild", check=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn("2 boards", out.stderr)
        self.assertUnchanged(before, "the refused multi-board --rebuild")

    def test_the_flag_does_not_unlock_it(self):
        out = self.reg.run("--rebuild", "--discard-placement", check=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn("--migrate --apply", out.stderr)
