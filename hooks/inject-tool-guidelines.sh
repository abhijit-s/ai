#!/usr/bin/env bash
# Inject tool hierarchy guidelines into every sub-agent at spawn time

guidelines=$(cat << 'EOF'
TOOL HIERARCHY GUIDELINES (from CLAUDE.md — follow these in priority order):

1. fff MCP  — FIRST CHOICE for all file search, glob, and grep inside the git-indexed project.
2. ast-grep — Syntax-aware structural code search when fff MCP is insufficient.
3. rg (ripgrep) — Full-text search when fff MCP / ast-grep don't fit.
   Use full language names: --type ruby (not --type rb), --type typescript, etc.
4. fd — File discovery by name/pattern. NEVER use find instead of fd.
5. grep / find — LAST RESORT ONLY. Use only when the above tools are genuinely unavailable.

BASH COMMAND GUIDELINES:
- Never use shell loops (for/while). Use fd -x or rg instead.
- Never use find — use fd.
- Never use grep -r — use rg.
- For multi-file discovery spanning >5 files, spawn an Explore sub-agent.

Examples:
  Wrong: find . -name "*.rb" -exec cat {} \;
  Right:  fd -e rb -x cat {}

  Wrong: grep -r "pattern" .
  Right:  rg "pattern"

  Wrong: grep -r "pattern" . --include="*.ts"
  Right:  rg "pattern" --type typescript

VOCABULARY GUIDELINES (from CLAUDE.md — hard requirement):
- Every response must expand acronyms at least once on first use.
- Use inline expansion: write the full expansion in brackets immediately after the acronym.
  Example: "PSA (Pod Security Admission)", "ARC (Actions Runner Controller)", "CI (Continuous Integration)"
- OR append a glossary block at the end listing every acronym used, if inline gets too verbose.
- Do NOT assume the reader knows any acronym, no matter how common it seems.
- If a response uses no acronyms, this rule is satisfied automatically.
EOF
)

jq -n --arg ctx "$guidelines" '{"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": $ctx}}'
exit 0
