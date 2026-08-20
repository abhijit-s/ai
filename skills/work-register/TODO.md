# work-register — TODO

## T1 — Promote to a full plugin (own skills, tools, agents)

**Want:** `work-register` becomes a proper Claude Code plugin rather than a single skill
plus a script, so it can **plug into any vault** and keep the board moving on its own.

Shape:

- **Plugin package** — `plugins/work-register/` with its own `skills/`, `hooks/`, and
  agents, installed via the marketplace rather than living loose in `~/ai/skills/`.
- **Vault-agnostic** — already half-true: the engine holds no vault path, and a register
  is declared by `[register.<name>]` + a `.work-register.toml` at its root. What is
  missing is an `init` verb that scaffolds those two files for an arbitrary vault, and
  removal of the `General/Tracking/*` layout assumption from the docs (it is config
  today, but every example hardcodes it).
- **Own tools** — expose the verbs (`capture`, `sync`, `move`, `reconcile`, `rebuild`,
  `status`) as callable tools instead of a bash invocation, so an agent can move the board
  without shelling out.
- **Slash commands** — `/work-register` (capture today), `/wr-move <card> <lane>`,
  `/wr-board` (render current state in chat). The engine verbs exist already (`--move`
  lands a card in a lane and reconciles the day file; `--reconcile` absorbs drags made in
  the Obsidian UI); what is missing is the `/`-invocable surface over them.
- **Own agents** — a triage agent that reads the board plus the day files and proposes
  the next day's register; a groomer that spots cards stuck in one column for N days.

## T2 — Feed the register from live sessions (hooks)

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
