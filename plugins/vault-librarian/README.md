# vault-librarian

A Claude Code plugin for curating Markdown knowledge vaults — Obsidian, mkdocs-style, or any directory tree of `.md` files with YAML frontmatter.

Catalogues, classifies, audits, repairs, renames, links, and tags notes. Driven by a single `taxonomy.yaml` you can hand-edit or auto-generate from an existing vault.

## What it does

- **Catalogue** the vault into a Pillar → Area → Sub-area → Topic → Note hierarchy.
- **Audit** drift between filesystem location, frontmatter, and the canonical taxonomy.
- **Classify** new notes — propose where they belong and what frontmatter they should carry.
- **Repair** frontmatter — fill missing fields, normalise casing, mirror the category as first tag.
- **Rename** files to match their titles, with automatic inbound-link rewrites across the vault.
- **Check & repair broken links** — Obsidian `[[wiki-links]]` or relative Markdown `[…](file.md)`.
- **Inject emoji sigils** into note H1s, configurable per category, idempotent.
- **Infer tags** from content via keyword rules; suggest or auto-attach.
- **Detect themes** by clustering co-occurring tags across the vault.
- **Enrich** frontmatter with derived fields (pillar, sub_area, topic, kind, created, updated).

Zero non-stdlib Python dependencies. Single file at `scripts/vault_librarian.py`. PyYAML used when present, scoped fallback parser otherwise.

## Install

```bash
claude plugin install https://github.com/abhijit-s/ai.git --plugin vault-librarian
```

Claude Code activates the skill whenever you mention the vault, frontmatter, tags, taxonomy, or any keyword in its description.

To use the CLI directly, find the cached plugin path and run:

```bash
python3 ~/.claude/plugins/cache/ai/vault-librarian/<version>/scripts/vault_librarian.py <command>
```

## Quick start on an existing vault

```bash
# 1. Scan an arbitrary vault and emit a starter config.
python3 scripts/vault_librarian.py taxonomy init \
    --root /path/to/your/vault \
    --out config/taxonomy.yaml

# 2. Open config/taxonomy.yaml — fill in the TODO central_questions,
#    curate the starter tag list, set link_syntax / slug_case as needed.

# 3. See what's in your vault.
python3 scripts/vault_librarian.py scan

# 4. Audit for drift.
python3 scripts/vault_librarian.py audit

# 5. Backfill derived frontmatter fields.
python3 scripts/vault_librarian.py enrich
```

## CLI reference

```
scan        Inventory the vault and report note counts per area.
audit       Find drift between path, frontmatter, and taxonomy.
classify    Suggest path / category / tags for one file.
apply       Write or repair frontmatter on one file.
enrich      Backfill pillar / sub_area / topic / kind / created / updated.
taxonomy    show | refresh | init — inspect, diff, or auto-generate config.
links       check | repair — find broken cross-references, repair safely.
naming      check | rename — propose canonical filenames, rename with link rewrite.
emojis      apply — inject configured emoji prefixes into note H1s.
tags        suggest — content-based tag inference from rules.
themes      detect — cluster co-occurring tags into proposed themes.
```

All commands take `--config <path>` (defaults to `config/taxonomy.yaml`) and `--root <path>` (overrides `vault.root` in config).

## Frontmatter schema

```yaml
---
title:    <Human title; matches the body H1 modulo emoji prefix>
pillar:   <slug; derived from top-level dir>
category: <slug; the area — required>
sub_area: <slug; optional, from numbered intermediate dir>
topic:    <slug; optional, deepest non-numbered intermediate dir>
kind:     <index | crash-course | deep-dive | reference>
created:  <YYYY-MM-DD; from git or filesystem>
updated:  <YYYY-MM-DD; same source>
tags:
  - <category-slug>             # mirrors category for grep-ability
  - <topical or cross-cutting>  # 1–5 additional tags
---
```

`title`, `category`, and `tags` are required. The rest is derived by `enrich` from path, git history, and tag content. Re-running `enrich` is idempotent.

## Configuration

`config/taxonomy.yaml` is the single source of policy. Sections:

| Section | What it controls |
| --- | --- |
| `vault` | Root path and excluded directories |
| `path_conventions` | Regex for pillar / sub-area detection; README filename |
| `link_syntax` | `obsidian` (`[[…]]`) / `markdown` (`[…](…md)`) / `none` |
| `slug_case` | `kebab` / `snake` / `camel` |
| `dates` | `git` / `mtime` / `none` |
| `frontmatter` | Required / derived / optional fields, casing rules, first-tag rule |
| `pillars` | The Pillar → Area tree, each with a `central_question` |
| `tags` | Controlled tag vocabulary |
| `naming` | Filename normalisation rules |
| `emojis` | Category → emoji sigil map |
| `inference_rules` | Keyword → tag rules for content-based tagging |

A `taxonomy init` run pre-fills the first eight; the last three you add by taste.

## Bootstrapping a new vault

```bash
# Generate a starter taxonomy from your existing vault.
python3 scripts/vault_librarian.py taxonomy init \
    --root ~/notes \
    --pillar-pattern '^[A-Z]-(.+)$' \   # accept letter-prefixed pillars
    --out ~/notes/.taxonomy.yaml

# Inspect:
python3 scripts/vault_librarian.py --config ~/notes/.taxonomy.yaml taxonomy show

# Audit (initially noisy — that's the point):
python3 scripts/vault_librarian.py --config ~/notes/.taxonomy.yaml audit
```

If your vault has no numeric prefixes, set `pillar_pattern: '^(.+)$'` in the generated config. If you don't use Obsidian wiki-links, set `link_syntax.type: markdown`. If you don't use git, set `dates.source: mtime`. Defaults match Obsidian conventions.

## Workflows

The skill ships ten named workflows in `SKILL.md`:

- **A — Catalogue the vault** (epistemology pass)
- **B — Classify a new note**
- **C — Repair existing frontmatter**
- **D — Evolve the taxonomy** (add a new area)
- **E — Bulk re-classification** (phased; link-safe)
- **F — Repair broken wiki-links**
- **G — Canonical filenames + safe rename**
- **H — Inject emoji sigils**
- **I — Content-based tag inference**
- **J — Theme detection** (vocabulary evolution)

Each workflow is a few commands plus the judgement around them. Detailed step-by-step in `references/workflows.md`.

## Architecture

```
vault-librarian/
├── skills/vault-librarian/
│   └── SKILL.md            # Agent guidance — when to activate, philosophy, workflows
├── README.md               # This file
├── config/
│   └── taxonomy.yaml       # Single source of policy
├── scripts/                # Python project; treat as project root for `make`
│   ├── vault_librarian.py       # 21-line CLI entrypoint shim
│   ├── vault_librarian/         # Package — eight focused modules, ~150 lines each
│   ├── tests/              # pytest suite — synthetic vault fixtures
│   ├── pyproject.toml
│   ├── Makefile            # `make test` / `make sanity`
│   ├── README.md           # CLI quick reference
│   └── DEVELOPMENT.md      # Maintainer notes
└── references/
    ├── conventions.md      # Frontmatter / naming / slug rules in detail
    └── workflows.md        # Step-by-step playbooks
```

The Python package is the deterministic mechanic; the `SKILL.md` carries the judgement. Re-implementing parsing inline in an agent loop is an anti-pattern — always shell out to `vault_librarian.py`.

For maintainers, see `scripts/DEVELOPMENT.md`. The test suite (`scripts/tests/`) doubles as executable documentation of the contract and runs in under a second — the intended AI feedback loop is `make test`.

## Compatibility

Designed for:
- Obsidian vaults (primary)
- mkdocs / Hugo / Jekyll content trees with YAML frontmatter
- Any tree of `.md` files with `---` delimited frontmatter

Not for:
- Flat-notebook tools (Logseq, Notion exports) — different structure
- Code-doc generators (Sphinx, JSDoc) — different purpose
- Org-mode

Python 3.10+, no required external dependencies. PyYAML used when available.

## Idempotence

Every write command is idempotent:

- `apply`, `enrich`, `emojis apply`, `tags suggest --apply` only write when the resulting file differs.
- Re-running on a clean vault is a safe no-op.
- `--dry-run` previews are available on every mutating command.

## License

MIT. Source at `plugins/vault-librarian/` in the [ai](https://github.com/abhijit-s/ai) repo.
