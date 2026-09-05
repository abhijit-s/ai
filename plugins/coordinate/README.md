# coordinate

Deterministic coordination for concurrent Claude Code sessions contending over a **shared mutable resource** — a git working tree, an exclusive dev database, a deploy/apply lane, an ArgoCD sync, or the Cloudflare WARP tunnel. A switch or write by any one session changes that resource for all of them; this plugin turns the cooperative COORD protocol into a deterministic, file-backed arbiter.

## What's in the box

| Component | Path | Role |
|---|---|---|
| `coordinate` skill | `skills/coordinate/SKILL.md` | The resource-agnostic COORD protocol — verbs, fields, priority ladder, lease/preemption/anti-starvation rules, seven-step flow. |
| `warp-coordinate` skill | `skills/warp-coordinate/SKILL.md` | The Cloudflare WARP specialization (profiles, reachability proof, never-flap, account-assert). |
| `coord` CLI | `scripts/coord.py` | The engine: a flock-serialized JSON ledger implementing the COORD state machine. Stdlib only, no dependencies. |
| Resource registry | `config/resources.toml` | Config-not-fork: per-resource metadata + reachability probe commands. `warp` is one row. |
| SessionEnd hook | `hooks/coord-release.sh` | Releases a session's leases on exit, closing the exited-holder gap deterministically. |
| Tests | `scripts/tests/test_coord.py` | 31 tests covering the six robustness properties. `make test-coord`. |

## Why a ledger, not just messages

Pure message-passing (the original COORD design) is deterministic in a human's head but not in code: two sessions can each *believe* they hold. `coord` extracts the protocol's structural semantics into a **single JSON ledger** (`/tmp/cc-coord/ledger.json`) updated under an exclusive `fcntl.flock`. Concurrent claims serialize; the outcome of any contention is a **pure function of ledger state** — never a race, never a wall-clock comparison across skewed machines. Contention ties break on a monotonic per-ledger sequence number (the order claims reached the ledger), then lexical id.

It stays faithful to the protocol's **advisory** nature: the ledger coordinates cooperating Claude sessions; it is not a lock server that can stop a human or a non-participant. The real last line of defense is a live resource probe before mutating — `coord probe`, step 5 — which is why `warp` carries an in-cluster probe command.

## Install

```sh
make install-coord      # symlink `coord` into ~/.local/bin (must be on PATH)
make test-coord         # run the test suite
```

Then, in a session, invoke the `coordinate` (or `warp-coordinate`) skill. The skill drives `coord`; you rarely call it by hand.

## The CLI

```
coord claim   <resource> [--prio P0..P3] [--hold 15m] [--id X] [--for "..."] [--next "..."] [--profile ...] [--session $CLAUDE_SESSION_ID]
coord request <resource>  ...            # phrased as asking a holder to yield
coord release <resource> --id X          # (--all releases every hold for --session)
coord grant   <resource> --re X          # yield to requester X
coord deny    <resource> --re X --for "frees in ~5m"
coord standing-release <resource> --for "merge-only session"
coord query   <resource> [--json]
coord status  [--json]
coord probe   <resource> [--cmd "..."]   # verify reachability (step 5)
coord sweep                              # expire lapsed holds
coord resources                          # list the registry
```

Exit codes: `0` granted/done/reachable, `10` queued (you do not hold — wait), `11` probe ran and failed (do not mutate), `12` probe refused — cannot resolve what to check.

`claim`/`request` also take `--holder-token <path>` / `--socket <path>` / `--pid <n>`; with none given, `coord` auto-reads `$CLAUDE_CODE_MESSAGING_SOCKET`.

## Context-aware probes (never test the wrong target)

A reachability probe must check the resource *actually claimed*, not whatever the environment defaults to. For `warp`, a bare `kubectl get ns` uses the kubeconfig's default context — `prod` on this machine — so probing a Dev lease that way would test PROD and falsely report Dev down. The registry declares a context-parameterized probe and a profile→context map:

```toml
[resource.warp]
probe_template = "kubectl --context {context} get ns --request-timeout=8s"
[resource.warp.contexts]
Dev = "surge-dev"
Prod = "prod"
```

`coord probe warp --profile Dev` fills `{context}` from the map (`surge-dev`), runs `kubectl --context surge-dev …`, and prints the context it tested. With no `--profile` it uses the held lease's profile. If the profile→context cannot be resolved (no profile, unknown profile, no template match), the probe **refuses with exit `12`** rather than fall through to the default context — a probe that can pass/fail against the wrong resource is worse than none. `--cmd` overrides the command verbatim.

## Three-layer dead-holder liveness

A holder can vanish three ways, each with its own reaper:

1. **Clean exit** → the `SessionEnd` hook releases every lease immediately.
2. **Unclean exit** (`kill -9`, OOM, crash — no hook fires) → **liveness reaping**: a claim records the session's messaging-socket path as a token, and every read (`query`/`claim`/`status`/`sweep`) reaps a holder whose session is dead, long before the TTL.
3. **Anything else** (a token that outlives its session, an anonymous hold) → the **lease TTL** backstop.

Liveness is not file existence — a `kill -9`'d process leaves its `.sock` file behind (bind creates the inode; only unlink removes it), so a stat would report a dead holder LIVE forever. It is a `connect()` probe: ECONNREFUSED (inode present, no listener) is the reap signal, confirmed by one retry so a momentarily-full accept backlog is never mistaken for death. A socket-inode **nonce** recorded at claim time defeats pid-reuse (the socket path is pid-named, so a recycled pid could bind the same path — a differing inode proves the original holder is gone). The probe is **asymmetric by design**: it reaps only on a *positive* dead signal (ECONNREFUSED, ESRCH, inode mismatch) and treats every ambiguity (EACCES, timeout, vanished path) as ALIVE — a false DEAD would let two sessions hold one resource (catastrophic), a false LIVE merely waits for the TTL (safe). `query`/`status` surface each holder's reaper mode: `liveness (connect + inode-nonce verified)`, `pid-path (weak: pid-reuse blind)`, or `ttl-only (no liveness token)`.

## Adding a resource

Edit `config/resources.toml` — never the engine:

```toml
[resource.my-thing]
description = "What it is and why it's shared."
default_hold = "10m"
probe = "some command; exit 0 == reachable"
```

The engine coordinates any resource name, registered or not; the registry only supplies metadata and the probe.

## Ledger location

`/tmp/cc-coord/ledger.json` by default (a fixed, machine-shared path, not a per-user `$TMPDIR`). Override with `COORD_DIR`. Override the registry path with `COORD_RESOURCES`, or the CLI binary the hook calls with `COORD_BIN`.
