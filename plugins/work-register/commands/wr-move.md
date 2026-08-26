---
description: Move a work register card to another lane — reconciles the day file for you
argument-hint: <card-id> <lane>
allowed-tools: Bash
---

Relocate a card on the work register board. The board owns a card's column, so this is the
supported way to change one: `--move` writes the board **and** stamps the new status back
onto the day file, keeping the two surfaces agreed.

## Resolve the engine

`CLAUDE_PLUGIN_ROOT` is exported when this runs as an installed plugin command. The
fallback covers a checkout that has not been installed yet.

```bash
WR="${CLAUDE_PLUGIN_ROOT:-$HOME/ai/plugins/work-register}/skills/work-register/scripts/sync_board.py"
```

## Run

Card: `$1` — Lane: `$2` (full arguments: `$ARGUMENTS`)

```bash
python3 "$WR" --move "$1=$2"
```

The lane is matched by substring and the emoji is optional, so `next`, `blocked` and
`in progress` all resolve. `--move` is repeatable when several cards move together.

If either argument is missing, do not guess. Run `python3 "$WR" --list --open` and ask
which card and which lane.

## Before you move

**Status is the owner's field.** Move a card because the owner said to, not to tidy the
board. If you inferred the move from evidence — a merged pull request, a closed issue —
propose it instead:

```bash
python3 "$WR" --probe
```

`--probe` resolves each card's own references and reports what it thinks should move,
applying nothing.

Moving a card to another **board** is a scope change, not a drag — edit the track's scope
in the corpus config, then `--migrate` to report it and `--migrate --apply` to perform it.

## Report

Confirm the move in one line: the card id, where it went, and that the day file was
reconciled. If the engine refused — unknown id, ambiguous lane — relay its reason verbatim
rather than retrying with a guess.
