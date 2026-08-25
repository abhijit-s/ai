---
name: work-register
description: Maintain the daily work register and its Obsidian Kanban board. Use when the user says "note this in my work register", "what's on for tomorrow", "add to the register", "sync the board", or dictates a day's plan of what to work on. Covers per-day register files, lane routing, tag/icon vocabulary, and board sync.
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

Each item is a checkbox line whose **leading marker routes it to a column**:

| Marker | Lands in | Use for |
|---|---|---|
| `📥` | Wishlist | Captured, not committed |
| `🔍` | Exploring | Investigate before committing |
| `💬` | Deciding | Needs a decision or a conversation |
| `▶` | Next | Committed, ready to move |
| `⏳` | In progress | Started |
| `🔴` | Blocked | Stalled on someone or something else |
| `🅿️` | Parked | Deliberately deferred |
| `- [x]` | Done | Complete (overrides the marker) |

The columns read left-to-right as a flow: capture → understand → decide → commit → do →
done, with Blocked and Parked as the two ways work leaves the flow unfinished.

The exact marker → column map is config, not code — read it from the register root's
`.work-register.toml` rather than trusting this table if they disagree.

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
sync_board.py --dry-run      # show what would happen, write nothing
sync_board.py --show-config  # resolved config layers
sync_board.py --register <name> --since 2026-08-01
```

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

Adding a register: declare `[register.<name>]` with `data_root` in the per-machine config,
then drop a `.work-register.toml` at that root for its conventions.

## Anti-patterns

| Don't | Instead |
|---|---|
| Recompute card placement on sync | Additive only — placement is the user's |
| Hand-edit a day file to update status | Move the card on the board, then `--reconcile` |
| Re-list yesterday's unfinished item in today's file | It is already on the board |
| `--rebuild` to "tidy up" | It discards the user's drags — `--refresh` if you only fixed text |
| Leaving a corrected day-file item stale on the board | `--refresh` |
| Restate track status in the register | Link to `Memory/auto/*` / `RESUME.md` |
| Hardcode a vault path in the engine or skill | Read it from config |
| Add a tag rule in Python | Add `[[tag_rules]]` to the corpus config |
| Carry an undated claim from memory | Stamp "as of <date> — re-verify" |
| Write a bare `BC-5` / `U4` on first use | Attach a slug |

## Remember

- **Day files are history; the board is state.** Never blur the two.
- **The corpus owns its vocabulary.** New work → new tag rule, not new code.
- **Intent, not status.** The register points; it does not duplicate.
