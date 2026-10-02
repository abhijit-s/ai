# DEFECT: the reaper lapses a *live* lease while its holder is blocked on a human prompt

**Filed:** 2026-09-07
**Component:** `coord` WARP lease reaper (`scripts/coord.py` — liveness probe + TTL backstop)
**Severity:** High for long human-gated operations (prod applies). One occurrence corrupted a prod apply.
**Reporters:** two Claude sessions coordinating the shared WARP tunnel — `prod-setup` (held Prod through a Datadog bring-up) and `creator-onboarding-improv` (Dev). The latter has exact claim/release ids + timestamps and has offered to corroborate.

## Symptom

During one session the reaper lapsed a **live** WARP lease **four times** (ids `k190906`, `k512425`, `k295109`-predecessor, and one more), every time while the holder was mid-task but **blocked waiting on a human `!` command** (a `terragrunt apply` yes-prompt, an owner-run mutation). Each time the lease vanished: a later `coord release` returned `no-op: you did not hold or queue`.

Once, this was not merely annoying — it was **corrupting**. A lease lapsed while a prod `terragrunt apply` was staged; the coord slot went free; a second session, reading `coord status` as free, legitimately claimed the tunnel on the Dev profile and switched it; the in-flight prod apply then failed with `Kubernetes cluster unreachable: dial tcp 10.140.x:443: network is unreachable` (the prod-VPC route was gone). The `helm uninstall` immediately prior had succeeded, so the sequence half-completed. Shared-tunnel flap → split terraform run.

## Why this contradicts the design

The reaper's own contract (`coord.py` ~line 134–153) is explicitly **fail-safe**:

> only ever reap on a POSITIVE dead signal … every ambiguity fails toward [the TTL backstop] … connect() remains the authority.

- `connect()` succeeds → a listener answered → **ALIVE**.
- `connect()` ECONNREFUSED (twice, to filter a momentarily-full accept backlog) → inode present, no listener → **DEAD**.
- An inode nonce (`enrich_token`) guards against pid reuse.

A live holder should therefore **never** be reaped early — only the TTL backstop should ever fire on it. Four early lapses of live leases means one of two things, both defects:

1. **False-DEAD from the liveness probe (the serious one).** A Claude CLI session blocked at a human prompt may not be accepting connections on its messaging socket (`/tmp/cc-socks/<pid>.sock`) at probe time — so `connect()` refuses, the probe reads DEAD, and the reaper reaps a session that is very much alive, just waiting on its human. This **violates the stated invariant** that ambiguity fails safe: "blocked on human input" is being classified as "dead," not as "unknown → defer to TTL."
2. **TTL too short for human-gated work.** If instead the TTL backstop fired, then a 45–90m hold is simply not long enough to span a prod apply that includes human review/confirmation time, and there is no way for the holder to keep it alive across the stall without polling (which a session blocked on a human cannot do).

Logs (which `creator-onboarding-improv` holds) would say which mechanism fired in each case; the fix set below covers both.

## Root cause (the sharp one)

Beneath the probe question is a design gap that makes the symptom *unrecoverable by consumers*: **after the fact, a lapsed (reaped) lease and an explicitly released lease are indistinguishable.** The ledger records only that the slot is free.

That indistinguishability is what forces both sessions off the authoritative signal (the lease) and onto a physical-state heuristic — `warpctx current`: "if the tunnel is already on Production, treat it as contended." And that heuristic then **misfires on clean handoffs**: a session that explicitly releases and leaves the tunnel on Production looks identical to one that was reaped mid-apply, so the peer's claim-only-when-safe loop stalls on a benign free slot.

So the causal chain is: reaper lapses a live lease → free slot is ambiguous (reaped vs released) → consumers guess from the tunnel profile → the guess misfires both ways (grabs during a live apply; stalls on a clean handoff).

## Proposed fixes (ranked; smallest first)

1. **Record *why* a lease ended.** Have `release` / the reaper stamp a terminal reason on the freed slot (`ended: explicit | reaped-liveness | reaped-ttl`, with a timestamp). This is the smallest change and it **removes the guessing entirely**: a consumer sees "freed explicitly 3s ago" (go) vs "reaped-ttl while for=prod apply" (ask first / probe) without any physical-state heuristic. Directly addresses the root cause.
2. **Do not classify "blocked on human input" as DEAD.** Treat a `connect()` refusal on a holder whose `for=` describes a human-gated op (or within a grace window since last renew) as **unknown**, not dead — defer to the TTL backstop, per the reaper's own stated fail-safe direction. Optionally add a liveness signal that survives a human stall (e.g. a heartbeat file the session's runtime touches independent of its prompt loop).
3. **Right-size / auto-extend TTL for human-gated holds.** A `--hold` that covers a prod apply must budget for human confirmation latency; consider an auto-extend-on-activity so an active-but-waiting holder isn't lapsed.

## Consumer-side mitigation (already adopted this session, until the tool is fixed)

The correct gate is narrower than "tunnel-on-Prod means contended":

- free lease **+** Prod profile **+** an explicit "I'm done" handoff from the prior holder → **go**.
- free lease **+** Prod profile **+** silence → **ask first / probe** (could be a reaped live holder).

An explicit release is direct evidence and supersedes the circumstantial tunnel profile. But this is a workaround for fix #1, not a substitute: recording the terminal reason removes the need for it.

## Impact if unfixed

Whoever holds Prod **longest through a human-gated apply** is bitten every time — precisely the highest-stakes operations. A tunnel flap mid-apply can split a terraform run and leave prod half-reconciled. Neither consuming session can fix this from its side; it is a `coord` defect.
