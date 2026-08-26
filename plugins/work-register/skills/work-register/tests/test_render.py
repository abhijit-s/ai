"""What every board write must still be true of afterwards.

The board is derived and disposable, which is exactly why the render is dangerous: every
write replaces the whole file, so anything the render does not know to carry forward is
gone. Three things live in that blind spot and each has already cost something —
frontmatter the plugin needs to treat the file as a board at all, the settings block the
plugin itself writes into, and the block anchors the owner creates by copying a link.

Every verb exercised here is one that genuinely REWRITES the board file. `--refresh` is
the trap: it writes only the boards it actually changed, so a refresh over an unchanged
day file touches nothing and would make a render assertion silently vacuous. Each refresh
below is therefore preceded by a real text correction.
"""

from __future__ import annotations

import json

from harness import RegisterCase

# The key set the live contract declares, with representative values. The NAMES are the
# contract here — `move-dates`, `inline-metadata-position` and `archive-with-date` are
# plugin-written keys that a render rebuilding the block from a shorter default would
# silently drop.
TEN_KEYS = {
    "kanban-plugin": "board",
    "move-dates": True,
    "inline-metadata-position": "footer",
    "archive-with-date": True,
    "show-checkboxes": True,
    "move-task-metadata": True,
    "move-tags": True,
    "show-relative-date": False,
    "link-date-to-daily-note": False,
    "tag-colors": [{"tagKey": "#prod", "color": "rgba(255,255,255,1)"}],
}
TEN_KEY_CONTRACT = (
    "[kanban]\nsettings = '''" + json.dumps(TEN_KEYS, separators=(",", ":")) + "'''\n"
)

DAY = """## Tooling

- [ ] ▶ fix the sync
- [ ] ⏳ write the tests
- [x] ship the thing
"""


class BoardWriteCase(RegisterCase):
    CONTRACT_EXTRA = TEN_KEY_CONTRACT

    def setUp(self) -> None:
        super().setUp()
        self.day = self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def real_refresh(self) -> None:
        """A refresh that actually rewrites the board, because the text really changed."""
        self.day.write_text(self.day.read_text().replace("write the tests", "write them"))
        out = self.reg.run("--refresh")
        assert "re-rendered" in out.stdout, out.stdout

    def writes(self) -> dict:
        return {
            "sync": lambda: self.reg.run(),
            "move": lambda: self.reg.run("--move", "20260801-01=Blocked"),
            "refresh": self.real_refresh,
            "archive": lambda: self.reg.run("--archive", "--keep", "0"),
        }


class RenderInvariants(BoardWriteCase):
    def test_frontmatter_carries_kanban_plugin_after_every_write(self):
        """Without `kanban-plugin: board` the file stops rendering as a board at all."""
        for label, write in self.writes().items():
            with self.subTest(verb=label):
                write()
                self.assertEqual(
                    "board",
                    self.reg.frontmatter().get("kanban-plugin"),
                    f"{label} dropped kanban-plugin from the frontmatter",
                )

    def test_declared_settings_keys_survive_every_write(self):
        for label, write in self.writes().items():
            with self.subTest(verb=label):
                write()
                self.assertEqual(
                    set(TEN_KEYS),
                    set(self.reg.settings()),
                    f"{label} changed the settings keys",
                )

    def test_refresh_carries_a_block_anchor_and_the_board_checkbox(self):
        """An `^id` is a live `[[BOARD#^id]]` target and the checkbox is the board's field.

        Both live only on the board, so a re-render driven by the day file has to read them
        back off the card it is replacing.
        """
        before = self.reg.card_for("20260801-01")
        self.reg.board.write_text(
            self.reg.board_text().replace(
                "- [ ] fix the sync", "- [x] fix the sync ^wr7f3a21", 1
            )
        )
        self.assertIn("^wr7f3a21", self.reg.board_text())

        # The day file owns the text, so a correction there is what drives the re-render.
        self.day.write_text(self.day.read_text().replace("fix the sync", "fix the sync properly"))

        out = self.reg.run("--refresh")
        self.assertIn("re-rendered", out.stdout)

        fresh = self.reg.card_for("20260801-01")
        self.assertIn("fix the sync properly", fresh, "the text correction did not reach the card")
        self.assertIn("^wr7f3a21", fresh, "--refresh destroyed the block anchor")
        self.assertTrue(
            fresh.lstrip().startswith("- [x]"), "--refresh reverted the board-owned checkbox"
        )
        self.assertNotEqual(before, fresh)

    def test_refresh_keeps_the_anchor_on_the_first_line(self):
        """`card_anchor` only recognises one at the end of the card's first line, so a
        carry that appended it anywhere else would break the very link it saved."""
        self.reg.board.write_text(
            self.reg.board_text().replace("- [ ] fix the sync", "- [ ] fix the sync ^wr7f3a21", 1)
        )
        self.day.write_text(self.day.read_text().replace("fix the sync", "fix it properly"))
        self.reg.run("--refresh")
        first = self.reg.card_for("20260801-01").split("\n")[0]
        self.assertTrue(first.endswith("^wr7f3a21"), first)


class SettingsMerge(BoardWriteCase):
    """The plugin writes into the settings block too, so the render cannot own it outright.

    The contract governs the keys it declares; anything else already in the block is the
    plugin's, and survives. That makes a setting toggled in the plugin's own interface
    durable without a config edit.
    """

    def plugin_writes(self, **extra) -> None:
        """Stand in for the Obsidian Kanban plugin saving its own settings into the board."""
        settings = self.reg.settings()
        settings.update(extra)
        blob = json.dumps(settings, separators=(",", ":"), ensure_ascii=False)
        head, fence, tail = self.reg.board_text().partition("%% kanban:settings\n```\n")
        _, _, rest = tail.partition("\n```")
        self.reg.board.write_text(head + fence + blob + "\n```" + rest)

    def test_an_undeclared_plugin_key_survives_the_next_write(self):
        self.plugin_writes(**{"lane-width": 320, "hide-card-count": True})
        for label, write in self.writes().items():
            with self.subTest(verb=label):
                write()
                after = self.reg.settings()
                self.assertEqual(320, after.get("lane-width"), f"{label} dropped a plugin key")
                self.assertIs(True, after.get("hide-card-count"))
                self.assertEqual(
                    "footer", after["inline-metadata-position"], f"{label} lost a declared key"
                )

    def test_the_contract_still_governs_a_key_it_declares(self):
        """Merging is not surrender: a declared key is the contract's answer, not the board's."""
        self.plugin_writes(**{"inline-metadata-position": "body"})
        self.reg.run()
        self.assertEqual("footer", self.reg.settings()["inline-metadata-position"])

    def test_merge_is_a_no_op_when_the_contract_declares_everything(self):
        """The live register's contract declares every key its board carries, so the merge
        must leave a settled board byte-identical."""
        before = self.reg.board_text()
        self.reg.run()
        self.reg.run()
        self.assertEqual(before, self.reg.board_text(), "the merge rewrote a settled board")

    def test_an_unreadable_settings_block_falls_back_to_the_contract(self):
        """A board is a file a human can also edit. Broken JSON is not a reason to refuse
        to render — the contract is the answer of record."""
        head, fence, tail = self.reg.board_text().partition("%% kanban:settings\n```\n")
        _, _, rest = tail.partition("\n```")
        self.reg.board.write_text(head + fence + "{not json at all" + "\n```" + rest)
        self.reg.run()
        self.assertEqual(set(TEN_KEYS), set(self.reg.settings()))
