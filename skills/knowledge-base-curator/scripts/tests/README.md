# Tests

Pytest suite. Treat `scripts/` as the project root and run from there.

## Run

```bash
make test                    # default — fast, all tests
make test-verbose            # full tracebacks, -vv
python3 -m pytest -k slugs   # filter by name
python3 -m pytest -x         # stop on first failure
```

Expected runtime: under 1 second on a laptop.

## Layout

```
tests/
├── conftest.py              # VaultBuilder fixture — synthetic vaults per test
├── test_slugs.py            # slugify / is_slug
├── test_yaml_io.py          # YAML reader, frontmatter writer
├── test_frontmatter.py      # split, walk, write
├── test_links.py            # wiki/markdown link detection
├── test_derivation.py       # canonical_filename, classify, placement, kind
└── test_commands.py         # end-to-end via cli.main()
```

## Conventions

- **One vault per test.** The `vault` fixture builds a fresh tmp-rooted vault. No shared state between tests.
- **No git, no network.** Default `dates_source` in the test config is `none`. Tests that need dates explicitly opt in.
- **End-to-end via CLI.** `test_commands.py` invokes `cli.main(["audit", ...])` exactly as the shell would. Internal refactors that don't change behaviour don't churn these tests.
- **Tests double as documentation.** Reading any test shows the API contract.

## Adding a test

1. Pick the file matching the module under test (or add to `test_commands.py` for end-to-end).
2. Use the `vault` fixture for setup. `populated_vault` if you need a small example tree.
3. Keep tests focused: one behaviour per test method.
4. Prefer asserting on observable output (file contents, stdout JSON, exit code) over internal state.

## AI feedback loop

This suite is designed for tight iteration:

- `make test` returns a non-zero exit on any failure — perfect for CI hooks or agent loops.
- Failures point at the specific behaviour broken, not at line numbers in a single 1000-line file.
- The synthetic-vault style means a misunderstanding of the vault model surfaces as a test failure rather than weird production behaviour.

When an AI agent changes `kb_curator/` code, the expected workflow is:

1. Run `make test`.
2. If a test fails, read its name + the assertion to understand the contract.
3. Fix the code (not the test) unless the test itself was wrong.
4. Re-run until green.
5. Run `make sanity` for an extra confidence check against the real vault.

## Adding a new behaviour

When adding a feature:

1. **Write the test first** in the right module — it codifies the contract.
2. Implement until `make test` passes.
3. Add a 1-line note to `DEVELOPMENT.md` if the new feature changes how a maintainer reasons about the code.
