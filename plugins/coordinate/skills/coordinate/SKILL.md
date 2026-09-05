---
name: coordinate
description: Use when concurrent Claude sessions on this machine contend for a shared mutable resource and you need them to claim, yield, and preempt it deterministically instead of clobbering each other. Defines the resource-agnostic COORD messaging protocol with its own verbs (CLAIM / REQUEST / GRANT / DENY / RELEASE / STANDING-RELEASE / QUERY / ACK), lease TTLs, a priority ladder, and anti-starvation rules — applicable to any shared resource: a git working tree or branch, an exclusive dev environment or database, a deploy/apply lane, an ArgoCD app sync, a network tunnel. Backed by the `coord` CLI, a flock-serialized shared ledger that makes contention outcomes deterministic. Resource-specific skills (e.g. warp-coordinate) bind this protocol to one concrete resource; reach for those when the resource is named, and this generic skill for anything without its own.
---

# Coordinate a Shared Mutable Resource

## Overview

Multiple Claude sessions run on this machine at once and contend for **shared mutable resources** — state that a switch or write by any one session changes for all of them. Examples:

- the shared **Cloudflare WARP tunnel** (Cloudflare's zero-trust client / VPN (virtual private network) tunnel, selected by a `warpctx` profile) — see the `warp-coordinate` specialization
- a shared **git working tree** or a specific branch — concurrent commits on one dirty tree
- an **exclusive dev environment or dev database** — during a reset, migration, or backfill
- a **deploy / apply lane** — a platform terraform apply lane, a prod apply approval gate
- an **ArgoCD app sync**, or any singleton external resource

**Core principle: a shared mutable resource is a global. Claim it out loud in a machine-parseable message, confirm your claim took effect, and do not relinquish it mid-operation.**

## Two layers: the `coord` ledger and the COORD message

This skill has two complementary layers.

1. **The `coord` CLI — a deterministic shared arbiter.** `coord` is a stdlib tool backed by a single JSON ledger under `/tmp/cc-coord/`, updated under an exclusive `flock` so concurrent sessions serialize and the outcome of any contention is a *pure function of ledger state* — never a race, never a guess about wall clocks. **This is the source of truth for who holds what.** Every subcommand also prints the canonical `COORD …` line for you to broadcast.

2. **The COORD message — advisory broadcast.** The printed line is what you send to peers over `SendMessage` so a session that only reads messages (or a human watching) still sees the claim. **The message layer is ADVISORY, not enforced.** A peer that does not speak `COORD`, a human, or a non-Claude process can move the resource anyway. Therefore the last line of defense is never the protocol: **verify the actual resource state immediately before you mutate** (`coord probe`, step 5). Trust the check, not the claim.

**Cheap in the common case.** If `ListAgents` finds no peers, or none contend for this resource, skip the ceremony — just proceed. The machinery below engages only when a real contender exists. (`coord query <resource>` tells you in one call whether anyone holds it.)

## Using the `coord` CLI

`coord` is on `PATH` (installed from the `coordinate` plugin; if missing, run `python3 <plugin>/scripts/coord.py` or `make install-coord` in `~/ai`). It never blocks — every call returns immediately with an exit code you can branch on:

| Exit | Meaning |
|---|---|
| `0` | granted / done / probe reachable |
| `10` | queued — you do NOT hold; wait and re-claim |
| `11` | probe failed — do NOT mutate |

```
coord claim   <resource> [--prio P0..P3] [--hold 15m] [--id X] [--for "..."] [--next "..."] [--profile ...] [--session $CLAUDE_SESSION_ID]
coord request <resource>  ...            # same, phrased as asking a holder to yield
coord release <resource> --id X          # free a hold you own (--all releases every hold for --session)
coord grant   <resource> --re X          # yield to requester X (release + promote)
coord deny    <resource> --re X --for "frees in ~5m"   # refuse to yield yet; reserves X for the next window
coord standing-release <resource> --for "merge-only session"
coord query   <resource> [--json]        # who holds it + the queue
coord status  [--json]                   # every tracked resource
coord probe   <resource> [--cmd "..."]   # verify the resource is actually reachable (step 5)
coord sweep                              # expire lapsed holds across all resources
coord resources                          # list the resource registry (config)
```

**Always pass `--session $CLAUDE_SESSION_ID`** on `claim`/`request` so the SessionEnd hook can release your leases when you exit. Pass a stable `--id` per hold so you can renew and release it.

**Liveness token — reap a dead holder early (unclean exit).** A clean exit is handled by the SessionEnd hook; a `kill -9` / OOM / crash fires no hook, so a claim also records a *liveness token* tied to your session's lifetime. `coord` uses your session's messaging socket **automatically** — it reads `$CLAUDE_CODE_MESSAGING_SOCKET` (exported into every session, e.g. `/tmp/cc-socks/<pid>.sock`) when you pass no token flag, so **you normally do nothing**. Peers then connect-probe that socket and reap your hold the moment your session dies, long before the lease TTL. Override or supply explicitly with `--holder-token <path>`, `--socket <path>`, or `--pid <n>`; a claim with no token available is a valid **TTL-only** hold (surfaced as `reaper: ttl-only (no liveness token)` in `query`/`status`). Liveness is decided by `connect()` — not file existence, since a killed process's socket file lingers — plus a socket-inode nonce that defeats pid-reuse, and it always fails toward "alive" so a live holder is never falsely reaped.

## The COORD message protocol

Every coordination message `coord` emits is a **single line**: fixed prefix, one verb, space-separated `key=value` fields. Terse enough to parse deterministically, plain enough to read at a glance.

```
COORD <VERB> resource=<name> [key=value ...]
```

`resource=<name>` is what makes this generic — every message names the resource it concerns, so unrelated claims never collide. Values containing spaces are wrapped in double quotes (`for="db reset + backfill"`). Unknown fields are ignored, never fatal.

### Verbs

| Verb | Meaning | CLI |
|---|---|---|
| `CLAIM` | I am taking the resource now (or renewing my lease). | `coord claim` |
| `REQUEST` | Someone holds it; I am asking them to yield. | `coord request` |
| `GRANT` | I am yielding to your REQUEST (I will RELEASE). | `coord grant --re` |
| `DENY` | I am not yielding yet — `for=` says why and when I will free. | `coord deny --re` |
| `RELEASE` | I am done; the resource is free. | `coord release` |
| `STANDING-RELEASE` | I never contend for this resource for the rest of my session. | `coord standing-release` |
| `QUERY` | Who holds this resource / what is its status? | `coord query` |
| `ACK` | Acknowledged (a CLAIM, GRANT, or RELEASE). | (send over `SendMessage`) |

### Fields

| Field | On verbs | Meaning |
|---|---|---|
| `resource=<name>` | all | The shared resource this message concerns. |
| `hold=<dur>` | CLAIM, REQUEST | **Lease TTL (time-to-live)**, not a hint — e.g. `hold=15m`. The claim auto-lapses at expiry. **Default `10m` if omitted** (or the resource's registry default). |
| `prio=<P0..P3>` | CLAIM, REQUEST | Claim priority (P0 highest). Drives preemption. |
| `next=<sched>` | any | Anticipated future need, e.g. `next="~20m/5m prod apply"`, so peers plan ahead. |
| `for=<purpose>` | any | Short human purpose / reason. |
| `id=<short>` | CLAIM, REQUEST, RELEASE | Id of the hold this line establishes, renews, or closes. |
| `re=<id>` | GRANT, DENY, ACK | The id this line responds to. |

### Priority ladder

- `P0` — safety-critical mutation or active incident. **Preempts everything.**
- `P1` — routine production change / verification.
- `P2` — routine dev mutation (apply, migration, restart, sync).
- `P3` — read-only / status, lowest.

## Rules (enforced by the ledger, so you don't have to arbitrate by hand)

### Acquire handshake — a CLAIM is not "held" the instant you send it

`coord claim` tells you immediately whether you hold (exit `0`) or are queued (exit `10`) — the ledger has already arbitrated deterministically, so you never have both sessions believing they hold. When queued, the CLI prints who blocks you and why (an owed higher-priority yield, a reservation, or an equal/lower wait). Broadcast the printed line, and if a human/peer might contend outside the ledger, still allow a short grace window for a `DENY` before you rely on a hold. A late-joining session runs `coord query` first to discover the current holder.

### Lease — `hold=` is a TTL, so one dead session cannot deadlock everyone

A hold is a **lease** that expires at `hold=` (default `10m`). At expiry the claim **auto-lapses** — `coord` frees it on the next read, and `coord sweep` (and the SessionEnd hook) force it — this is the escape hatch when a holder crashes or hangs. A holder needing more time **renews before expiry** by re-running `coord claim` with the **same `--id`** and a fresh `--hold`. `coord release` early whenever you finish before the lease ends.

### Contention — who holds when two CLAIM race

Higher priority wins (P0 > P1 > P2 > P3). On a priority tie, the ledger breaks it **without trusting wall clocks** — it uses a monotonic per-ledger sequence number (the order claims actually reached the ledger under `flock`), then the lexically lower `id`. The loser is queued; its `claim` becomes an implicit `REQUEST` and it waits.

### Preemption — yield, but never mid-operation

A `REQUEST` of **strictly higher priority** than the current hold must be honored (`coord claim`/`request` reports `preempt owed`); equal or lower priority may be `DENY`ed to the end of the current lease (a `DENY` must say when it frees). A `P0` preempts any lower hold immediately. But **the holder finishes its current non-interruptible operation first** — never interrupt a terraform apply, a migration, or a db write mid-flight (corruption risk) — then `coord release`s (which promotes the waiter) and the peer re-claims. An interruptible operation (a read) yields at once.

### Anti-starvation — every waiter eventually wins

The ledger enforces both guards:
- **Every lease is bounded — even P0.** No indefinite hold exists, so a burst of high-priority work cannot lock out a low-priority waiter forever.
- **A preempted or denied waiter holds a reservation for the next free window**, ahead of any new same-or-lower-priority CLAIM — and, because a reserved waiter sorts ahead of *any* non-`P0` fresh claim, a long-suffering `P3` is not endlessly jumped by fresh `P2`s. It takes the slot the moment the current holder releases. (`P0` still preempts everything — the one exception.)

## Step flow (each maps to a verb / CLI call)

| Step | What you do | Command |
|---|---|---|
| 1. Discover peers | `ListAgents`. **None found → just proceed.** Unsure who holds? `coord query`. | `coord query` |
| 2. Classify contenders | Only peers that touch this resource contend. **None contend → just proceed.** A peer that never touches it waives once. | `coord standing-release` |
| 3. Announce the claim | Free → `coord claim` (exit 0 = you hold). Held → `coord request` and let the rules decide (exit 10 = queued). Broadcast the printed line. | `coord claim` / `coord request` |
| 4. Honor standing releases and requests | Record every STANDING-RELEASE; answer inbound REQUESTs — a strictly-higher-priority REQUEST is not optional. | `coord grant --re` / `coord deny --re` |
| 5. Verify the resource, don't trust the protocol | Before mutating, confirm the actual resource state with a real check. The protocol is advisory; the ledger cannot promise exclusivity against non-participants. | `coord probe` |
| 6. Work without relinquishing mid-operation | Never yield during a non-interruptible operation — finish it first. Renew the lease before it expires if you need longer. | `coord claim` (same `--id`, renew) |
| 7. Release and notify | `coord release` when done — it promotes the next waiter automatically. Broadcast the RELEASE line. | `coord release` / `ACK` |

### Worked example (across different resources)

```
# A dev apply — claim the lane, verify it took, do the work, release:
coord claim apply-lane --id p2 --prio P2 --hold 15m --session $CLAUDE_SESSION_ID --for "dev terraform apply"
# → exit 0, prints: COORD CLAIM resource=apply-lane id=p2 prio=P2 hold=15m for="dev terraform apply"
coord probe apply-lane        # or a resource-specific real check
# ... run the apply ...
coord release apply-lane --id p2 --for "apply done, lane free"

# A higher-priority incident preempts a P2 hold (holder finishes its atomic step first):
coord claim apply-lane --id x0 --prio P0 --hold 8m --session $CLAUDE_SESSION_ID --for "incident: revert bad apply"
# → exit 10, prints who holds + "preempt owed". Holder then:
coord release apply-lane --id p2 --for "yielding to P0"   # promotes x0

# A merge-only session waives contention for the whole session:
coord standing-release dev-db --for "canon/merge-only session, never touches the db"
```

## Remember

The ledger buys you *deterministic* arbitration — no two sessions both believing they hold, and a skew-free tiebreak. It does **not** buy you exclusivity against a human or a non-participant: that is what `coord probe` (step 5) is for. The two silent failures this exists to prevent — a peer moving the resource under you, and a dead holder deadlocking everyone — are covered by the claim-and-query handshake, the lease TTL, and the SessionEnd release hook. A real state check before every mutation covers what any cooperative protocol never can.
