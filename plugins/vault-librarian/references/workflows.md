# Playbooks

Detailed step-by-step for each workflow named in `SKILL.md`. Open this file when a workflow needs more than the one-paragraph overview can give.

## A — Catalogue the vault

Goal: produce a fresh epistemological map. Read-only.

```bash
python scripts/vault_librarian.py scan --json > /tmp/kb-inventory.json
python scripts/vault_librarian.py scan                       # human summary
python scripts/vault_librarian.py audit                      # drift report
python scripts/vault_librarian.py taxonomy show              # current canonical shape
```

Report back to the user with:

1. **Pillar/area counts** — pulled from `scan`. Highlight the top 3 largest and the bottom 3 smallest areas.
2. **Drift summary** — counts of errors / warnings / infos from `audit`. Quote the top 3 errors verbatim. Don't dump every finding.
3. **Suspected gaps** — areas with central questions that look stale, areas that look like split candidates (large + heterogeneous tag distribution), pillars that look thin.
4. **Open questions** — anything you noticed that needs the user's call (a recurring topic that has no home, a category that overlaps another).

## B — Classify a single note

Goal: produce a placement + frontmatter proposal for one note.

1. `python scripts/vault_librarian.py classify <path> --json`
2. Read the note. State its **central question** in one sentence — yours, not the script's.
3. Compare your central question against the script's top suggestion and its alternates. The mechanical score is a prior; your judgment is the verdict.
4. If you change the category vs the script's suggestion, say why in one sentence.
5. Present the user with:
   - Target directory (relative to vault root)
   - Proposed frontmatter (full YAML block)
   - Inbound links impact (if the file already exists in the vault — grep for `[[<filename without extension>]]`)
6. After approval: move the file (if needed), then `python scripts/vault_librarian.py apply <new-path> --category <slug> --tags <a,b,c> --title "<H1>"`.

## C — Repair existing frontmatter

Goal: bring one or more files into conformance with the taxonomy.

For a single file:

```bash
python scripts/vault_librarian.py classify <path>           # expected shape
python scripts/vault_librarian.py apply <path> --dry-run    # show diff first
python scripts/vault_librarian.py apply <path>              # apply
```

For a batch (after running `audit`):

1. Group findings by type. Apply in this order — safe fixes first, semantic fixes last:
   - **Casing normalisation** (`category: GMP Scheduler` → `gmp-scheduler`): script does this on `apply`.
   - **Missing required fields**: re-derive `title` from H1, `category` from path.
   - **First-tag mirror rule**: script enforces on `apply`.
   - **Path↔category mismatch**: ask the user — the path or the category needs to change, and you don't know which.
2. After the batch, re-run `audit`. Expect the error count to drop and the info count (singleton tags) to be roughly stable.

## D — Evolve the taxonomy

Goal: introduce a new area (or, very rarely, a new pillar) without breaking existing recall.

Pre-flight check — answer all three before editing anything:

- What is the new area's **central question** in one sentence?
- Which existing notes (give paths) would move into it?
- Which existing area shrinks when you take those notes out, and does that shrinkage break the donor area's coherence?

If any answer is hand-wavy, stop. Either gather more notes first or place the in-flight note under an existing area as a "best-fit-for-now".

Editing flow:

1. Open `config/taxonomy.yaml`. Find the parent pillar's `areas:` list.
2. Insert the new area, preserving alphabetical order within the pillar:
   ```yaml
   - slug: <new-slug>
     path: <Directory Name>
     central_question: "<one sentence>"
   ```
3. If the area benefits from tags not yet in the controlled list, add them to the top-level `tags:` list.
4. Show the diff to the user before writing.
5. After approval, create the physical directory and seed it with a `README.md` using the minimal template in `conventions.md`.
6. Re-classify the in-flight notes against the new area (Workflow B) and move them.
7. Re-run `audit` — there should now be zero "category not in taxonomy" errors for the migrated notes.

## E — Bulk re-organisation

Goal: a sweeping cleanup, e.g., after several months of unstructured note-taking.

This is the most dangerous workflow because of `[[wiki-link]]` breakage. Default to "show, confirm, then act" at every step.

1. **Snapshot**: `python scripts/vault_librarian.py scan --json > /tmp/kb-before.json` and commit the vault to git first.
2. **Audit** with `--json`, then categorise findings into batches you can apply independently.
3. **Phase 1 — casing**: safe, no file moves. Apply in one batch.
4. **Phase 2 — frontmatter fills**: derive missing required fields. Safe.
5. **Phase 3 — category re-mapping** (no file move): if a note's frontmatter says category X but the path says Y, default to Y unless the user disagrees. No moves at this phase — only frontmatter edits.
6. **Phase 4 — physical moves**: for each move, grep the vault for `[[<filename>]]` and warn before moving. Move in small batches; re-grep between batches.
7. **Phase 5 — directory pruning**: empty directories left behind get removed.
8. **Verify**: `python scripts/vault_librarian.py scan` and `audit` — counts should match expectations from the plan.

When in doubt, prefer two small phases over one large one. Git history is your friend; rebases on an Obsidian vault are not.
