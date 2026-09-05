---
name: warp-coordinate
description: The WARP-tunnel specialization of the generic `coordinate` skill — it supplies the COORD protocol and the `coord` CLI; this binds it to one resource. Use before claiming or switching the shared Cloudflare WARP tunnel (Cloudflare's zero-trust client / VPN, selected via `warpctx`) for cluster work — kubectl, terraform apply, argocd sync/get, in-cluster reads — while other Claude sessions may be active, when a `warpctx ensure <profile>` is needed and you are not sure you are the only session, or when a peer's tunnel switch could retarget your commands mid-operation.
---

# Coordinate the Shared WARP Tunnel

This is the **Cloudflare WARP specialization of the generic `coordinate` skill** (WARP is Cloudflare's zero-trust client / VPN (virtual private network) tunnel, not an initialism). The `COORD` message grammar, verbs, fields, priority ladder, deterministic rules, seven-step flow, and the `coord` CLI all live in `coordinate` and apply here unchanged. **Read `coordinate` first.** Below are only the WARP-specific bindings and hazards.

## Resource binding

- **Resource name:** `resource=warp` on every message — i.e. `coord claim warp …`.
- **The resource is the tunnel context**, switched via the `warpctx` CLI (command-line interface): `warpctx current`, `warpctx ensure "Sidekick - Production"`, `warpctx ensure "Sidekick - Dev"`. A switch by **any** session changes the tunnel for **all** sessions.
- **Profile field:** `--profile Dev|Prod`, bound to the `warpctx` profile names `"Sidekick - Dev"` and `"Sidekick - Production"`. It rides through onto the emitted `COORD … profile=<Dev|Prod>` line.
- **Who contends:** only sessions doing kubectl / terraform / argocd / in-cluster reads. A code / canon / GitHub-merges-only session never touches the tunnel — it verifies deploys by reading `newTag` on the `chore/gitops-overlays` branch at a SHA over HTTPS (HyperText Transfer Protocol Secure) — so it should run `coord standing-release warp --for "…"` once, early.

Example WARP claim:

```
coord claim warp --id k1 --profile Dev --prio P3 --hold 5m --session $CLAUDE_SESSION_ID \
  --for "kubectl get ns" --next "~25m/10m dev apply"
# → COORD CLAIM resource=warp id=k1 profile=Dev prio=P3 hold=5m for="kubectl get ns" next="~25m/10m dev apply"
# (a liveness token is recorded automatically from $CLAUDE_CODE_MESSAGING_SOCKET,
#  so if this session is killed mid-hold the tunnel claim is reaped early, not held to TTL)
```

## WARP-specific hazards

### Prove reachability BEFORE mutating — the load-bearing step (generic step 5)

`warpctx current` naming a profile — and even `warpctx` reporting "Connected / healthy / 0% loss" — is **NOT** evidence the tunnel reaches the cluster. It has reported healthy for hours while private routes were dead (`kubectl` → "network is unreachable", `internal.surge.io` resolving to nothing), then recovered on its own. After your claim lands and before any dev or prod mutation, run **`coord probe warp`** — which runs a real in-cluster call that traverses the tunnel (`kubectl get ns`, per the registry) and returns exit `11` if it fails. If it fails while WARP claims healthy, **the tunnel is the prime suspect**. (There is no `warpctx` health verb beyond `current` / `ensure` to trust here — the real call `coord probe` runs is the only proof. For a prod check, `coord probe warp --cmd "kubectl get ns --context prod ..."` overrides the probe.)

### Never flap the tunnel mid-apply (generic step 6)

Flapping — switch away and back — during a terraform apply or any multi-step cluster operation can split an apply across two contexts. Even when honoring an owed higher-priority `REQUEST` or a `P0` preemption, finish the current atomic operation first, then `coord release warp --id <id>`.

### Prod delete/apply is owner-gated

The auto-mode classifier BLOCKS `kubectl delete` / `apply` on prod (it allows `patch` / `get`). Prod deletes/applies are run by the owner via a `!` shell prompt — a coordinating session can `get` / `patch` prod but must not `delete` / `apply` prod directly.

### Account-assert before any terraform apply

dev = `066283878314`, prod = `909133987739` — separate AWS accounts. Confirm the account id before applying; it is the strongest prod-safety gate. Map it to priority on your `coord claim`: a prod-safety mutation is `--prio P0`, a routine prod apply/verify is `P1`, a dev mutation is `P2`, a read is `P3`.

### Verify a rollout-restart by pod age, not `kubectl rollout status`

`rollout status` false-greens an unchanged deployment. Confirm a restart by new pod age / pod UID (unique identifier) instead. Relevant because tunnel work often ends in a restart.

## Remember

The tunnel lies about its own health, and a peer can move it under you. Neither is caught by looking at `warpctx` — only by `coord probe warp` (a real cluster call) and a `coord claim warp` your peers can see in the ledger and in the broadcast `COORD resource=warp` line.
