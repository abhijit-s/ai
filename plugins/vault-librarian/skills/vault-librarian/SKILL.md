---
name: vault-librarian
description: Catalogue, classify, and curate a Markdown knowledge vault (Obsidian / mkdocs / any directory tree of .md files with YAML frontmatter). Use when auditing taxonomy, classifying new notes, repairing frontmatter, renaming files safely, checking broken wiki/Markdown links, detecting themes, injecting emoji sigils, evolving categories, or bootstrapping a taxonomy from an existing vault. Covers epistemology mapping, taxonomy drift, frontmatter schema, controlled tag vocabulary, README indexes, and link integrity.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
---

Curate a Markdown knowledge vault as a living taxonomy. The vault is structured as a hierarchy of **Pillars → Areas → Sub-areas → Topics → Notes**, with every note carrying YAML frontmatter (`title`, `category`, `tags`, plus derived fields). This skill keeps the taxonomy coherent, classifies new material into it, and proposes principled extensions when reality outgrows the current shape.

The skill works on **any vault that matches the configured shape** — Obsidian, mkdocs-style, or a plain directory tree. Conventions live in `config/taxonomy.yaml` (path regexes, link syntax, slug case, date source). To bootstrap from an existing vault, run `python scripts/vault_librarian.py taxonomy init --root <vault> --out config/taxonomy.yaml`.

The deterministic mechanics (walking the tree, parsing frontmatter, writing back YAML) live in `scripts/vault_librarian.py`. The judgment (where does this note belong? does this warrant a new area? which tags actually carry signal?) is **your** job, informed by the canonical taxonomy in `config/taxonomy.yaml`.

## When to activate

- "Audit the KnowledgeBase / vault"
- "Classify this note / where should this go?"
- "Add frontmatter to this file"
- "What's our taxonomy?" / "List categories"
- "Re-organise / refactor the vault"
- "Propose a new area for X"
- Any file path inside the configured vault root with missing/stale frontmatter

If the user just wants prose-style polish on a doc (not classification), defer to `/writing-documentation` instead.

## Core philosophy

**The taxonomy serves recall, not symmetry.** A category exists because notes in it answer the same kind of question. If two areas always co-appear in searches, they want to merge. If one area has 40 notes spanning three unrelated subtopics, it wants to split. Resist the urge to balance the tree for its own sake.

**Path is the strong signal; frontmatter is the index.** Filesystem location encodes the primary classification. Frontmatter (`category`, `tags`) makes that classification machine-queryable and lets a single note carry secondary affinities (e.g., a Postgres note tagged `performance`). When path and `category` disagree, the path almost always wins — the file was placed deliberately, the frontmatter rots.

**Evolve the taxonomy reluctantly, but evolve it.** Don't bend a new note into a poor-fit category just to avoid editing `taxonomy.yaml`. But don't spin up a new area for two notes either — the threshold for a new sub-area is roughly five notes with a clear shared question.

## The vault model

```
Pillar (01-…)         e.g. "01-Languages & Runtimes"        slug: languages-runtimes
└── Area              e.g. "Golang"                          slug: golang
    └── Sub-area      e.g. "02-Concurrency"   (optional)     slug: golang-concurrency
        └── Topic     e.g. "Channels"         (optional)     groups related notes
            └── Note  "Buffered vs Unbuffered channels.md"   carries frontmatter
```

- Pillars are the seven numbered top-level directories. **Do not add pillars without explicit user approval** — they're the most stable shape in the vault.
- Areas under a pillar are stable mid-term and map 1:1 to a `category` value in frontmatter.
- Numbered sub-areas (`01-`, `02-`, …) imply a learning order; preserve that ordering when classifying.
- Every directory should contain a `README.md` acting as its index (linked `[[wiki-links]]` to notes inside).
- `zAttachments/` and `.obsidian/` are not part of the taxonomy. Ignore them.

## Frontmatter contract

Every note must carry:

```yaml
---
title: <Human title, matches the H1>
category: <kebab-case area slug from taxonomy.yaml>
tags:
  - <kebab-case-tag>
  - ...
---
```

Rules:

- `category` is **always** kebab-case, **always** a value defined in `taxonomy.yaml` under `categories:`. If you can't find a match, that's a signal to propose a taxonomy extension — not to invent a new value silently.
- `tags` are kebab-case, lowercase. The first tag conventionally repeats the category for grep-ability. Add 1–4 more tags expressing cross-cutting concerns (`performance`, `deep-dive`, `crash-course`, `index`, `security`, …).
- `title` matches the H1 in the body. If they drift, the H1 wins (humans see it; frontmatter is metadata).
- READMEs additionally carry the `index` tag.

Tag discipline: tags that appear only once are noise. The CLI's `audit` command flags singleton tags so you can either promote them (use elsewhere) or drop them.

## Workflows

### Workflow A — Catalogue the vault (epistemology pass)

Use when the user asks to "understand the KnowledgeBase" or you need a fresh map before doing anything else.

1. Run `python scripts/vault_librarian.py scan --json > /tmp/kb-inventory.json` to produce the inventory.
2. Run `python scripts/vault_librarian.py audit` to surface drift (path↔category mismatches, missing frontmatter, missing READMEs, singleton tags, undefined categories).
3. Read `config/taxonomy.yaml` and compare against the scan summary. Report to the user:
   - Pillar/area shape (counts of notes per area)
   - Drift summary (top issues, not every issue)
   - Suspected gaps (areas with very few notes, areas that look like they want to split)

Do not propose changes yet. The catalogue pass is informational.

### Workflow B — Classify a new (or unclassified) note

Use when a `.md` file exists outside the vault, lives in the wrong place, or lacks frontmatter.

1. Read the note. Identify its **central question** in one sentence ("What is this note actually answering?").
2. Match the central question against `taxonomy.yaml` categories. Prefer:
   - An exact existing area
   - The pillar that owns the closest existing area
3. Run `python scripts/vault_librarian.py classify <path>` for a mechanical suggestion (filename/content keyword match against the taxonomy). Treat its output as a **prior**, not a verdict.
4. Decide:
   - **Fits cleanly** → propose target path + frontmatter to the user, then apply with `apply`.
   - **Fits with adjustment** → suggest the closest area and explain the trade-off.
   - **Doesn't fit anywhere** → go to Workflow D (propose extension).
5. Never move files or write frontmatter without showing the user the proposed change first, unless they've explicitly said "just do it".

### Workflow C — Repair existing frontmatter

Use when `audit` flags a file or the user points at one.

1. `python scripts/vault_librarian.py classify <path>` to compute the **expected** category/tags from path + content.
2. Diff against the file's current frontmatter.
3. For each discrepancy:
   - **Casing only** (`category: GMP Scheduler` → `gmp-scheduler`): fix without asking.
   - **Category value changed**: ask before changing, since it implies a re-classification.
   - **Missing tags**: add the canonical pillar/area tag without asking; ask before adding cross-cutting ones.
4. Write changes with `python scripts/vault_librarian.py apply <path> --category <c> --tags <t1,t2,…>` or via the `Edit` tool for surgical edits.

### Workflow D — Evolve the taxonomy

Use when classification surfaces a note (or cluster) that doesn't fit, **and** the cluster is non-trivial (≥3 notes in flight, or a recurring theme).

1. Articulate the new area's **central question** in one sentence. If you can't, the area isn't real.
2. Pick the parent pillar. New areas extend pillars; they don't create them.
3. Propose a `category` slug (kebab-case, singular noun phrase).
4. Show the user a diff of the proposed `taxonomy.yaml` change before applying.
5. After approval, update `config/taxonomy.yaml`, create the directory + `README.md`, then re-classify the in-flight notes against the new area.

### Workflow E — Bulk re-classification

Use when the user wants to "clean up" or "re-organise". Treat with caution — bulk moves break inbound `[[wiki-links]]`.

1. Run a full `audit`. Group findings by type.
2. Propose a phased plan: casing fixes first (safe), then missing-FM fills, then re-categorisations, then physical moves.
3. For physical moves, also grep for inbound `[[wiki-links]]` and warn about breakage. Don't move silently.

### Workflow F — Repair broken wiki-links

Use when the user asks to "check links" / "find broken links", or after a rename pass.

1. `python scripts/vault_librarian.py links check` — lists every unresolved `[[link]]`, grouped by source file, with closest-match suggestions (Levenshtein on note stems).
2. Attachments (`.png`, `.pdf`, …) and absolute-path-style links (`Folder/Subfolder/Note`) are resolved correctly and not flagged.
3. `python scripts/vault_librarian.py links repair --dry-run` — show the link rewrites the tool would apply when the suggestion is unambiguous (exactly one close match).
4. `python scripts/vault_librarian.py links repair` to apply. Add `--aggressive` to repair when there are multiple close matches; only use after eyeballing the candidates.

Real broken-link sources to watch for: **smart quotes vs straight quotes** (`'P'` vs `'P'`), **em-dash vs hyphen** drift, and **typos in the link target**. Templates and placeholder text like `[[Note Name]]` will always show as broken; that's expected.

### Workflow G — Canonical filenames + safe rename

Use when the user wants files renamed to match their titles, or when audit reveals filename↔H1 drift.

1. `python scripts/vault_librarian.py naming check` — lists files whose `frontmatter.title` (or H1) differs from the canonical filename. The rules come from `naming:` in `taxonomy.yaml` (underscore→space, strip leading emoji, max length, illegal chars).
2. READMEs (`README.md`) and Excalidraw files (`*.excalidraw.md`) are intentionally skipped — they have filesystem conventions that override title-based naming.
3. To rename one file with **automatic inbound link repair**:
   ```
   python scripts/vault_librarian.py naming rename --path "<file>" --dry-run   # preview
   python scripts/vault_librarian.py naming rename --path "<file>"             # apply
   ```
   The script rewrites every `[[old-stem]]` in the vault to `[[new-stem]]` before renaming the file, so inbound links survive.
4. For batch renames, drive `naming rename` in a loop with explicit user sign-off per file or per group.

### Workflow H — Inject emoji sigils

Use when the user wants visual category cues in titles.

1. `python scripts/vault_librarian.py emojis apply --dry-run` — preview the H1 edits.
2. `python scripts/vault_librarian.py emojis apply` — write. The script reads `emojis:` in `taxonomy.yaml` (category → emoji) and prefixes the H1 of every note in that category.
3. **Idempotent**: re-runs do not double-prefix if any configured emoji already leads the H1.
4. To skip a category, remove or comment out its line in `taxonomy.yaml > emojis:`.

This is a low-stakes cosmetic pass but a high-visibility one. Always dry-run first on a vault used by humans.

### Workflow I — Content-based tag inference

Use when a note is missing useful cross-cutting tags (`performance`, `security`, `observability`, …) and you don't want to read 300 lines of body to figure out which apply.

1. `python scripts/vault_librarian.py tags suggest <path>` — runs the `inference_rules:` list from `taxonomy.yaml` against the file's title+body. Each rule's `keywords` (case-insensitive substring match) gates whether its `tag` is suggested.
2. `python scripts/vault_librarian.py tags suggest <path> --apply` — append the suggested tags to the file's frontmatter (idempotent: tags already present are not duplicated).
3. To extend inference: add a new entry to `inference_rules:`. Rules should be narrow — a broad keyword (`"the"`, `"data"`) will flood every note.

### Workflow J — Theme detection (vocabulary evolution)

Use when the user asks "what themes run through the vault?" or before proposing new controlled tags.

1. `python scripts/vault_librarian.py themes detect` — groups tags by co-occurrence into clusters. Default threshold: pairs must share ≥4 notes. Universal connector tags (`index`, `reference`) and category slugs are excluded so cross-cutting themes surface.
2. Read the clusters as hypotheses, not verdicts. A real theme has a one-sentence name; if you can't name the cluster, it's just shared vocabulary.
3. When a cluster looks like a stable theme, promote its anchor tag to `tags:` in `taxonomy.yaml` (controlled vocabulary), or add an `inference_rules:` entry that auto-tags new notes into it.

## Using the CLI

The Python entrypoint is `scripts/vault_librarian.py`. It is the single source of mechanics — do not re-implement frontmatter parsing or directory walking in ad-hoc shell.

```
python scripts/vault_librarian.py scan [--json] [--root <vault>]
python scripts/vault_librarian.py audit [--json]
python scripts/vault_librarian.py classify <path> [--json]
python scripts/vault_librarian.py apply <path> [--category <slug>] [--tags <a,b,c>] [--title <t>] [--dry-run]
python scripts/vault_librarian.py taxonomy show
python scripts/vault_librarian.py taxonomy refresh [--apply]
python scripts/vault_librarian.py links check [--json]
python scripts/vault_librarian.py links repair [--dry-run] [--aggressive]
python scripts/vault_librarian.py naming check [--json]
python scripts/vault_librarian.py naming rename --path <file> [--dry-run]   # rewrites inbound links
python scripts/vault_librarian.py emojis apply [--dry-run]
python scripts/vault_librarian.py tags suggest <path> [--apply]
python scripts/vault_librarian.py themes detect [--min-cooccurrence N]
```

All commands honour `--config <path>` (defaults to `config/taxonomy.yaml` in the skill directory). The vault root comes from `vault.root` in the config; override with `--root`.

The script has zero non-stdlib dependencies — it ships its own minimal YAML parser/serialiser for the frontmatter subset (block-style mappings, lists of strings). This keeps the skill invocable from any Python 3.10+ environment without `pip install`.

## Anti-patterns

- **Inventing a `category` value not in the config.** If it's not in `taxonomy.yaml`, either map to the closest existing slug or run Workflow D first.
- **Title Case in `category` or `tags`.** Always kebab-case. The CLI's `apply` will normalise, but don't propose Title Case in the first place.
- **Auto-moving files based on `classify` output.** `classify` is a suggestion engine; moves need explicit user sign-off because of `[[wiki-link]]` breakage.
- **Splitting an area because it "feels big".** Splits need a real central-question distinction, not a size argument.
- **Touching `zAttachments/` or `.obsidian/`.** These aren't notes. The CLI excludes them; you should too.
- **Adding singleton tags to a single note.** A tag with one user has no recall value. Pick an existing tag or skip.
- **Skipping the `audit` step before bulk changes.** You'll miss drift and create more.
- **Renaming files without the CLI's `naming rename` command.** A bare `mv` breaks every `[[wiki-link]]` pointing at the file. `naming rename` rewrites inbound links first, then renames.
- **Running `emojis apply` on someone else's vault without dry-run.** It edits every H1 in every matching category. Always preview.
- **Treating `themes detect` output as authoritative.** Clusters are hypotheses — only promote a cluster to controlled vocabulary or an inference rule after you can name the theme in one sentence.
- **Adding an `inference_rules:` keyword broad enough to match every note.** Narrow keywords beat broad ones. If a rule fires on 80% of files, its tag carries no signal.

## Coordinating with other skills

- `/writing-documentation` — for prose style, README structure, code-block conventions. Use it when polishing a note's body, not when classifying.
- `/writing-claude-skills` — only when extending *this* skill itself, not when curating the vault.

## Remember

- Path is truth; frontmatter is the index of truth.
- The taxonomy serves recall — measure splits and merges by how much easier they make finding things, not by symmetry.
- Mechanics in Python, judgment in the skill. Never re-implement parsing inline.
- A new pillar is a once-a-year decision. A new area is a once-a-quarter decision. New tags happen weekly. Scale your caution accordingly.
- The skill is opinionated *and* flexible: opinions live in `taxonomy.yaml` (categories, controlled tags, naming rules, emojis, inference rules), the CLI executes them, and the agent stays the arbiter of taste. To shift the opinion, edit the config — don't fight the tools.
- Themes are discovered, not invented. Run `themes detect` periodically to see what the vault is actually about, then promote real themes into controlled vocabulary or inference rules.
