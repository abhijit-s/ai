"""The verbs that must not write, and the two output contracts something else depends on.

Read-only is asserted by CHECKSUM over the whole register, never by an absence of output:
a verb can be perfectly quiet on stdout and still have stamped an id into a day file.
"""

from __future__ import annotations

import json

from harness import RegisterCase

DAY = """## Tooling

- [ ] ▶ fix the sync ::work-register
- [ ] ⏳ write the tests ::work-register
- [ ] ▶ a thing with no track at all
- [x] ship the thing ::work-register
"""

SCOPED = """
[scope]
default = "work"
suppress_default = true
"""


class ReadOnlyVerbs(RegisterCase):
    CONTRACT_EXTRA = SCOPED

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()
        self.before = self.reg.checksum()

    def test_list_writes_nothing(self):
        for argv in [
            ("--list",),
            ("--list", "--open"),
            ("--list", "--json"),
            ("--list", "--track", "work-register"),
            ("--list", "--column", "Next"),
        ]:
            with self.subTest(argv=argv):
                self.reg.run(*argv)
                self.assertUnchanged(self.before, f"--list {argv}")

    def test_show_writes_nothing(self):
        self.reg.run("--show", "20260801-01")
        self.assertUnchanged(self.before, "--show")

    def test_probe_writes_nothing(self):
        # Offline by construction: this contract declares no `[probe] repos`, so no card's
        # text yields a GitHub reference and nothing reaches the network.
        self.reg.run("--probe")
        self.assertUnchanged(self.before, "--probe")

    def test_status_and_show_config_write_nothing(self):
        self.reg.run("--status")
        self.reg.run("--status", "--brief")
        self.reg.run("--show-config")
        self.assertUnchanged(self.before, "the reporting verbs")

    def test_every_dry_run_writes_nothing(self):
        for argv in [
            ("--dry-run",),
            ("--refresh", "--dry-run"),
            ("--reconcile", "--dry-run"),
            ("--archive", "--keep", "0", "--dry-run"),
            ("--move", "20260801-01=Blocked", "--dry-run"),
            ("--migrate", "--dry-run"),
        ]:
            with self.subTest(argv=argv):
                self.reg.run(*argv)
                self.assertUnchanged(self.before, f"{argv} --dry-run")

    def test_a_dry_run_sync_still_mints_ids_in_memory(self):
        """The report has to be about the real placement, or a dry run is worth nothing."""
        self.reg.day("2026-08-02", "## More\n\n- [ ] ⏳ an unstamped item\n")
        before = self.reg.checksum()
        out = self.reg.run("--dry-run")
        self.assertIn("20260802-01", out.stdout)
        self.assertIn("In progress", out.stdout)
        self.assertUnchanged(before, "a dry-run sync")


class OutputContracts(RegisterCase):
    CONTRACT_EXTRA = SCOPED

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def test_status_brief_emits_exactly_one_line(self):
        """The SessionStart hook consumes this and is fail-open, so a second line does not
        break loudly — it degrades the hook's envelope silently. Pin the shape."""
        out = self.reg.run("--status", "--brief")
        self.assertEqual("", out.stderr)
        self.assertEqual(1, len(out.stdout.strip().splitlines()), repr(out.stdout))
        self.assertIn("work-register [test]", out.stdout)

    def test_status_brief_is_one_line_even_with_findings_to_report(self):
        led = json.loads(self.reg.ledger.read_text())
        for entry in led["placed"].values():
            entry["since"] = "2020-01-01"
        self.reg.ledger.write_text(json.dumps(led, indent=2))
        out = self.reg.run("--status", "--brief")
        self.assertIn("stale", out.stdout)
        self.assertEqual(1, len(out.stdout.strip().splitlines()), repr(out.stdout))

    def test_list_json_stdout_is_nothing_but_the_document(self):
        out = self.reg.run("--list", "--json")
        rows = json.loads(out.stdout)
        self.assertEqual(4, len(rows))
        self.assertEqual(
            {"id", "date", "group", "column", "track", "scope", "tags", "done", "text"},
            set(rows[0]),
        )

    def test_show_on_an_unknown_id_exits_non_zero(self):
        out = self.reg.run("--show", "20991231-99", check=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn("no card with id", out.stderr)

    def test_show_prints_the_section_prose_behind_a_card(self):
        self.reg.day(
            "2026-08-03",
            "## Deciding\n\nThe reasoning that will not fit on a card.\n\n- [ ] 💬 pick one\n",
        )
        self.reg.run()
        out = self.reg.run("--show", "20260803-01")
        self.assertIn("The reasoning that will not fit on a card.", out.stdout)
        self.assertIn("pick one", out.stdout)


class ScopeFilterWart(RegisterCase):
    """A trackless card renders to the default board but does NOT match that board's scope.

    DECISION, not a defect to fix here. Scope is derived from a track, and a card with no
    track honestly has no scope — `scope_for` returns "" rather than guessing the default.
    The render is separately TOTAL: `board_for` falls back to the default board so nothing
    can become invisible. So the two disagree by design, and `--list --scope work` is
    narrower than "the work board".

    Pinned because the alternative — making the filter fall back to the default scope —
    would make `--scope` mean something different from the `scope` field `--list --json`
    emits, and that field is a published contract. If this is ever changed, this test is
    where the decision gets re-argued.
    """

    CONTRACT_EXTRA = SCOPED

    def setUp(self) -> None:
        super().setUp()
        self.reg.day("2026-08-01", DAY)
        self.reg.run()

    def test_scope_default_does_not_match_a_trackless_card(self):
        rows = json.loads(self.reg.run("--list", "--json").stdout)
        trackless = [r for r in rows if r["text"] == "a thing with no track at all"]
        self.assertEqual(1, len(trackless))
        self.assertEqual("", trackless[0]["scope"], "a trackless card must have no scope")

        scoped = json.loads(self.reg.run("--list", "--scope", "work", "--json").stdout)
        self.assertNotIn(
            "a thing with no track at all",
            [r["text"] for r in scoped],
            "the wart has been fixed — re-argue the decision in this test's docstring",
        )
        self.assertEqual(3, len(scoped))

    def test_the_trackless_card_is_nonetheless_on_the_default_board(self):
        self.assertIn("a thing with no track at all", self.reg.board_text())
