# Claude Code Hooks

Hooks live in this directory and are wired in `claude-settings.json` under
the `hooks.*` blocks. Each hook receives a JSON payload on stdin and may
write a JSON object to stdout describing additional context, permission
decisions, or no-op (silent exit).

## SessionStart

| Hook                                         | Purpose                                                      |
| -------------------------------------------- | ------------------------------------------------------------ |
| `obsidian-second-brain/hooks/load_vault_context.py` | Loads vault identity + active state.                  |
| `preload-fff-tools.sh`                       | Primes the LLM to call `ToolSearch` for fff MCP early.        |
| `refresh-tool-registry.sh`                   | Rebuilds `~/.claude/cache/tool-registry-manifest.json`.       |

## SubagentStart

| Hook                       | Purpose                                                                        |
| -------------------------- | ------------------------------------------------------------------------------ |
| `inject-guidelines.py`     | Injects per-profile guideline prose snippets (`<!-- inject: ... -->` override). |
| `inject-tool-digest.py`    | Injects a profile-filtered, health-aware tool digest (`<!-- tools: ... -->` override). |

## PreToolUse

| Matcher    | Hook                          | Purpose                                                     |
| ---------- | ----------------------------- | ----------------------------------------------------------- |
| `Bash`     | `enforce-tool-registry.py`    | Registry-driven nudge for search/listing/git-status verbs.  |
| `mcp__.*`  | `enforce-tool-registry.py`    | Same hook, fires on MCP tool calls.                         |

## PostToolUse

| Matcher    | Hook                       | Purpose                                                  |
| ---------- | -------------------------- | -------------------------------------------------------- |
| `Bash`     | `audit-search-tools.sh`    | Logs search-class tool calls to `~/.claude/logs/...`.    |
| `mcp__fff__.*` | `audit-search-tools.sh` | Same hook, fires on fff MCP calls.                       |

## Other

| Event              | Hook                          |
| ------------------ | ----------------------------- |
| `UserPromptSubmit` | `tmux-rename-session.sh`      |
| `Stop`             | `notify.sh -s done -q`        |
| `Notification`     | `notify.sh -s info -q`        |
| `PostCompact`      | `obsidian-bg-agent.sh`        |

## Tool registry

`refresh-tool-registry.sh`, `inject-tool-digest.py`, and
`enforce-tool-registry.py` are the three integration points for the
materialised tool registry. See `docs/tool-registry.md` for the full design
and triage flow.

## Deprecated

- `enforce-tool-guidelines.sh` — replaced by `enforce-tool-registry.py`.
  Kept on disk through a probation window so the swap is trivially
  reversible (revert the `claude-settings.json` change). To be removed
  once at least one full session in advisory mode has produced a clean
  `~/.claude/logs/search-tool-audit.jsonl`.
- `inject-tool-guidelines.sh` — superseded by `inject-guidelines.py`.
