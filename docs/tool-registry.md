# Tool Registry

A profile-filtered, auto-introspected catalog of every tool available to a
Claude Code session — both MCP (Model Context Protocol) tools and shell
CLI tools — with health, capability tags, and per-intent preference chains.

## What it solves

Claude Code surfaces tools across three context zones (eager-zone schemas,
deferred-zone names, one-shot system-reminders). The deferred zone is a
flat list with no capability or health metadata, and system-reminders
never update mid-session. The registry materialises this missing surface
once per session so:

- Sub-agents see a profile-filtered digest of the tools they should reach
  for (and which are currently healthy).
- The PreToolUse hook nudges away from training-prior calls (`grep -r`,
  `find .`) toward the registry's canonical preference (`mcp__fff__grep`,
  `mcp__fff__find_files`).
- Adding a new MCP server requires zero registry code — just register it
  in `mcp.json` or `claude-settings.json`.

## Setup after clone

The MCP (Model Context Protocol) server's `node_modules/` is gitignored by
the repo's root `.gitignore`. After a fresh clone of the dotfiles repo,
run a one-time install:

```bash
cd mcp-servers/tool-registry
npm install
```

This is a post-clone bootstrap step only — no `npm install` runs at
`SessionStart` or at runtime. Re-run it only when `package-lock.json`
changes (e.g., an SDK bump). The `obsidian-vault` MCP server has the
same convention.

## Where it lives

| Component                                | Path                                                    |
| ---------------------------------------- | ------------------------------------------------------- |
| MCP server (Node)                        | `mcp-servers/tool-registry/`                            |
| Manifest cache (per-session)             | `~/.claude/cache/tool-registry-manifest.json`           |
| CLI tool enumeration                     | `hooks/tools/cli-tools.yaml`                            |
| Annotation overlay                       | `hooks/tools/annotations.yaml`                          |
| Profile catalog                          | `hooks/profiles.json`                                   |
| Python client (shared by hooks)          | `hooks/lib/tool_registry_client.py`                     |
| SessionStart refresh                     | `hooks/refresh-tool-registry.sh`                        |
| SubagentStart digest                     | `hooks/inject-tool-digest.py`                           |
| PreToolUse nudge                         | `hooks/enforce-tool-registry.py`                        |
| PreToolUse:Task override stash           | `hooks/pretool-stash-override.py`                       |
| Per-session override queue (FIFO)        | `~/.claude/cache/subagent-overrides/<session_id>.jsonl` |
| Override queue helper                    | `hooks/lib/override_queue.py`                           |

## Manifest shape

```yaml
schema_version: 1
generated_at: 2026-06-10T14:00:00Z
last_success: 2026-06-10T14:00:00Z
tools:
  mcp__fff__grep:
    name: mcp__fff__grep
    source:
      kind: mcp
      server: fff
      tool: grep
    schema: { ...auto-discovered from tools/list... }
    description: "Search file contents (frecency-ranked)"
    category: [search-content]
    capability_tags: [content-search, frecency-ranked, indexed]
    prefer_over:
      search-content: [rg, grep]
    compose_with: [Read, mcp__fff__record_access]
    health:
      state: healthy
      checked_at: 2026-06-10T14:00:00Z
      detail: "tools/list returned 7 tools"
discovery_errors:
  - server: chartmogul
    kind: handshake
    detail: "spawn chartmogul ENOENT"
```

## Profile catalog

Every sub-agent type in `hooks/guidelines.json` has a corresponding profile
entry. Agents on disk without an entry (`design-refiner`,
`pr-comment-reviewer`, `committer`, `pr-creator`) fall through to the
`default` profile. The main loop uses `session-default`, which ships with
the broad category set because its tool mix is unbounded.

To add a new agent profile, edit `hooks/profiles.json`. To narrow an
existing profile, remove categories or tools from its entry.

## Override comments

A sub-agent prompt may carry an override comment that replaces the default
profile filter for that single spawn:

```
<!-- tools: documentation-refiner -->          # use a named profile
<!-- tools: mcp__fff__grep,find-files -->      # ad-hoc tool + category list
```

The `tools:` prefix is independent of `inject-guidelines.py`'s `inject:`
prefix — a prompt may carry both and both take effect.

### Override comments — how they work

The live `SubagentStart` event from Claude Code does NOT carry the spawn
prompt. To make the override comments reachable, a `PreToolUse:Task`
hook (`hooks/pretool-stash-override.py`) parses both `<!-- tools: ... -->`
and `<!-- inject: ... -->` from `tool_input.prompt`, then stashes them
in a per-session FIFO (First-In-First-Out) queue:

```
~/.claude/cache/subagent-overrides/<session_id>.jsonl
```

Each line is a JSON entry of the form
`{ts, agent_type, tool_use_id, tools, inject}`. On `SubagentStart`,
`inject-tool-digest.py` and `inject-guidelines.py` each look up the
earliest queue entry matching `(session_id, agent_type)` with a
non-empty `tools` / `inject` field, consume it, and atomically rewrite
the queue file. Entries older than 30 minutes are dropped on every
write, so the file stays bounded without a separate sweeper.

The PreToolUse matcher is `Task|Agent` because the captured event has
`tool_name == "Agent"` but Claude Code historically also routes the
matcher string `Task` to the same tool.

#### Documented limitation

Correlation is FIFO on `(session_id, agent_type)` — the `tool_use_id`
from PreToolUse and the `agent_id` from SubagentStart live in different
ID spaces and cannot be matched directly. If two parallel spawns of the
**same** agent type carry **different** overrides, they may get matched
out of order in the race window. Single-spawn-at-a-time and
different-type parallel spawns are unaffected. This is acceptable for
v1.

## Browser automation: two categories, split on capability

Browser tools are annotated across two categories rather than one, because
the two servers are not interchangeable:

| Category             | Head of chain | Covers                                                                     |
| -------------------- | ------------- | -------------------------------------------------------------------------- |
| `browser-automation` | lightpanda    | Navigation, page reads, extraction, DOM interaction, waits, script eval    |
| `browser-visual`     | playwright    | Screenshots, viewport, drag/drop, uploads, native dialogs, tabs, network   |

Lightpanda is a headless browser with no renderer — cheap to start, small in
memory — so every `browser-automation` chain is headed by a
`mcp__lightpanda__*` tool, with the `mcp__playwright__*` equivalent carrying
no chain and therefore sorting into the fallback tier.

`browser-visual` holds the capabilities lightpanda **cannot** provide at all.
There is nothing to rank against, so those entries carry no chain and the
PreToolUse hook stays silent on them — a screenshot call is never nudged.

### The unavailability fallback is health-driven, not annotated

"Use lightpanda first, playwright if unavailable" needs no `fallback_to`
wiring. Both consumers filter on health:

- `inject-tool-digest.py` skips non-`healthy` tools when building the digest.
- `enforce-tool-registry.py::pick_alternatives` only offers `healthy`
  alternatives.

So if the lightpanda handshake fails, its tools drop out of the digest and
stop being suggested — playwright becomes the visible head of
`browser-automation` on its own, and playwright calls draw no nudge. Verify
with a doctored manifest:

```bash
jq '(.tools | to_entries | map(if (.key|startswith("mcp__lightpanda__"))
  then .value.health.state = "unhealthy" else . end) | from_entries) as $t | .tools = $t' \
  ~/.claude/cache/tool-registry-manifest.json > /tmp/fakehome/.claude/cache/tool-registry-manifest.json
echo '{"tool_name":"mcp__playwright__browser_navigate","tool_input":{}}' \
  | HOME=/tmp/fakehome python3 hooks/enforce-tool-registry.py   # → no output
```

### Why the descriptions are overridden

Lightpanda ships multi-paragraph tool descriptions (its `save` and `extract`
entries run to hundreds of words). The digest prints
`description` verbatim into every sub-agent's context, so
`annotations.yaml` overrides each one with a single line. Niche lightpanda
tools (`session_*`, `save`, `structuredData`, `nodeDetails`, `detectForms`,
`scroll`, `getCookies`) are deliberately left un-annotated: with no
category they stay fully callable but out of the digest and the nudge path,
which keeps the injected digest to ~40 lines.

## How to add a new MCP server

1. Register it in `mcp.json` (or `claude-settings.json::mcpServers`).
2. Optional: add annotation entries in `hooks/tools/annotations.yaml`
   for its tools' `category`, `capability_tags`, and `prefer_over`. The
   registry tolerates missing entries — un-annotated tools flow through
   with empty metadata.
3. Restart the session (or call `mcp__tool-registry__refresh`).

That's it. No registry code change.

## How to add a new category

1. Use the new category name in any annotation entry's `category` field
   in `hooks/tools/annotations.yaml`.
2. Optional: reference the new category in a profile's `categories`
   list in `hooks/profiles.json`.
3. Restart the session (or call `mcp__tool-registry__refresh`).

The category taxonomy is open with a documented closed seed — see KTD2
in the plan.

## Health resolution

| Tool source  | Health rule                                                  |
| ------------ | ------------------------------------------------------------ |
| MCP server   | `tools/list` handshake succeeded with ≥1 tool → `healthy`    |
| MCP server   | Handshake succeeded but returned 0 tools → `unhealthy`       |
| MCP server   | Handshake failed → `unhealthy` with the error in `detail`    |
| MCP server   | Global 15s discovery deadline exceeded → `timeout`           |
| CLI tool     | `command -v <name>` returned a path → `healthy`              |
| CLI tool     | `command -v <name>` returned non-zero → `unhealthy`          |

Auth-gated MCP servers (`claude_ai_*` OAuth set, etc.) frequently fall
into the "handshake succeeded, tools/list returned empty" bucket. This is
treated as `unhealthy` because operationally the server is unusable
without credentials.

## Refresh model

The cache is built once per session by the `SessionStart` hook
(`hooks/refresh-tool-registry.sh`). On-demand rebuilds are available via
`mcp__tool-registry__refresh` (returns `{ tools_count, healthy_count,
errors_count }`). `mcp__tool-registry__tool_health(name)` re-probes a
single tool and updates the cache entry without rebuilding everything.

The cache is written atomically (tmp + fsync + rename) so a partial-write
can never produce a half-parsed read.

## Scope boundaries (v1)

- Plugin-registered MCP servers (the `claude_ai_*` set injected by the
  compound-engineering plugin) are not enumerated. Discovery sources are
  `mcp.json` + `claude-settings.json::mcpServers` only.
- Mid-session health re-discovery is not automatic. Use `tool_health(name)`
  or `refresh()`.
- The registry has no cross-session learning. Each session starts fresh.
- The PreToolUse hook always returns `permissionDecision: allow` in v1.
  `block_unhealthy: true` is forward-compat infrastructure; the per-profile
  flip is gated on accumulated audit-log evidence (see the plan's open
  question on block-mode rollout cadence).

## Triage

- Check whether the cache exists: `ls ~/.claude/cache/tool-registry-manifest.json`
- Inspect discovery errors: `jq .discovery_errors ~/.claude/cache/tool-registry-manifest.json`
- Re-probe one tool: call `mcp__tool-registry__tool_health(name="<tool>")` from a session.
- Force a full rebuild: call `mcp__tool-registry__refresh()` or run
  `bash hooks/refresh-tool-registry.sh </dev/null`.
- Check the audit log: `tail ~/.claude/logs/search-tool-audit.jsonl`
