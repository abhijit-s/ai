# Claude Code Hooks

Hooks live in this directory and are wired in `claude-settings.json` under
the `hooks.*` blocks. Each hook receives a JSON payload on stdin and may
write a JSON object to stdout describing additional context, permission
decisions, or no-op (silent exit).

## SessionStart

| Hook                                         | Purpose                                                      |
| -------------------------------------------- | ------------------------------------------------------------ |
| `obsidian-second-brain/hooks/load_vault_context.py` | Loads vault identity + active state.                  |
| `preload-search-tools.sh`                    | Primes the LLM to call `ToolSearch` for fff MCP (lexical) and turbo-rag (conceptual) early; lists the live turbo-rag corpus roots. |
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
