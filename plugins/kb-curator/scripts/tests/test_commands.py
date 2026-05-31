"""End-to-end tests that exercise the CLI via `cli.main`.

These are the most valuable tests for an AI feedback loop: each one
builds a tiny vault, invokes a CLI command exactly as a user would, and
asserts on the resulting filesystem state. A regression in any command
surfaces as a focused failure here.
"""

from __future__ import annotations

import json

from kb_curator.cli import main


def run(*argv: str) -> int:
    """Invoke the CLI like the shell entrypoint would."""
    return main(list(argv))


# ---------------------------------------------------------------------------
# scan + audit
# ---------------------------------------------------------------------------

class TestScanAndAudit:
    def test_clean_vault_audits_with_no_errors(self, populated_vault, capsys):
        # populated_vault already has correct frontmatter; audit should pass.
        rc = run("--config", str(populated_vault.config_path), "audit", "--json")
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)["summary"]
        assert summary.get("error", 0) == 0

    def test_audit_flags_missing_frontmatter(self, vault, capsys):
        vault.config()
        vault.raw("01-Foo/Alpha/raw.md", "# No frontmatter here\n")
        rc = run("--config", str(vault.config_path), "audit", "--json")
        assert rc == 1  # error → non-zero
        findings = json.loads(capsys.readouterr().out)["findings"]
        assert any("missing frontmatter" in f["message"] for f in findings)

    def test_audit_flags_unknown_category(self, vault, capsys):
        vault.config()
        vault.note("01-Foo/Alpha/n.md", title="N", category="not-in-taxonomy",
                    tags=["not-in-taxonomy"])
        rc = run("--config", str(vault.config_path), "audit", "--json")
        assert rc == 1
        msgs = [f["message"] for f in json.loads(capsys.readouterr().out)["findings"]]
        assert any("not in taxonomy" in m for m in msgs)


# ---------------------------------------------------------------------------
# apply (frontmatter repair)
# ---------------------------------------------------------------------------

class TestApply:
    def test_normalises_existing_tags_and_mirrors_category(self, vault, capsys):
        vault.config()
        # Title-case tags + wrong first tag.
        vault.raw("01-Foo/Alpha/n.md",
                   "---\ntitle: N\ncategory: alpha\ntags:\n  - Foo\n  - bar\n---\n\nbody\n")
        path = str(vault.root / "01-Foo/Alpha/n.md")
        rc = run("--config", str(vault.config_path), "apply", path)
        assert rc == 0
        new = (vault.root / "01-Foo/Alpha/n.md").read_text()
        # alpha is first; tags are kebab-case.
        assert "tags:\n  - alpha\n" in new
        assert "  - foo\n" in new

    def test_dry_run_does_not_write(self, vault, capsys):
        vault.config()
        p = vault.note("01-Foo/Alpha/n.md", title="N", category="alpha", tags=["alpha"])
        original = p.read_text()
        rc = run("--config", str(vault.config_path), "apply", str(p), "--dry-run")
        assert rc == 0
        assert p.read_text() == original


# ---------------------------------------------------------------------------
# enrich (idempotence is critical)
# ---------------------------------------------------------------------------

class TestEnrich:
    def test_adds_pillar_and_topic(self, vault):
        vault.config(dates_source="none")  # avoid touching git
        p = vault.note("01-Foo/Alpha/Topic/n.md",
                        title="N", category="alpha", tags=["alpha"])
        run("--config", str(vault.config_path), "enrich")
        text = p.read_text()
        assert "pillar: foo" in text
        assert "topic: topic" in text

    def test_idempotent(self, vault):
        """Critical property: a second `enrich` must not mutate anything."""
        vault.config(dates_source="none")
        p = vault.note("01-Foo/Alpha/n.md", title="N", category="alpha", tags=["alpha"])
        run("--config", str(vault.config_path), "enrich")
        first = p.read_text()
        run("--config", str(vault.config_path), "enrich")
        assert p.read_text() == first


# ---------------------------------------------------------------------------
# links check
# ---------------------------------------------------------------------------

class TestLinks:
    def test_resolves_when_target_exists(self, populated_vault, capsys):
        rc = run("--config", str(populated_vault.config_path), "links", "check")
        assert rc == 0
        assert "All wiki-links resolve" in capsys.readouterr().out

    def test_flags_broken_link(self, vault, capsys):
        vault.config()
        vault.note("01-Foo/Alpha/a.md", title="A", category="alpha", tags=["alpha"],
                    body="# A\n\nSee [[Does Not Exist]] for more.")
        rc = run("--config", str(vault.config_path), "links", "check", "--json")
        assert rc == 1
        broken = json.loads(capsys.readouterr().out)["broken"]
        assert any(b["target"] == "Does Not Exist" for b in broken)


# ---------------------------------------------------------------------------
# naming check
# ---------------------------------------------------------------------------

class TestNaming:
    def test_proposes_rename_when_title_differs(self, vault, capsys):
        vault.config()
        vault.note("01-Foo/Alpha/old name.md",
                    title="New Name", category="alpha", tags=["alpha"])
        rc = run("--config", str(vault.config_path), "naming", "check", "--json")
        assert rc == 0
        issues = json.loads(capsys.readouterr().out)["issues"]
        assert any(i["proposed_stem"] == "New Name" for i in issues)

    def test_rename_rewrites_inbound_links(self, vault):
        vault.config()
        a = vault.note("01-Foo/Alpha/a.md", title="A New Title",
                        category="alpha", tags=["alpha"], body="# A\n")
        b = vault.note("01-Foo/Alpha/b.md", title="B", category="alpha",
                        tags=["alpha"], body="# B\n\nSee [[a]] for more.")
        rc = run("--config", str(vault.config_path), "naming", "rename",
                 "--path", str(a))
        assert rc == 0
        # File renamed; inbound link rewritten.
        assert not a.exists()
        assert (vault.root / "01-Foo/Alpha/A New Title.md").exists()
        assert "[[A New Title]]" in b.read_text()


# ---------------------------------------------------------------------------
# emojis apply
# ---------------------------------------------------------------------------

class TestEmojis:
    def test_prefixes_h1(self, vault):
        vault.config(emojis={"alpha": "🅰️"})
        p = vault.note("01-Foo/Alpha/n.md", title="N",
                        category="alpha", tags=["alpha"], body="# N\n\nbody")
        run("--config", str(vault.config_path), "emojis", "apply")
        assert "# 🅰️ N" in p.read_text()

    def test_idempotent(self, vault):
        vault.config(emojis={"alpha": "🅰️"})
        p = vault.note("01-Foo/Alpha/n.md", title="N",
                        category="alpha", tags=["alpha"], body="# N\n\nbody")
        run("--config", str(vault.config_path), "emojis", "apply")
        first = p.read_text()
        run("--config", str(vault.config_path), "emojis", "apply")
        # Second run must not double-prefix.
        assert p.read_text() == first
        assert first.count("🅰️") == 1


# ---------------------------------------------------------------------------
# tags suggest
# ---------------------------------------------------------------------------

class TestTagsSuggest:
    def test_keyword_rule_fires(self, vault, capsys):
        vault.config(inference_rules=[
            {"tag": "performance", "keywords": ["latency", "throughput"]},
        ])
        p = vault.note("01-Foo/Alpha/n.md", title="N", category="alpha",
                        tags=["alpha"], body="# N\n\nMeasured p99 latency under load.")
        rc = run("--config", str(vault.config_path), "tags", "suggest",
                 str(p), "--json")
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert "performance" in out["suggestions"]

    def test_apply_writes_tag(self, vault):
        vault.config(inference_rules=[
            {"tag": "performance", "keywords": ["latency"]},
        ])
        p = vault.note("01-Foo/Alpha/n.md", title="N", category="alpha",
                        tags=["alpha"], body="# N\n\nlatency study")
        run("--config", str(vault.config_path), "tags", "suggest", str(p), "--apply")
        assert "performance" in p.read_text()


# ---------------------------------------------------------------------------
# themes detect
# ---------------------------------------------------------------------------

class TestThemes:
    def test_finds_co_occurring_cluster(self, vault, capsys):
        vault.config()
        # Three notes that co-tag {x, y, z} — should cluster.
        for i in range(3):
            vault.note(f"01-Foo/Alpha/n{i}.md", title=f"N{i}",
                       category="alpha", tags=["alpha", "x", "y", "z"])
        rc = run("--config", str(vault.config_path), "themes", "detect",
                 "--min-cooccurrence", "2", "--json")
        assert rc == 0
        clusters = json.loads(capsys.readouterr().out)["clusters"]
        # x, y, z all co-occur and aren't excluded (not category slugs).
        assert any({"x", "y", "z"} <= set(c) for c in clusters)


# ---------------------------------------------------------------------------
# taxonomy init (auto-discovery)
# ---------------------------------------------------------------------------

class TestTaxonomyInit:
    def test_discovers_numbered_pillars(self, vault, tmp_path):
        vault.config()  # initial config so cli.main loads
        # Carve up a separate vault tree to discover.
        new_root = tmp_path / "fresh"
        (new_root / "01-Engineering" / "Backend").mkdir(parents=True)
        (new_root / "02-Design" / "UX").mkdir(parents=True)
        (new_root / "01-Engineering" / "Backend" / "n.md").write_text(
            "---\ntitle: N\ncategory: backend\ntags: [backend, perf]\n---\n\nbody"
        )
        out = tmp_path / "out.yaml"
        rc = run("--config", str(vault.config_path),
                 "taxonomy", "init", "--root", str(new_root), "--out", str(out))
        assert rc == 0
        text = out.read_text()
        assert "01-Engineering" in text
        assert "02-Design" in text

    def test_custom_pillar_pattern(self, vault, tmp_path):
        vault.config()
        new_root = tmp_path / "letters"
        (new_root / "A-Foundations" / "Core").mkdir(parents=True)
        out = tmp_path / "out.yaml"
        rc = run("--config", str(vault.config_path),
                 "taxonomy", "init", "--root", str(new_root),
                 "--pillar-pattern", r"^[A-Z]-(.+)$", "--out", str(out))
        assert rc == 0
        text = out.read_text()
        assert "A-Foundations" in text
