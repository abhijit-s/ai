# work-register

A daily work register and the Obsidian Kanban board(s) derived from it.

The register answers one question: **what am I moving today, and what does it depend on?**
Per-day Markdown files are the source; the board is a generated view you also drag cards
on. Both surfaces stay honest because every field has exactly one owner.

| Field | Owner | Flows |
|---|---|---|
| Existence, text, grouping | Day file | day file → board (`sync` adds new, `--refresh` updates existing) |
| Status: column + checkbox | Board | board → day file (`--reconcile`, `--move`) |

The sync is **additive**: it only ever adds cards it has never seen. A rebuild that
recomputed placement would clobber every drag — the usual failure mode of a generated
board — so a card's column stays the owner's to change, forever.

## What ships here

```
.claude-plugin/plugin.json          plugin manifest
commands/wr-board.md                /work-register:wr-board — render the board in chat
commands/wr-move.md                 /work-register:wr-move  — relocate a card
hooks/hooks.json                    SessionStart registration
hooks/work-register-pulse.sh        the pulse: stale-lane nag + this session's open cards
skills/work-register/SKILL.md       the skill — capture, sync, lookup, lane vocabulary
skills/work-register/scripts/       sync_board.py, the engine (stdlib only)
skills/work-register/tests/         the unittest suite
skills/work-register/TODO.md        roadmap
```

## The engine

`sync_board.py` is a plain standard-library script with **no plugin dependency**. It
resolves its register from config and holds no vault path, so it runs identically from a
terminal, from an agent, or from the SessionStart hook:

```bash
python3 ~/ai/plugins/work-register/skills/work-register/scripts/sync_board.py --list --open
```

Inside the plugin — a skill body, a command, the hook — resolve it from the plugin root
instead, which works whether the plugin is installed or run from a checkout:

```bash
WR="${CLAUDE_PLUGIN_ROOT:-$HOME/ai/plugins/work-register}/skills/work-register/scripts/sync_board.py"
```

## Standing one up in a new vault

```bash
python3 .../sync_board.py --init ~/vaults/notes --dry-run   # print the three writes
python3 .../sync_board.py --init ~/vaults/notes             # and perform them
```

`--init` writes a commented `.work-register.toml` at the vault root, merges a
`[register.<name>]` binding into the per-machine base config, and creates the day-file
directory. It writes no board — the first sync renders it. It refuses rather than
overwrites.

## The SessionStart pulse

Silent when healthy. It speaks only to report a stale lane, or to put the session's own
open cards into context when the conversation is tracked. A hook that speaks every session
is a hook you learn to ignore.

It may **propose** a card movement — `--probe` resolves each card's own pull-request,
issue and canon references — but it never applies one. Status is the owner's field, and a
board that moves itself stops being trustworthy.

## Tests

```bash
cd ~/ai/plugins/work-register/skills/work-register
python3 -m unittest discover -s tests -t tests
```

Standard-library `unittest`, no dependency to install. Hermetic: every test builds its own
register in a temp directory and never touches a live vault.
