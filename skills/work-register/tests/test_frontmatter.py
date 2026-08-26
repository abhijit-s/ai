"""`--status` noticing a board whose frontmatter another writer has damaged.

The render is not the only thing that writes to the board file. Obsidian's own Properties
editor re-serialises YAML frontmatter whenever a property is edited, and a formatter
plugin will rewrite it unasked — which is how a live board lost `kanban-plugin: board`
mid-session and stopped being rendered as a board at all.

Half the fix already existed and is pinned here rather than built: `render_board` writes
`[board.frontmatter]` from the contract on every pass, so any sync REPAIRS the damage.
What is under test is the other half — NOTICING. Between syncs the board can sit
unrenderable and nothing says so, and a silent repair is still a silent problem: the owner
finds out by opening the board.

Two properties the tests below defend, both learned from what `--status` is consumed by:

  * damage has to make the verdict UNHEALTHY, or the SessionStart nudge (which greps the
    verdict for ⚠️) stays quiet on exactly the failure it exists to surface;
  * `--status --brief` has to stay ONE line, because that hook is fail-open — a second
    line degrades its envelope silently rather than breaking loudly.

And `--status` must write nothing at all. Repair belongs to sync; a status verb that
mutates is one nobody can trust. Asserted by checksum over the whole register, never by an
absence of stdout.
"""

from __future__ import annotations

from datetime import datetime, timezone

from harness import RegisterCase

# Two keys, so a test can damage one and leave the other intact. `topic` is here to prove
# the checked set is VOCABULARY: the engine names no key, it checks whatever the contract
# declares, so a corpus-specific key is checked exactly like the plugin's own.
CONTRACT = """
[board.frontmatter]
"kanban-plugin" = "board"
topic           = "work-register-board"
"""

DAY = """## Tooling

- [ ] ▶ fix the sync
- [ ] ⏳ write the tests
"""

# The phrase the report leads with. Asserted as a constant so a test cannot pass by
# matching some other line that happens to carry the word "frontmatter".
REPORTED = "frontmatter"


def today() -> str:
    """UTC, matching the `TZ=UTC` the harness pins into the engine's environment."""
    return datetime.now(timezone.utc).date().isoformat()


class FrontmatterCase(RegisterCase):
    CONTRACT_EXTRA = CONTRACT
    DAY_DATE = "2026-08-01"

    def setUp(self) -> None:
        super().setUp()
        self.reg.day(self.DAY_DATE, DAY)
        self.reg.run()

    # --- standing in for the other writer ------------------------------------
    def frontmatter_lines(self) -> list[str]:
        return self.reg.board_text().split("---\n", 2)[1].rstrip("\n").splitlines()

    def rewrite_frontmatter(self, lines: list[str] | None) -> None:
        """Replace the board's frontmatter block, or remove it entirely with None."""
        body = self.reg.board_text().split("---\n", 2)[2]
        block = "" if lines is None else "---\n" + "\n".join(lines) + "\n---\n"
        self.reg.board.write_text(block + body, encoding="utf-8")

    def drop(self, key: str) -> None:
        self.rewrite_frontmatter(
            [line for line in self.frontmatter_lines() if not line.startswith(f"{key}:")]
        )

    def change(self, key: str, value: str) -> None:
        self.rewrite_frontmatter([
            f"{key}: {value}" if line.startswith(f"{key}:") else line
            for line in self.frontmatter_lines()
        ])

    def status(self) -> str:
        return self.reg.run("--status").stdout


class Detection(FrontmatterCase):
    def test_a_deleted_key_is_reported(self):
        """The `kanban-plugin` case: without it the file stops rendering as a board."""
        self.drop("kanban-plugin")
        out = self.status()
        self.assertIn(REPORTED, out)
        self.assertIn(self.reg.board.name, out)
        self.assertIn("kanban-plugin", out)
        self.assertIn("missing", out)

    def test_a_changed_value_is_reported(self):
        """Damage is not only deletion — a key rewritten to another value contradicts the
        contract just as completely, and the board is just as broken."""
        self.change("kanban-plugin", "basic")
        out = self.status()
        self.assertIn(REPORTED, out)
        self.assertIn("kanban-plugin", out)
        self.assertIn("basic", out, "the report does not say what the board actually says")
        self.assertIn("board", out, "the report does not say what the contract declares")

    def test_a_key_expanded_into_a_block_list_is_reported(self):
        """Exactly the shape Obsidian's Properties editor produces: the scalar the contract
        declares becomes an empty value with the list underneath. The board no longer says
        what the contract says, so it is a contradiction rather than an equivalent."""
        self.rewrite_frontmatter(
            [
                line if not line.startswith("topic:") else "topic:\n  - work-register-board"
                for line in self.frontmatter_lines()
            ]
        )
        out = self.status()
        self.assertIn(REPORTED, out)
        self.assertIn("topic", out)
        # Named rather than printed as '': an empty value IS the block-list signature, and
        # the report has to be legible to whoever has to act on it.
        self.assertIn("no value on its own line", out)

    def test_a_board_with_no_frontmatter_at_all_is_reported(self):
        self.rewrite_frontmatter(None)
        out = self.status()
        self.assertIn(REPORTED, out)
        for key in ("kanban-plugin", "topic"):
            self.assertIn(key, out, f"{key} was not reported as missing")

    def test_a_corpus_declared_key_is_checked_like_any_other(self):
        """Vocabulary in config: the engine names no key, so a key that means nothing to
        the Kanban plugin is checked exactly like the one that does."""
        self.change("topic", "something-else")
        out = self.status()
        self.assertIn(REPORTED, out)
        self.assertIn("topic", out)
        self.assertNotIn(
            "kanban-plugin: missing", out, "an intact key was reported alongside the damaged one"
        )

    def test_an_intact_board_is_not_reported(self):
        out = self.status()
        self.assertNotIn(REPORTED, out, "an undamaged board was reported as damaged")

    def test_the_report_points_at_the_verb_that_repairs_it(self):
        """A finding with no remedy attached is a finding the owner has to research."""
        self.drop("kanban-plugin")
        self.assertIn("sync", self.status())


class Verdict(FrontmatterCase):
    """Damage has to reach the one-line verdict, because that line is what the hook reads.

    The day file is dated TODAY so nothing else can make the register unhealthy: a fixed
    past date would leave `last capture Nd ago` in the verdict and this class could not
    tell a frontmatter finding from the calendar.
    """

    DAY_DATE = today()

    def test_an_intact_register_is_healthy(self):
        self.assertIn("✅", self.reg.run("--status", "--brief").stdout)

    def test_damage_makes_the_verdict_unhealthy(self):
        """The SessionStart nudge greps the verdict for ⚠️ and stays silent otherwise, so
        a board that no longer renders has to move the verdict or nobody is told."""
        self.drop("kanban-plugin")
        out = self.reg.run("--status", "--brief").stdout
        self.assertIn("⚠️", out)
        self.assertIn("board", out)

    def test_brief_is_exactly_one_line_intact_and_damaged(self):
        for label, damage in (("intact", lambda: None), ("damaged", lambda: self.drop("topic"))):
            with self.subTest(board=label):
                damage()
                out = self.reg.run("--status", "--brief")
                self.assertEqual("", out.stderr)
                self.assertEqual(
                    1, len(out.stdout.strip().splitlines()), repr(out.stdout)
                )


class ReadOnly(FrontmatterCase):
    def test_status_repairs_nothing_it_finds(self):
        """Repair belongs to sync. Checksum, not silence: a verb can be quiet on stdout and
        still have rewritten the board it was asked to report on."""
        self.drop("kanban-plugin")
        before = self.reg.checksum()
        self.assertIn(REPORTED, self.status())
        self.reg.run("--status", "--brief")
        self.assertUnchanged(before, "--status on a damaged board")
        self.assertNotIn("kanban-plugin", self.reg.frontmatter())


class Repair(FrontmatterCase):
    """The existing half. `render_board` writes the frontmatter from the contract on every
    pass, so a plain sync already repairs the damage — pinned so the detection above can
    keep pointing at it."""

    def test_a_plain_sync_restores_every_declared_key(self):
        self.rewrite_frontmatter(None)
        self.reg.run()
        after = self.reg.frontmatter()
        self.assertEqual("board", after.get("kanban-plugin"))
        self.assertEqual("work-register-board", after.get("topic"))

    def test_status_goes_quiet_once_the_sync_has_repaired_it(self):
        self.drop("kanban-plugin")
        self.assertIn(REPORTED, self.status())
        self.reg.run()
        self.assertNotIn(REPORTED, self.status())
