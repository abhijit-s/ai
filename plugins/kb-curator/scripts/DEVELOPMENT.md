# Development notes

For maintainers — humans and AI agents — touching the `kb_curator` Python package.

## Project shape

```
scripts/
├── kb_curator.py         # 21-line shim; just imports cli.main()
├── kb_curator/           # actual package
│   ├── __init__.py       # version
│   ├── cli.py            # argparse + dispatch table
│   ├── commands.py       # one `cmd_<verb>` per subcommand
│   ├── derivation.py     # classification, naming, kind/placement, git dates
│   ├── frontmatter.py    # Note model + parse/walk/write
│   ├── links.py          # wiki/markdown link parsing + Levenshtein
│   ├── model.py          # Pillar / Area / Taxonomy + config loader
│   ├── slugs.py          # slugify / is_slug
│   └── yaml_io.py        # YAML reader + frontmatter writer
├── tests/                # pytest suite — one file per module
├── pyproject.toml        # package metadata, optional dev deps
├── Makefile              # `make test` / `make sanity` / `make install`
├── README.md             # CLI quick reference (user-facing)
└── DEVELOPMENT.md        # this file
```

## Design principles

### 1. Mechanics in Python, judgement in the agent

The skill couples a deterministic CLI (`kb_curator.py`) with prose guidance (`SKILL.md`) plus configuration (`taxonomy.yaml`). When deciding where new behaviour belongs:

- **Filesystem walks, regex matches, frontmatter parsing → Python.** The CLI must do them the same way every time.
- **Where a note belongs, whether a new area is warranted, what tag captures recall best → agent or human.** Don't bake taste into Python.

If a feature feels like "knows what a good tag is", it probably wants to live in `inference_rules:` in the config (data) plus a generic rule engine in code, not a hard-coded function.

### 2. Config drives policy, code drives mechanism

`taxonomy.yaml` is the single source of policy. Code never embeds:
- Specific category slugs (those live in `pillars:`)
- The pillar regex (lives in `path_conventions.pillar_pattern`)
- The link syntax (lives in `link_syntax.type`)
- Slug case (lives in `slug_case`)
- The date source (lives in `dates.source`)

When you add a tunable, add a config field and a documented default — never a code constant that "users can patch".

### 3. Idempotence

Every write command must be safe to re-run:

- `apply`, `enrich`, `emojis apply`, `tags suggest --apply` only write when the resulting file differs from the current one.
- Tests assert this via the pattern: run twice, second run touches nothing (`test_idempotent` in `test_commands.py`).

If you add a write command, write the idempotence test first.

### 4. Stdlib by default

The runtime has no required external dependencies. PyYAML is auto-detected — if present, it's used for richer YAML fidelity; if absent, the scoped `_MiniYaml` reader in `yaml_io.py` handles the project's actual schema.

Adding a dependency is a non-trivial decision because the plugin ships into users' `~/.claude/plugins/` directories. If you must:

1. Make it optional (`[project.optional-dependencies]`).
2. Detect at import time and fall back gracefully.
3. Document the impact in `README.md`.

## Where to add new code

| New feature kind | Module |
| --- | --- |
| New CLI subcommand | `commands.py` + `cli.py` |
| A new YAML field consumed by existing commands | `model.py` (extend `Taxonomy` + `load_taxonomy`) |
| A new derivation rule for `enrich` | `derivation.py` |
| A new slug style | `slugs.py` |
| A new link syntax (e.g., reST `:doc:`) | `links.py` (extend `find_wiki_links`) |
| A new audit check | `commands.py > cmd_audit` |
| A new test | `tests/test_<module>.py` |

## Anti-patterns to avoid

- **Re-implementing YAML parsing or frontmatter splitting inline.** Always import from `yaml_io` / `frontmatter`. If the helper is wrong, fix it once.
- **Calling `git log` directly outside `derivation.py`.** That function knows about case-insensitive filesystems and rename-following; bypassing it loses those fixes.
- **Hard-coding the seven pillars or the user's slugs into tests.** Tests use `VaultBuilder` to construct minimal vaults; the production user's `taxonomy.yaml` is not the test fixture.
- **Adding state to module globals.** All state lives on the `Taxonomy` instance, which is passed explicitly.
- **Skipping `make test` before committing.** The suite is < 1 second; there's no excuse.

## Testing strategy

See `tests/README.md` for the contract. Highlights:

- Every command has at least one end-to-end test in `test_commands.py`.
- Pure functions get unit tests in module-named files.
- Tests build vaults via `VaultBuilder` (see `conftest.py`) — never check fixture files into the repo.
- `populated_vault` is the shared "normal small vault" — use it for cross-cutting tests; spin up a fresh `vault` for behaviour that needs specific shapes.

### Critical invariants pinned by tests

These are the contracts a refactor must not break:

1. `yaml_dump_frontmatter` produces fields in a fixed order (`title` → placement → provenance → tags → other). Pinned by `test_yaml_io.TestFrontmatterDump.test_field_order_is_canonical`.
2. `enrich` is idempotent. Pinned by `test_commands.TestEnrich.test_idempotent`.
3. `emojis apply` is idempotent. Pinned by `test_commands.TestEmojis.test_idempotent`.
4. `naming rename` rewrites inbound `[[wiki-links]]`. Pinned by `test_commands.TestNaming.test_rename_rewrites_inbound_links`.
5. Wiki-link patterns inside backticks are not flagged as broken. Pinned by `test_links.TestFindWikiLinks.test_skips_inline_code`.
6. LaTeX-shaped tokens (`M*k/N`) are not flagged as broken links. Pinned by `test_links.TestFindWikiLinks.test_drops_latex_lookalikes`.
7. Case-only file renames work on case-insensitive filesystems. (Manual — hard to unit-test cross-platform; verified by code review of `cmd_naming` two-step rename.)

## Common maintenance tasks

### Adding a new optional frontmatter field

1. Add the key to `frontmatter.derived` (or `optional`) in `taxonomy.yaml`.
2. If the value is derivable, add a derive function in `derivation.py` and call it from `cmd_enrich`.
3. Add the key to the `ORDERED` list in `yaml_io.py` so it appears in canonical position.
4. Write a test in `test_commands.py::TestEnrich`.

### Adding a new CLI command

1. Write the handler `cmd_<verb>` in `commands.py`. Return `0` on success, non-zero on error.
2. Register it in `cli.py`'s `DISPATCH` dict and add the subparser.
3. Document the command in `README.md`.
4. Add an end-to-end test in `test_commands.py`.
5. Add a workflow entry in `SKILL.md` if it's something the agent should reach for.

### Renaming a public symbol

The CLI surface is the stable API. Module-level Python symbols are not — but if you rename one used in `tests/` or `conftest.py`, update both.

## Performance

The whole suite + a vault scan should finish in well under 1 second for vaults up to ~1000 notes. If you write code that iterates the vault more than once per command, push back — it's almost always a bug.

For commands that legitimately need multi-pass behaviour (e.g., `taxonomy refresh` reads both observed disk + declared config), do the I/O once and operate on the in-memory `Note` list.

## Release / sharing

The plugin is installed via `claude plugin install https://github.com/abhijit-s/ai.git --plugin kb-curator` and cached under `~/.claude/plugins/cache/ai/kb-curator/<version>/`. When you make a backward-incompatible change to the config schema:

1. Bump `__version__` in `kb_curator/__init__.py`.
2. Note the breaking change at the top of `README.md`.
3. Update `taxonomy init` so freshly generated configs use the new shape.

## Getting help

The skill is self-contained — there is no upstream community. The expectation is:

- Read `SKILL.md` for *what* the curator does and *when* it activates.
- Read this file for *how* the code is organised and *why*.
- Read `tests/` to learn the API by example.
- When stuck, write a failing test, then make it pass.
