---
name: work-register
description: Maintain AND query the daily work register and its Obsidian Kanban board. Use for capture — "note this in my work register", "add to the register", "sync the board", or a dictated day's plan — and equally for lookup, whenever the answer lives on the board rather than in the code: "what's on my plate", "what's on for tomorrow", "what's in progress", "am I blocked on anything", "what's still open", "where did we get to on X", "what's stale", "take stock of the register", or any question about a card, a lane, or one track's slice of work. Also for moving a card between lanes, reconciling drags made in the Obsidian UI, and standing a register up in a new vault. Covers per-day files as the source, the derived board, lane routing, the tag/icon and track vocabulary, and the read verbs that answer a lookup in a few hundred bytes rather than a 13KB board read.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Work Register

The register answers one question: **what am I moving today, and what does it depend on?**
It is intent, captured per day. It is not a status database — `Memory/auto/*` and the
umbrella `RESUME.md` own live track status, and the register points at them.

## The two surfaces

| Surface | Path | Role |
|---|---|---|
| **Day file** | `<register_dir>/YYYY-MM-DD.md` | **Source.** One day's intent. Content is append-only; only the status field is ever rewritten, by `--reconcile`. |
| **Board** | `<board>` | **Derived.** Kanban view; owns live status. |

Both resolve from config — never hardcode a path. `--show-config` prints the layers.

**Why append-only + additive sync:** the board is a generated view the user also *drags
cards on*. If a rebuild recomputed placement, every drag would be clobbered — the usual
failure mode of a generated board, and what silently killed the previous kanban. So a day
file is history once written, and the sync only **adds** cards it has never seen. A card's
column is the user's to change, forever.

## Capturing a day

Write `<register_dir>/YYYY-MM-DD.md`. Keep the user's own priority order and grouping —
if they dictated `1️⃣ … 2️⃣ …`, those become the `##` headings, which become the card's
`🧭 group`.

Each item is a checkbox line whose **leading marker routes it to a column**. The marker set
is **that vault's declared vocabulary, not the shape** — the table below is the widest one
in use, and a freshly initialised vault starts with the three marked ⭑:

| Marker | Lands in | Use for |
|---|---|---|
| `📥` | Wishlist | Captured, not committed |
| `🔍` | Exploring | Investigate before committing |
| `💬` | Deciding | Needs a decision or a conversation |
| ⭑ `▶` | Next | Committed, ready to move |
| ⭑ `⏳` | In progress | Started |
| ⭑ `🔴` | Blocked | Stalled on someone or something else |
| `🅿️` | Parked | Deliberately deferred |
| `- [x]` | Done | Complete (overrides the marker) |

Whatever the set, the columns read left-to-right as a flow: capture → understand → decide
→ commit → do → done, with Blocked and Parked as the two ways work leaves the flow
unfinished.

**Read the map, do not trust this table.** `[[board.lanes]]` in the register root's
`.work-register.toml` is the authority; an unrecognised marker is not an error, it just
falls to `default_column`. `--show-config` reports how many lanes are declared.

**Rules for the body:**

- One item = one card. Wrapped lines are joined, so hard-wrap freely.
- Add a short **Context** paragraph per group, and **date every claim you carry in from
  memory** ("as of the 2026-08-20 park — re-verify"). The register is read days later;
  undated status rots silently.
- Point at the track that owns the detail: `Track → [[project_*]]`, a canon plan path, a
  PR number. Never restate a track's status in the register.
- Expand acronyms and opaque handles on first use — `PG-X18 (ban → forfeiture cascade)`,
  `BC-5 (Performance)`. A bare handle in a day file is unreadable in a month.
- Do **not** hand-write `<!-- wr:… -->` ids. The sync mints and stamps them.
- Never *hand*-edit a past day file to reflect new status — move the card on the board and
  run `--reconcile`, which rewrites only the checkbox and marker.

## Keeping the two surfaces in sync

**Every field has exactly one owner**, so there is never a conflict to resolve:

| Field | Owner | Flows |
|---|---|---|
| Existence, text, grouping | Day file | day file → board (`sync` adds new, `--refresh` updates existing) |
| Status: column + checkbox | Board | board → day file (`--reconcile`, `--move`) |

```bash
sync_board.py                # add new cards; never moves an existing one
sync_board.py --move ID=COL  # relocate a card + reconcile its day file
sync_board.py --reconcile    # stamp board status back onto the day files (text untouched)
sync_board.py --refresh      # re-render card text from the day files; KEEPS placement
sync_board.py --probe        # resolve cards' own references; PROPOSES, never moves
sync_board.py --status       # register health; --brief prints only the verdict line
sync_board.py --rebuild      # re-place everything from day files; DISCARDS drags
sync_board.py --init PATH    # stand a register up in a vault that has never had one
sync_board.py --dry-run      # show what would happen, write nothing
sync_board.py --show-config  # resolved config layers
sync_board.py --register <name> --since 2026-08-01
```

## The read surface — `--list` and `--show`

Every verb above either mutates or renders a verdict. **Neither returns cards.** So reach
for these two whenever the question is *what is on the board?* rather than *change it*.
Both are strictly read-only — no id is minted, no board written, no ledger touched.

```bash
sync_board.py --list                                  # every card, in board order
sync_board.py --list --track prod-setup --open        # one track's unfinished work
sync_board.py --list --column "in progress"          # substring match; emoji optional
sync_board.py --list --json                          # the programmatic surface
sync_board.py --show 20260821-03                     # the reasoning behind one card
```

**`--list`** prints one line per card — `id · column · 🧵 track · text` — in exactly the
board's own order (configured `column_order`, then position within the column), so it reads
as a *subset of the board* rather than a re-sort of it. Filters compose. `--open` drops the
done column and nothing else: **Parked is deferred, not closed.**

`--json` emits an array of `{id, date, group, column, track, tags, done, text}`. Those key
names are a contract — build on them.

**`--show ID`** prints the day-file section behind one card: its `##` heading, the group's
context prose, and the item line as written, plus the card's live column and track. That is
the *reasoning*, which the card face necessarily drops. An unknown id is a failed lookup —
it reports to stderr and exits non-zero.

**Never read the whole board or a whole day file to answer a question these answer.** The
board is ~13 KB and a day file ~15 KB; one track's open cards are a few hundred bytes.

`track` is what makes this worth having: it is the field that partitions the board by *who
is asking*. A card declares it with `::name` in the item text or its `##` heading, or
inherits it from a `[[track_rules]]` match.

### SessionStart already told you

The `work-register-pulse.sh` SessionStart hook resolves this conversation's memory-kit
track from its ledger and injects that track's open cards as context — so a resumed session
knows its register slice at **zero tool calls**. It caps the list, and stays silent when
there is no track (the normal case on a fresh session, which starts before any `::track`
directive) or nothing stale. If those cards are already in your context, do not re-fetch
them; use `--show` to go deeper on one, or `--list` to widen the slice.

Run the default sync right after writing a day file, and **report which column each new
card landed in** — that is the user's confirmation that routing matched their intent.
Offer `--reconcile` when they mention having moved cards or finished something.

**If you correct an item's text in a day file, run `--refresh`** — plain `sync` is additive
and will leave the card showing the old wording. `--refresh` re-renders every card face
from its day-file item while keeping the column and checkbox the board holds, so a text fix
propagates without disturbing placement.

Reach for `--rebuild` only after the lane set changes, and say plainly that drags are
lost. Deletions stick via the `.sync-state.json` ledger, so a card the user deleted from
the board is never resurrected. The board is disposable — fully reconstructible from the
day files.

**Carry-forward is the board's job.** A card still in Next next week stays on the board;
do not re-list it in a new day file. A day file records what was *added* that day.

### Moving cards without touching Obsidian

```bash
sync_board.py --move 20260821-04="in progress"      # substring match; emoji optional
sync_board.py --move 20260821-06=done --move 20260821-01=next   # repeatable
```

`--move` is the counterpart to a drag: it relocates the card, ticks or unticks the
checkbox when it crosses the Done boundary, **and reconciles the day file in the same
run** — so a move made in chat and a move made by dragging in Obsidian end up in exactly
the same state. Use this when the user says "mark X done" or "X is blocked now"; use
`--reconcile` when they have already dragged cards in the UI.

## Keeping the board honest — `--probe` and `--status`

A board only knows what a capture told it, so it drifts silently: cards sit in a lane
while the work behind them finishes elsewhere. These two verbs close that without a hook.

**`--probe`** resolves the references cards already cite — `app#733`, `surge-bot#356`,
canon plan paths — and reports what they say *now*. A merged pull request is a fact, so
this needs no session data and no hook. **It proposes and never applies**, then prints the
exact `--move` line to accept. Status is the board's field; a probe that moved cards would
be taking a field it does not own.

Binding matters, and it is the difference between a useful proposal and a wrong one:

| Binding | Meaning | Probe behaviour |
|---|---|---|
| **item-bound** | the reference is in the card's own text | terminal → **proposes** a move |
| **section-bound** | it is in the group's shared context paragraph | terminal → **advisory only** |

A card may cite a merged pull request as *background* ("found while merging #733") rather
than as the thing that closes it. Proposing Done there would be wrong, so section-bound
findings are surfaced for review instead.

**`--status`** reports register health — last capture, lane counts, cards sitting past
`stale_days`. `--brief` prints one verdict line, suitable for a SessionStart nudge:

```
⚠️ work-register [abhi]: last capture 4d ago · 10 card(s) stale >3d
```

Offer `--probe` when the user asks what's still true, takes stock, or has been away.
An unresolved reference (offline, unauthenticated, repo moved) is reported as **unknown**,
never as done.

## Configuration (memory-kit philosophy)

Layers, later winning: **code defaults** ← `~/.config/work-register/config.toml`
(per-machine, out of the vault, the only place absolute paths live) ← `.work-register.toml`
at the register root (the corpus governs its own conventions — ADR-079) ← `--config`.

Every key has a code default, so the tool runs with zero settings. When the user wants a
new lane, a new tag/icon, or a different card shape, **edit the register root's
`.work-register.toml` — never the engine.** Grammar (item syntax, id format) is logic and
stays in code.

### Standing a register up in a new vault — `--init`

A register is nothing but a `[register.<name>]` binding plus a marker at its root, so
`--init` writes those rather than making you know the shape by heart:

```bash
sync_board.py --init ~/vaults/notes                      # name slugged from the basename
sync_board.py --init ~/vaults/notes --name notes         # or say it outright
sync_board.py --init ~/vaults/notes --register-dir Journal --board Kanban/BOARD.md
sync_board.py --init ~/vaults/notes --dry-run            # print the three writes, touch nothing
```

It writes exactly three things:

| Written | Role |
|---|---|
| `PATH/.work-register.toml` | the corpus contract — discovery marker plus this vault's conventions |
| `[register.<name>]` in `~/.config/work-register/config.toml` | the per-machine path binding, **merged** into whatever is already there |
| `PATH/<register_dir>/` + a `README.md` | the day-file directory and its conventions |

And deliberately **no board**: the board is derived, so the first `sync` renders it. An
empty one scaffolded here would be a second source of truth for one run.

**Defaults are neutral on purpose.** `Register/` and `WORK-REGISTER.md` at the vault root —
no assumption about where in a vault a register belongs. A vault that wants it filed
somewhere deeper says so with `--register-dir` / `--board`, and those land in the binding.

The contract it writes is **short**, because every key already has a code default. The one
thing it must declare is the lane markers, since a marker vocabulary is a choice the engine
cannot make: with no lane declared, every unticked item falls to `default_column`. Adding a
tag rule, a track rule or a fourth lane is a commented example in the file.

**It refuses rather than overwrites.** An existing `.work-register.toml`, or a name the base
config already declares, is a hard stop with a non-zero exit — pass `--name` for a second
register at a different root. The base-config merge is textual, so every comment in that
file survives; it is backed up first, re-parsed afterwards, and rolled back if anything that
was there before moved. `default_register` is set only when it is currently unset.

`--init` finishes by resolving the register it just wrote and printing what the engine
actually sees — layers, paths, lane and rule counts. A successful init proves itself.

To add a register **by hand** instead: declare `[register.<name>]` with `data_root` in the
per-machine config, then drop a `.work-register.toml` at that root for its conventions.

## Anti-patterns

| Don't | Instead |
|---|---|
| Recompute card placement on sync | Additive only — placement is the user's |
| Hand-edit a day file to update status | Move the card on the board, then `--reconcile` |
| Re-list yesterday's unfinished item in today's file | It is already on the board |
| `--rebuild` to "tidy up" | It discards the user's drags — `--refresh` if you only fixed text |
| Leaving a corrected day-file item stale on the board | `--refresh` |
| Read the whole board to find a few cards | `--list --track X --open` |
| Guess why a card exists from its face | `--show ID` — the day file holds the reasoning |
| Restate track status in the register | Link to `Memory/auto/*` / `RESUME.md` |
| Hardcode a vault path in the engine or skill | Read it from config |
| Hand-write the binding and the marker for a new vault | `--init PATH` |
| Copy an existing vault's 240-line contract into a new one | `--init` writes the thin one; defaults cover the rest |
| Add a tag rule in Python | Add `[[tag_rules]]` to the corpus config |
| Carry an undated claim from memory | Stamp "as of <date> — re-verify" |
| Write a bare `BC-5` / `U4` on first use | Attach a slug |

## Remember

- **Day files are history; the board is state.** Never blur the two.
- **The corpus owns its vocabulary.** New work → new tag rule, not new code.
- **Intent, not status.** The register points; it does not duplicate.
