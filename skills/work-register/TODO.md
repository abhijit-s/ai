# work-register — TODO

## T1 — Promote to a full plugin (own skills, tools, agents)

**Want:** `work-register` becomes a proper Claude Code plugin rather than a single skill
plus a script, so it can **plug into any vault** and keep the board moving on its own.

Shape:

- **Plugin package** — `plugins/work-register/` with its own `skills/`, `hooks/`, and
  agents, installed via the marketplace rather than living loose in `~/ai/skills/`.
- **Vault-agnostic** — **done for the standing-up problem.** `--init PATH` scaffolds a
  register in any vault: a thin, commented `.work-register.toml` at the root, a
  `[register.<name>]` binding merged into the per-machine base config, and the day-file
  directory with a README. Layout defaults are neutral (`Register/` and
  `WORK-REGISTER.md` at the vault root), overridable with `--register-dir` / `--board`.
  It writes no board — the first sync renders it. It refuses rather than overwrites, and
  the base-config merge is textual (comments survive), backed up, re-parsed and rolled
  back on loss. It finishes by resolving the register it wrote, so a success proves
  itself. `--dry-run` prints all three writes and touches nothing.

  The docs never actually hardcoded `General/Tracking/*` — that part of this item was
  stale. What did leak was the *lane vocabulary*: SKILL.md presented one vault's
  eight-marker flow as the shape. It now marks the three a fresh register starts with and
  points at `[[board.lanes]]` as the authority.

  **Residual, small:** the existing `abhi` contract declares `[register.abhi]` with an
  absolute `data_root` inside the vault file. It works (the base config declares the same
  values), but it puts a per-machine path in a vault-committed file, which is exactly what
  a clone of that vault onto another machine would get wrong. `--init` deliberately does
  not write that table; the old contract could drop it.
- **Own tools** — **DEFERRED, and the reasoning matters more than the feature.** The want
  was to expose the verbs (`capture`, `sync`, `move`, `reconcile`, `rebuild`, `status`) as
  callable tools — a Model Context Protocol (MCP) server inside the plugin — so an agent
  could drive the board without shelling out.

  Every reason this item existed has since been met more cheaply, by things built after it
  was written:

  | The want | What answers it now |
  | --- | --- |
  | Discoverable without reading docs | the skill, plus the SessionStart hook that pushes the verbs into context |
  | Typed, validated arguments | the engine validates; `resolve_column` matches a column by substring |
  | A structured return the model need not parse out of stdout | `--list --json` |
  | Frictionless to invoke | `python3` is already allowlisted; a new server would need its own permission plumbing |

  Against that, an MCP server's schemas occupy context in every session whether or not the
  board is touched, and the mitigation — deferring them — costs a discovery round-trip
  before the first call. A running cost, to replace a bash invocation that has neither.

  **Revisit when, and only when, something without a shell has to drive the register** —
  another product, a browser agent, a job calling over a protocol. Until then this is
  complexity mistaken for sophistication, which is the failure this file exists to prevent.
  Recorded rather than deleted so the next reader inherits the argument instead of
  re-proposing the feature.
- **Slash commands** — `/work-register` (capture today), `/wr-move <card> <lane>`,
  `/wr-board` (render current state in chat). The engine verbs exist already (`--move`
  lands a card in a lane and reconciles the day file; `--reconcile` absorbs drags made in
  the Obsidian UI); what is missing is the `/`-invocable surface over them.
- **Own agents** — a triage agent that reads the board plus the day files and proposes
  the next day's register; a groomer that spots cards stuck in one column for N days.

## T2 — Feed the register from what actually happened

> **Corrected 2026-08-25, and partly built.** This item originally prescribed a
> `SessionEnd` hook. Measured against August's ledgers, **SessionEnd fails to fire ~44% of
> the time** here (56 crash-recovered stubs vs 70 clean compiles) — what makes memory-kit
> reliable is the nightly sweep repairing from checkpoints, not the event. So: read the
> *store*, never trust the event.
>
> **Built:** `--probe` (resolve the references cards already cite — deterministic, no
> hook, no session data) and `--status` (staleness verdict). See SKILL.md.
>
> **Still open:** ledger replay, below — the half that catches work leaving no pull
> request, which is how three canon research consolidations went unrecorded in one week.

**Want:** the board reflects the current state of affairs **without being hand-driven** —
today it only knows what a day file says, so it goes stale the moment work happens outside
a capture.

Candidate wiring:

- **`SessionEnd` / `SessionStart` hook** — harvest what the session actually did (files
  touched, PRs opened, tests run) and propose card movements: "PR #768 merged → move
  `20260821-02` to 🏁 Done?"
- **Proposal, not auto-apply.** Status is the owner's field (see the field-ownership
  contract in `scripts/sync_board.py`). A hook must write a *proposal* the owner accepts,
  never silently drag a card — otherwise the board stops being trustworthy, which is the
  one failure this design exists to prevent.
- Reuse memory-kit's checkpoint/episodic exhaust rather than re-deriving session activity.

## T3 — Integrate with memory-kit / native Claude memory

**Want:** the register stops duplicating what memory already knows.

- **Read** — a card's `Track → [[project_*]]` pointer should resolve through memory-kit's
  five-verb contract, so `status` can print each card's live track state next to it
  instead of the owner re-reading `Memory/auto/*`.
- **Write** — a card reaching 🏁 Done is a candidate durable fact; offer to route it to
  `memory.write` as a proposal (human-gated, matching memory-kit's existing model).
- **Native memory** — the per-track namespacing work
  (`project_native_memory_track_namespacing`) is the natural join: a register card and a
  memory track are the same subject viewed from different angles.

## Open questions

- **OQ1** — does a card's status become a memory fact, or stay board-local? Leaning
  board-local, with only completions promoted.
- **OQ2** — one register per vault, or one register spanning vaults with a `corpus` field
  per card? memory-kit chose per-corpus with a named-selection argument; following that
  precedent is probably right.
- **OQ3** — carry-forward: should a card sitting in ▶️ Next for a week auto-appear in
  today's day file, or is the board alone the standing worklist? Currently the latter.

## Constraints to preserve

Whatever this grows into, these are the invariants that keep it from rotting:

1. **Per-field ownership** — day files own existence/text/grouping; the board owns status.
   Never give a field two owners.
2. **Additive sync** — never recompute placement. A drag is the owner's decision.
3. **Deletions stick** — the ledger exists so a deleted card is not resurrected.
4. **Grammar in code, vocabulary in config** — new lanes/tags/icons must never need a
   code change.
