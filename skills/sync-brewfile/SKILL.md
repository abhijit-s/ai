---
name: sync-brewfile
description: Sync Brewfile to match currently installed Homebrew packages. Use when packages have drifted — new installs not yet in Brewfile, or Brewfile entries that have been uninstalled.
argument-hint: Optional path to Brewfile (defaults to current working directory)
---

# Sync Brewfile to Installed Packages

Bring the Brewfile in line with what's actually installed, without overwriting it wholesale.

## Process

### 1. Read Current State in Parallel

```bash
# Resolve Brewfile path (argument or default)
cat Brewfile                           # or path from argument
brew list --formula | sort             # all installed formulae
brew list --cask | sort                # all installed casks
brew tap | sort                        # all active taps
brew leaves | sort                     # top-level formulae (not deps of others)
```

Run all five commands in parallel.

### 2. Identify Differences

**Formulae to ADD** — in `brew leaves` output but not referenced in Brewfile.

When a formula is tap-prefixed in `brew leaves` (e.g. `one2nc/cloudlens/cloudlens`), check whether its tap is also listed in the Brewfile taps section; add the tap if missing.

**Formulae to REMOVE** — listed in Brewfile but absent from `brew list --formula` output. These have been uninstalled since the Brewfile was last updated.

**Casks to ADD** — in `brew list --cask` but not in Brewfile.

**Casks to REMOVE** — listed in Brewfile but absent from `brew list --cask`.

**Taps to ADD** — active taps (from `brew tap`) not listed in the Brewfile taps section.

> Note: dependency-only formulae (present in `brew list` but absent from `brew leaves`) do **not** belong in the Brewfile unless already there — they're managed transitively.

### 3. Look Up Descriptions

For each formula/cask being added, get a one-line description:

```bash
brew info <formula>        # first line of output is the description
brew info --cask <cask>    # same
```

Use the description as an inline comment above the entry, matching the style of existing entries in the Brewfile.

### 4. Make Targeted Edits

Edit the Brewfile with minimal, surgical changes — **do not rewrite it wholesale**.

- Insert new formulae alphabetically within the existing formulae block
- Insert new casks alphabetically within the existing casks block
- Insert new taps in the taps section at the top
- Remove only the specific lines for uninstalled entries

### 5. Verify

After edits, do a final spot-check:

```bash
brew bundle check --no-lock   # confirm Brewfile matches installed state
```

If `brew bundle check` reports missing packages, investigate before declaring done — it may indicate a formula name mismatch (e.g. tap-prefixed vs bare name).

## Key Principles

- **Leaves, not all packages**: Only top-level formulae belong in the Brewfile. Dependencies are managed transitively by Homebrew.
- **Surgical edits**: Preserve comments, ordering, and section structure. Don't regenerate the file.
- **Descriptions**: Every entry should have a comment explaining what it is.
- **Tap consistency**: If a formula requires a tap, that tap must appear in the taps section.
