---
name: sync-brewfile
description: Sync Brewfile to match currently installed Homebrew packages. Use when packages have drifted — new installs not yet in Brewfile, or Brewfile entries that have been uninstalled.
argument-hint: Optional path to Brewfile (defaults to current working directory)
---

# Sync Brewfile to Installed Packages

All logic lives in `packages/homebrew/scripts/sync_brewfile.py`. Run it from the
directory containing the Brewfile (or pass the path explicitly):

```bash
python3 /Users/a.salvi/.dotfiles/packages/homebrew/scripts/sync_brewfile.py [BREWFILE_PATH]
```

Or use the shell wrapper directly:

```bash
/Users/a.salvi/.dotfiles/packages/homebrew/scripts/sync_brewfile.sh [BREWFILE_PATH]
```

## What the script does

1. Gathers installed state in parallel (`brew list --formula`, `brew list --cask`, `brew tap`, `brew leaves`)
2. Parses the Brewfile to find what's already tracked
3. Computes additions and removals:
   - **Formulae to add**: in `brew leaves` but not in Brewfile (top-level only, not transitive deps)
   - **Formulae to remove**: in Brewfile but absent from `brew list` (uninstalled)
   - **Casks to add/remove**: same logic
   - **Taps to add**: active taps not listed in the Brewfile taps section
4. Fetches one-line descriptions (`brew desc`) for all new entries in parallel
5. Applies surgical edits — inserts alphabetically, removes by name, preserves comments and structure

## After the script runs

Spot-check with:

```bash
brew bundle check --file=Brewfile --verbose
```

Pre-existing link-state warnings (`docker`, `gnupg link: false`, `libpq`) are expected and
unrelated to the sync. Investigate only if a formula name mismatch is reported.
