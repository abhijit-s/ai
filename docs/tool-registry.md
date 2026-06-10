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
      search-content: [ast-grep, rg, grep]
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
