---
description: Render the work register board in chat — the whole board, or one track's / scope's open slice
argument-hint: "[--open] [--track <name>] [--scope <name>] [--column <name>] [--show <id>]"
allowed-tools: Bash
---

Show the work register's current state **in chat**. Read it from the engine — never by
opening `WORK-REGISTER.md` or a day file, which cost 13KB and 15KB respectively to answer
what these verbs answer in a few hundred bytes.

## Resolve the engine

`CLAUDE_PLUGIN_ROOT` is exported when this runs as an installed plugin command. The
fallback covers a checkout that has not been installed yet.

```bash
WR="${CLAUDE_PLUGIN_ROOT:-$HOME/ai/plugins/work-register}/skills/work-register/scripts/sync_board.py"
```

## Run

Arguments given: `$ARGUMENTS`

Pass them through to `--list` verbatim — they are already the engine's own flags:

```bash
python3 "$WR" --list $ARGUMENTS
```

With **no arguments**, show the standing worklist rather than the whole archive, since the
closed cards are rarely what was asked for:

```bash
python3 "$WR" --list --open
```

If the arguments name a single card (`--show <id>`, or a bare id like `20260821-04`), run
`--show` instead — that surfaces the day-file reasoning behind the card, not just its line.

## Report

Relay the engine's output as-is. It is already formatted for reading: one line per card,
grouped by lane, with the id, column and track. Do not re-summarise it into prose and do
not reorder it — lane order is the board's own order and carries meaning.

Add at most one line of your own if something in the output is worth flagging: a stale
lane, a blocked card, or a track with nothing open.

Do **not** move any card. This command reads. `/work-register:wr-move` writes.
