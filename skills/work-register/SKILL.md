---
name: work-register
description: Maintain AND query the daily work register and its Obsidian Kanban board(s). Use for capture — "note this in my work register", "add to the register", "sync the board", or a dictated day's plan — and equally for lookup, whenever the answer lives on the board rather than in the code: "what's on my plate", "what's on for tomorrow", "what's in progress", "am I blocked on anything", "what's still open", "where did we get to on X", "what's stale", "take stock of the register", or any question about a card, a lane, or one track's slice of work. Also for moving a card between lanes, reconciling drags made in the Obsidian UI, and standing a register up in a new vault. Covers per-day files as the source, the derived board(s) — one per scope, so the default view can exclude personal work — lane routing, the tag/icon and track vocabulary, and the read verbs that answer a lookup in a few hundred bytes rather than a 13KB board read.
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
| **Board(s)** | `<board>`, plus one per declared scope | **Derived.** Kanban view; owns live status. Scope decides which board renders a card; the board decides its column. |

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
sync_board.py --rebuild --discard-placement   # re-place everything from day files
sync_board.py --migrate      # cards whose scope now names another board; REPORTS only
sync_board.py --migrate --apply   # ...and move them, each keeping its column
sync_board.py --archive      # trim Done to a recency window; day files untouched
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
sync_board.py --list --scope personal --open          # one scope's unfinished work
sync_board.py --list --column "in progress"          # substring match; emoji optional
sync_board.py --list --json                          # the programmatic surface
sync_board.py --show 20260821-03                     # the reasoning behind one card
```

**`--list`** prints one line per card — `id · column · 🧵 track · text` — in exactly the
boards' own order (default board first, then each declared scope's; within a board the
configured `column_order`, then position within the column), so it reads as a *subset of
the boards* rather than a re-sort of them. **It is the union view** — the one surface that
spans every scope, and read-only, which is why there is no combined board. Filters compose
— `--track`, `--scope`, `--column` and `--open` narrow the same listing. `--open` drops the
done column and nothing else: **Parked is deferred, not closed.**

`--json` emits an array of `{id, date, group, column, track, scope, tags, done, text}`.
Those key names are a contract — build on them.

**`--show ID`** prints the day-file section behind one card: its `##` heading, the group's
context prose, and the item line as written, plus the card's live column and track. That is
the *reasoning*, which the card face necessarily drops. An unknown id is a failed lookup —
it reports to stderr and exits non-zero.

**Never read a whole board or a whole day file to answer a question these answer.** A board
is ~13 KB and a day file ~15 KB; one track's open cards are a few hundred bytes.

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

Reach for `--rebuild` only after the lane set changes. It **refuses without
`--discard-placement`**, because it discards every drag not yet reconciled and every
Obsidian block anchor on the board — and each anchor is a live `[[…#^id]]` link target.
Never offer it as the repair for a wrong card face; that is `--refresh`, which re-renders
the face and keeps both. On a register that renders more than one board it refuses
outright, and the flag does not unlock that. Deletions
stick via the `.sync-state.json` ledger, so a card the user deleted from a board is never
resurrected. The boards are disposable — fully reconstructible from the day files.

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

## Scope — telling personal work from work work

**One capture stream, many rendered boards.** Day files stay a single stream, because that
is how a day happens — personal and work items interleave as they occur. The **render** is
what separates them: sync partitions cards by scope and writes one board per scope, so the
default board can be left open all day with no personal work on it.

A tag cannot do this job. The Kanban plugin has a transient search box and no saved
filters, so "everything *except* personal" is not expressible — and the board file holds
every card regardless of what is typed in the box. It is a render concern, so the render is
where it is solved.

**Every card renders to exactly one board.** That is the load-bearing property, not a
detail. The board owns a card's column; a card on two boards would have two owners for that
one field, and they would diverge the instant either copy was dragged. Three things make a
second placement unreachable rather than merely unlikely:

| Property | Why it holds |
|---|---|
| scope → board is a **function** | a scope names one board; two scopes naming one file is refused at config resolution |
| it is **total** | a scope the map does not name — including the empty scope of a trackless card — falls to the default board, so nothing falls off every board |
| there is **one placement site** | sync appends each card to `board_for(...)` once, so the partition is built by construction rather than checked afterwards |

And because that is a claim about code while a board is a file you can also edit, an id
found on two boards is **reported**, never silently resolved by keeping one.

**The union is `--list`, not a board.** It already spans every board and is read-only, so it
cannot own placement. There is no combined board and there should not be one.

**Scope is a property of a track, not of a card.** Personal versus work describes a thread
of work rather than an individual item, so it is declared once per track and every card on
that track inherits it. A card with no track falls to the default scope's board.

Declared in the register root's `.work-register.toml`:

```toml
[scope]
default          = "work"       # the scope a track sits in unless it says otherwise
suppress_default = true         # cards in that scope carry NO tag
track.house-move = "personal"   # a track that no [[track_rules]] entry names
board.personal   = "PERSONAL-BOARD.md"   # relative to the register root

[[track_rules]]
pattern = "roof|gutter|damp"
track   = "roof-repair"
scope   = "personal"            # or inline, where a pattern already names the track
```

**Why `board.<scope>` sits in the corpus contract and not the per-machine config:** it is
vocabulary. It names scopes, and scopes are declared in that same file. Only `data_root` is
genuinely machine-specific — `register_dir` and `board` are vault-root-relative layout that
merely happens to live in the binding. The **default scope keeps using the register's
existing `board` key**, which is what makes a register naming no second scope render
exactly the file it always did, under the name it always had.

**Declaring a scope creates nothing until a card lands in it.** An unused scope renders no
file, so the map can be written ahead of the work.

### Reclassifying a track — `--migrate`

Changing a track's scope means its cards must move **between files**. That is a status
write: the board owns the column, and a card that teleports takes its column into a file
you were not looking at. So it never happens as a side effect — not on `sync`, not on
`--refresh`, not on `--reconcile`.

```bash
sync_board.py --migrate            # report: which cards now render to a different board
sync_board.py --migrate --apply    # …and move them, each keeping its column
```

Two deliberate acts, not one: the verb, and then `--apply`. Plain `sync` and `--status`
**notice** the drift and say so, but move nothing.

`--migrate` reports two flavours through the same seam, because they are one question asked
once — *is every card on the board the day files would put it on?*

| Flavour | Meaning | What happens |
|---|---|---|
| **wrong-board** | the track was reclassified, so the render names another file | `--apply` moves it, column intact |
| **no-source** | the day-file item behind the card is gone | reported only — with no source there is no scope to move it to |

Only the wrong-board count reaches the `--status --brief` verdict line: it is the
consequence of a config edit just made, so it is actionable now. A card with no day-file
source is older drift with no settled disposition, so it stays in the detail.

`--rebuild` **refuses outright** on a register that renders more than one board — even
with `--discard-placement`. It would re-partition every card across all of them in one
pass, and a mis-scoped track would silently relocate its whole slice into a file nobody was
watching. Use `--refresh` for a text fix and `--migrate --apply` for a reclassification.

Resolution is by track **name** either way, and that is the whole point: a card declaring
`::house-move` outright — on a track no rule matches — must land in the same scope as one
the rules infer onto that track. The `[scope] track.<name>` map wins over an inline
`scope` on a rule, so a corpus can correct a rule's scope without touching the rule.

Scope reaches the board as a `#scope/<name>` tag and nothing else. The Kanban plugin has
only a transient search box — no saved filters, no scoped views — so a tag on the card
face is the only persistent handle, and `#scope/personal` in that box is what isolates the
slice.

**The default scope is suppressed.** With every track being work, the board renders exactly
as it did before scope existed and only the exception is marked. Set
`suppress_default = false` to tag both sides instead — one value, no code change — then
`--refresh` to re-render.

Suppression is a *rendering* choice only, never a resolution one: `--list --scope work`
still matches a work card that carries no tag, and `--json` always carries the resolved
`scope`.

```bash
sync_board.py --list --scope personal --open                 # the personal slice
sync_board.py --list --scope work --track prod-setup --open  # filters compose
```

## Trimming Done — `--archive`

**Done is a recency window; the day files are the record.** A board where nearly half the
cards are Done reads as history rather than as a worklist, and it compounds twice as fast
once a register renders more than one board and each accumulates its own.

The archive is not a new store, and that is the whole design. It is the **day files** —
source, permanent, dated, append-only, and holding the reasoning `--show` prints. The board
is derived and disposable, so taking a Done card off it loses nothing.

```bash
sync_board.py --archive                        # the configured window: [archive] keep_days
sync_board.py --archive --before 2026-08-12    # an explicit date cut
sync_board.py --archive --keep 10              # keep the 10 most recent, per board
sync_board.py --archive --dry-run              # name what would go; write nothing
sync_board.py --archive --include-anchored     # also take anchored cards (see below)
```

It removes cards from the **done column only**, on **every board** the register renders,
and **never touches a day file** — not to reconcile, not to stamp an id.

`--before` and `--keep` ask different questions, so exactly one may be passed:

| Form | Question | Use when |
|---|---|---|
| `--before DATE` | *what finished before this?* | cutting at a sprint or month boundary; deterministic, so a dry run and the real run a day apart agree |
| `--keep N` | *how big should Done be?* | the complaint is the column's size, independent of how fast the work moves |
| bare | *the usual window* | the common case — `[archive] keep_days` in the corpus contract |

Recency is read from the ledger — `since` (stamped on placement and on every `--move`),
then `day`, then the card id's own date prefix. That is the **same ladder `--status` ages a
stale card with**, deliberately: one question, so one answer. Note `since` is when the card
reached its column, not when the work happened — so a done item synced in from an old day
file is *recent on the board*, and will not vanish the moment it first appears.

### Two things it deliberately refuses

**Anchored cards are skipped.** An `^abc123` block id on a card is a link target the owner
created; `[[WORK-REGISTER#^id]]` pointing into the board is not disposable even though the
board is. Anchored cards outside the window are **held back and named by id and anchor**, so
nothing is ever dropped silently. `--include-anchored` is the deliberate opt-in, and it
prints each anchor it is about to orphan.

**Id-less cards are skipped.** A hand-written done card carrying no `<!-- wr:… -->` id
cannot be recorded in the ledger, so removing it would be a deletion rather than an archive.
It stays, and is reported.

### Archived is not deleted

The ledger records every id ever placed, which is what stops a card the owner deleted from
being resurrected. An archived entry gains an **`archived` key holding the date it left** —
one key, because its presence is the flag and its value says when.

That marker is load-bearing in two directions:

- a later `sync` still **refuses to re-add** it, exactly as for a deletion;
- but the run reports the two separately — `🗑️ N deleted from the board stay deleted` and
  `📦 N archived off the board stay off` — instead of the archive inflating the deletion
  count into nonsense.

**Migration:** an entry written before `--archive` existed carries no such key, and every
card placed then was on a board. So a **missing key reads as "not archived"**. The existing
ledger keeps working untouched, is never backfilled, and needs no schema bump.

### Not the Kanban plugin's own archive

The Obsidian Kanban plugin has `archiveCompletedCards` and an `archive-with-date` setting.
**Do not use it.** `column_sequence` takes "the configured flow first, then anything else
present", so a plugin-written `## Archive` section is read as **just another column** —
re-rendered as one, its cards still counted by `--list` and `--status`, and `--reconcile`
stamping the column name back into day files. The board is a file two writers share, and
each assuming it owns the file is the same bug class as a plugin deleting our frontmatter
key.

The `%% kanban:settings %%` block has the same two authors, and the render **merges**
rather than overwrites: the contract governs the keys it declares, and any other key
already in the block is the plugin's and survives. So a setting toggled in the plugin's own
interface lasts, with no config edit. A key the contract declares stays the contract's
answer, so a plugin write over a declared key is corrected rather than adopted.

## Configuration (memory-kit philosophy)

Layers, later winning: **code defaults** ← `~/.config/work-register/config.toml`
(per-machine, out of the vault, the only place absolute paths live) ← `.work-register.toml`
at the register root (the corpus governs its own conventions — ADR-079) ← `--config`.

Every key has a code default, so the tool runs with zero settings. When the user wants a
new lane, a new tag/icon, or a different card shape, **edit the register root's
`.work-register.toml` — never the engine.** Grammar (item syntax, id format) is logic and
stays in code.

How wide the Done window is tracks how fast a register moves, so it is vocabulary too:

```toml
[archive]
keep_days = 14      # bare `--archive` keeps Done cards this recent; --before/--keep override
```

Deliberately **not** a key: whether an anchored card is protected. Not breaking a live
`[[…#^id]]` link is grammar, not house style, so it is a flag that has to be typed rather
than a setting that could be turned off once and forgotten.

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
| `--rebuild` to "tidy up" | It refuses without `--discard-placement`, and for good reason — `--refresh` if you only fixed text |
| `--rebuild` to clear old Done cards | `--archive` — it removes only what the window names, and keeps anchors |
| Let the Kanban plugin archive completed cards | Its `## Archive` section reads as another column — `--archive` |
| Delete a Done card from the board to tidy it | `--archive` — a deletion and an archive read differently in the ledger |
| Archive anchored cards to get a clean sweep | Each is a live `[[…#^id]]` link; `--include-anchored` only once you have read the list |
| Leaving a corrected day-file item stale on the board | `--refresh` |
| Read the whole board to find a few cards | `--list --track X --open` |
| Guess why a card exists from its face | `--show ID` — the day file holds the reasoning |
| Restate track status in the register | Link to `Memory/auto/*` / `RESUME.md` |
| Hardcode a vault path in the engine or skill | Read it from config |
| Hand-write the binding and the marker for a new vault | `--init PATH` |
| Copy an existing vault's 240-line contract into a new one | `--init` writes the thin one; defaults cover the rest |
| Add a tag rule in Python | Add `[[tag_rules]]` to the corpus config |
| Hand-write a second board for personal work | Give the track a `scope`, and the scope a `board` — sync renders it |
| Build a combined board spanning every scope | `--list` already spans them, and is read-only so it cannot own placement |
| Move a card between boards by editing the files | Change the track's scope, then `--migrate --apply` |
| Tag a card's scope by pattern-matching its text | Scope belongs to the track the card is on |
| Carry an undated claim from memory | Stamp "as of <date> — re-verify" |
| Write a bare `BC-5` / `U4` on first use | Attach a slug |

## Remember

- **Day files are history; the board is state.** Never blur the two — and because they are
  history, they are also the archive: Done on the board is only a recency window.
- **The corpus owns its vocabulary.** New work → new tag rule, not new code.
- **Intent, not status.** The register points; it does not duplicate.
