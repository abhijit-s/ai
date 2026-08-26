"""`--probe`, kept entirely offline.

The probe resolves two kinds of reference: GitHub issues and pull requests, which need the
network, and canon documents, which are files. Only the second is exercised here — the
first is covered at the parsing boundary instead, because a test that shelled out to `gh`
would be a test of the machine's credentials rather than of the engine.

What is actually under test is the part that has judgement in it: a reference in an item's
own text BINDS to that card and drives a proposal; one in the section's shared prose is
cited by every card under the heading, so it is advisory only.
"""

from __future__ import annotations

from harness import RegisterCase, engine


class RefExtraction(RegisterCase):
    def test_an_unknown_repo_shorthand_is_ignored(self):
        cfg = {"probe": {"repos": {"app": "Easygo-Surge/app"}}}
        refs = engine().extract_refs(cfg, "fixes app#733 and maybe widget#12")
        self.assertEqual([("issue", "Easygo-Surge/app#733")], refs)

    def test_a_canon_path_is_recognised_by_shape(self):
        refs = engine().extract_refs({"probe": {"repos": {}}}, "see 40_decisions/adr/ADR-079-x.md")
        self.assertEqual([("canon", "40_decisions/adr/ADR-079-x.md")], refs)

    def test_references_are_de_duplicated_in_first_seen_order(self):
        cfg = {"probe": {"repos": {"app": "Easygo-Surge/app"}}}
        refs = engine().extract_refs(cfg, "app#1 then app#2 then app#1 again")
        self.assertEqual(
            [("issue", "Easygo-Surge/app#1"), ("issue", "Easygo-Surge/app#2")], refs
        )


class CanonProposals(RegisterCase):
    def setUp(self) -> None:
        super().setUp()
        self.canon = self._tmp / "canon"
        (self.canon / "40_decisions").mkdir(parents=True)
        self.reg.append_contract(f'[probe]\ncanon_roots = ["{self.canon}"]\n')

    def doc(self, name: str, readiness: str) -> None:
        (self.canon / "40_decisions" / name).write_text(
            f"---\nartifact_readiness: {readiness}\ntitle: a doc\n---\n\nbody\n"
        )

    def test_a_terminal_item_bound_reference_becomes_a_proposal(self):
        self.doc("ADR-079-done.md", "implemented")
        self.reg.day(
            "2026-08-01", "## S\n\n- [ ] ⏳ land 40_decisions/ADR-079-done.md\n"
        )
        self.reg.run()
        out = self.reg.run("--probe")
        self.assertIn("proposal(s)", out.stdout)
        self.assertIn("20260801-01", out.stdout)
        self.assertIn("artifact_readiness=implemented", out.stdout)
        self.assertIn("--move 20260801-01=", out.stdout)
        # PROPOSES only: status is the board's field.
        self.assertEqual("⏳ In progress", self.reg.column_of("20260801-01"))

    def test_an_open_reference_yields_no_proposal(self):
        self.doc("ADR-080-open.md", "draft")
        self.reg.day("2026-08-01", "## S\n\n- [ ] ⏳ land 40_decisions/ADR-080-open.md\n")
        self.reg.run()
        out = self.reg.run("--probe")
        self.assertIn("no proposals", out.stdout)

    def test_a_section_bound_reference_is_advisory_not_a_proposal(self):
        self.doc("ADR-081-done.md", "shipped")
        self.reg.day(
            "2026-08-01",
            "## S\n\nBackground: 40_decisions/ADR-081-done.md sets the direction.\n\n"
            "- [ ] ⏳ do the follow-up work\n",
        )
        self.reg.run()
        out = self.reg.run("--probe")
        self.assertIn("no proposals", out.stdout)
        self.assertIn("section context", out.stdout)
        self.assertIn("review, do not assume", out.stdout)

    def test_a_done_card_is_not_probed(self):
        self.doc("ADR-082-done.md", "implemented")
        self.reg.day("2026-08-01", "## S\n\n- [x] land 40_decisions/ADR-082-done.md\n")
        self.reg.run()
        out = self.reg.run("--probe")
        self.assertIn("probing 0 open card(s)", out.stdout)

    def test_an_unresolvable_reference_is_unknown_not_done(self):
        self.reg.day("2026-08-01", "## S\n\n- [ ] ⏳ land 40_decisions/ADR-999-absent.md\n")
        self.reg.run()
        out = self.reg.run("--probe")
        self.assertIn("could not be resolved", out.stdout)
        self.assertIn("treat as unknown, not as done", out.stdout)
        self.assertIn("no proposals", out.stdout)
