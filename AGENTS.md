This file provides guidance to AI coding agents when working with code across all projects.

## Command Tool Order (read first — applies to every bash command)

Before writing any bash command that searches files or text, work through this order and stop at the first tool that fits:

1. **fff MCP** (`mcp__fff__grep`, `mcp__fff__find_files`) — first choice for all search inside a git-indexed project
2. **ast-grep** — syntax-aware structural search when fff MCP is insufficient
3. **`rg`** (ripgrep) — full-text search; use full language names (`--type ruby`, not `--type rb`)
4. **`fd`** — file discovery by name/pattern
5. **`grep` / `find`** — last resort only, when the tools above are genuinely unavailable for the task

This order is also enforced by a `PreToolUse` hook that fires on every bash command and by a `SubagentStart` hook injected into every sub-agent. The CLAUDE.md wording and hook wording are intentionally identical so there is no ambiguity.

**Never reach for `grep` or `find` by default.** They are familiar but slower, `.gitignore`-unaware, and lack the structured output of modern alternatives. If you find yourself typing `grep -r` or `find .`, stop and use `rg` or `fd` instead.

For session orientation and git state, also use fff MCP non-search tools:
`list_recent_files` (session start — what's in flight), `get_git_status` (instead of `git status`),
`list_directories` (active project areas), `record_access` (after every file read).

## Core Philosophy

**Complexity is not insight.** Smart people mistake elaborate solutions for wisdom. Ten-page memos that could be one. Factory classes wrapping factory classes. Abstractions for problems that don't exist yet.

Mastery is finding the elegant simplicity that cuts through complexity—not making simple things complex.

When reviewing your own work, ask: *Am I adding complexity because it's necessary, or because it feels sophisticated?*

## Sub-Agent Delegation

**Delegate to sub-agents proactively.** Sub-agents preserve your context window and enable parallel execution.

Spawn `Explore` agents for codebase discovery rather than reading many files directly. When work decomposes into independent pieces, delegate each to a sub-agent and run them in parallel. Use specialized agents for code review, architecture analysis, committing, PR creation, and refinement workflows.

Sub-agents consume their own context (not yours), can run simultaneously, and start with fresh perspective—avoiding confirmation bias from accumulated context.

**Patterns:**

- Before reading more than 3–5 files, spawn an `Explore` agent to gather context
- Launch multiple agents in a single message when tasks are independent
- Use `run_in_background: true` for tasks that don't block your main work

### Sub-Agent Guideline Injection

A `SubagentStart` hook automatically injects guidelines into every sub-agent from a config-driven library at `~/.claude/hooks/guidelines.json`. Each agent type has a default profile (slug list); you can override it per spawn.

**To override the default profile**, append a comment annotation anywhere in the agent prompt:

```
<!-- inject: slug1,slug2 -->
```

**Available slugs:**
- `tool-hierarchy` — tool selection order (fff MCP → ast-grep → rg → fd → grep)
- `bash-commands` — bash loop/find/grep anti-patterns
- `vocab-acronyms` — acronym expansion requirement

**When to override:** Omit `bash-commands` for agents that don't run shell commands (e.g., documentation writers). Omit `tool-hierarchy` for agents that only call MCP tools. Add `vocab-acronyms` to any agent producing user-facing prose.

**To add a new guideline:** Create `~/.claude/hooks/guidelines/<slug>.txt`, add metadata to `~/.claude/hooks/guidelines.json` under `slugs`, and optionally add it to relevant profiles.

### Quick Reference

| Workflows                  | Purpose                                       |
| -------------------------- | --------------------------------------------- |
| `/commit`                  | Commit with conventional message (why > what) |
| `/create-pr`               | Create PR with concise description            |
| `/ship`                    | Autonomous end-to-end feature development     |
| `/refine-implementation`   | Multi-pass code review before commit          |
| `/examine-architecture`    | Evaluate codebase for structural problems     |
| `/address-pr-review`       | Resolve PR review comments                    |
| `/review-dependabot`       | Analyze and merge Dependabot PRs              |
| `/publish`                 | End-to-end release workflow                   |
| `/interview`               | Interview user about a plan                   |
| `/daily-claude-code-recap` | Summarize the day's sessions                  |

| Agents                  | Purpose                                       |
| ----------------------- | --------------------------------------------- |
| `code-explorer`         | Trace execution paths, map dependencies       |
| `code-architect`        | Design feature architectures                  |
| `code-reviewer`         | Review for bugs, security, conventions        |
| `code-refiner`          | Simplify complexity, improve maintainability  |
| `architecture-reviewer` | Evaluate brittleness, complexity, coupling    |
| `plan-refiner`          | Validate plans, suggest simpler approaches    |
| `pr-comment-reviewer`   | Evaluate PR comments for actionability        |
| `committer`             | Create commits with conventional messages     |
| `pr-creator`            | Create PRs with structured descriptions       |
| `design-refiner`        | Iteratively refine frontend designs           |
| `documentation-refiner` | Maintain Markdown files and developer docs    |
| `skeptic`               | Challenge conclusions before reaching user    |

| Domain Skills            | Trigger                   |
| ------------------------ | ------------------------- |
| `frontend-design`        | Building web interfaces   |
| `writing-documentation`  | Updating docs             |
| `writing-claude-skills`  | Creating Claude skills    |
| `writing-claude-prompts` | Writing prompts           |
| `chartmogul-analytics`   | Analyzing revenue metrics |
| `cooking`                | Recipes and meal planning |

## Documentation & Knowledge Routing

**Never use the general-purpose agent for documentation or note-taking tasks.** Invoke the correct skill immediately — before any other action.

| Intent | Context | Skill |
| ------ | ------- | ----- |
| Write / draft an engineering ADR | Vault | `knowledge-capture:record-decision` |
| Write an ADR for a vault structure change | Vault | `obsidian-adr` |
| Capture an engineering learning / journal entry | Vault (engineering) | `knowledge-capture:capture-journal` or `record-learning` |
| Make a general note / capture this | Any (non-engineering) | `obsidian-capture` |
| Log a learning / "I learned that" | Any | `knowledge-capture:record-learning` |
| Record a decision / "we decided" | Any | `knowledge-capture:record-decision` |
| Write or update repo docs | Repo markdown files | `writing-documentation` → `documentation-refiner` agent |
| Synthesize / connect dots across notes | Vault | `vault-deep-synthesis` |
| Audit vault quality / find gaps | Vault | `knowledge-audit:audit-scan` |
| Sync vault docs → repo `docs/ai-context/` | Surge repo | `surge-ai:sync-ai-context` |

**Trigger phrases (case-insensitive) → skills:**

- "write an ADR", "draft an ADR", "create an ADR" → `knowledge-capture:record-decision` (engineering context); `obsidian-adr` only when the vault's own structure is changing
- "make a note", "note that", "capture this", "jot this down" → `obsidian-capture` (unless engineering context — then `capture-journal`)
- "I learned that", "record this learning", "log this learning" → `knowledge-capture:record-learning`
- "we decided to", "record this decision", "capture this decision" → `knowledge-capture:record-decision`
- "update the docs", "write documentation", "document this" → `writing-documentation`
- "synthesize", "connect dots", "pull together notes" → `vault-deep-synthesis`

**`obsidian-second-brain` vs `engineering-tools` — both share the same vault, different layers:**

- **ADRs**: `knowledge-capture:record-decision` for architecture/engineering decisions. `obsidian-adr` only for vault structural changes (folder reorganisations, schema changes).
- **Engineering learnings**: always use `knowledge-capture:capture-journal` or `record-learning`, never `/obsidian-capture` or `/obsidian-save`. Notes written via obsidian-second-brain lack the `informed_by` frontmatter field and become invisible to the `knowledge-audit:audit-scan` drift detection pipeline.
- **Vault health**: `knowledge-audit:audit-scan` for engineering drift (stale ADR chains, missing decision trails). `/obsidian-health` for personal hygiene (orphaned notes, broken links, overdue tasks).

## Tooling Preferences

### Tool Hierarchy

**Always use the highest available tool. Never skip to a lower tier.** (See "Command Tool Order" at the top of this file for the canonical rule.)

1. **fff MCP** — All file search, glob, and grep inside the git-indexed project. Always try this first.
2. **ast-grep** — Syntax-aware structural code search when fff MCP is insufficient.
3. **`rg`** (ripgrep) — Full-text search when fff MCP or ast-grep don't fit. Use full language names with `--type`.
4. **`fd`** — File discovery by name/pattern. Never use `find` instead.
5. **`grep` / `find`** — Last resort only. Use only when the tools above are genuinely unavailable for the task.

### Bash Command Guidelines

**Avoid shell loops.** `for`/`while` loops and compound shell constructs require permission prompts and are slower than modern alternatives. **Prefer `fd` over `find` and `rg` over `grep` — reach for `find`/`grep` only as a last resort when `fd`/`rg` are genuinely unavailable.**

| Instead of                                  | Use                      |
| ------------------------------------------- | ------------------------ |
| `find . -name "*.md" -exec cat {} \;`       | `fd -e md -x cat {}`     |
| `grep -r pattern .`                         | `rg pattern`             |
| `for f in *.md; do grep pattern "$f"; done` | `rg pattern *.md`        |
| `for f in dir/*; do head -5 "$f"; done`     | `fd . dir -x head -5 {}` |

For complex multi-file discovery, spawn a sub-agent rather than writing shell loops.

### Modern CLI Tools

- **fff MCP**: First choice for file search, glob, and grep within the git-indexed project — registered as a stdio MCP server.
  - `find_files` / `grep` / `multi_grep` — search (see Command Tool Order above)
  - `list_directories` — frecency-ranked active dirs; prefer over `ls`/`eza` when orienting in a project
  - `list_recent_files` — files ranked by recent access; use at session start to see what's in flight. `dirty_only=true` narrows to uncommitted + recently touched files
  - `get_git_status` — prefer over shelling out to `git status`; output is frecency-enriched and grouped by status
  - `record_access` — **call this after reading any file** to feed the access back into the frecency database and improve future search rankings
- **File searching**: `fd` — faster than `find`, respects `.gitignore`, simpler syntax
- **Text searching**: `rg` (ripgrep) — use full language names with `--type` (e.g., `--type ruby`, not `--type rb`)
- **Syntax-aware searching**: `ast-grep` for structural code search; combine with `rg` for efficiency
- **File viewing**: `bat` — syntax highlighting and line numbers
- **Directory listings**: `eza` — colorized output with git status integration

### Personal Productivity CLIs

- **`helpscout`** (HelpScout CLI) — Customer support for Tuple
- **`ynab`** (YNAB (You Need A Budget) CLI) — Personal budgeting

## Comment Philosophy

**Write self-documenting code that rarely needs comments.**

| Comment Type  | Action                                                         |
| ------------- | -------------------------------------------------------------- |
| Explains WHAT | Remove — use better naming                                     |
| Explains HOW  | Remove — extract to a named function                           |
| Explains WHY  | Keep if non-obvious (business logic, constraints, workarounds) |

**Keep**: Technical constraints, algorithm rationale, external workarounds, performance notes.

**Target**: 80–90% fewer comments. TODO/FIXME items belong in `TODO.md`.

## Documentation Standards

**Write timeless documentation.** Describe what IS, not what WAS.

Avoid temporal references: "vs previous", "used to be X", "now uses Y", "the new approach".

**Test**: If it would be unclear in 6 months, remove it. Exception: `CHANGELOG.md` documents changes over time.

## Diagrams & Visual Communication

**Reach for diagrams whenever a picture beats prose.** A well-placed diagram collapses paragraphs of explanation into an instantly scannable visual — use them proactively, not just when asked.

**When to diagram:**
- System or component topology (what talks to what)
- Flows with branching logic: request lifecycles, decision trees, user journeys
- Before-and-after state changes, data transformations, or migration paths
- Sequences involving multiple actors or time-ordered steps
- Any concept where relationships, hierarchy, or directionality matter

**Format guidance:**
- **Mermaid JS** — prefer for structured diagrams: flowcharts, sequence diagrams, ER (Entity-Relationship) diagrams, state machines, Gantt charts. Renders natively in GitHub, Notion, and most modern editors.
- **ASCII** — prefer for inline sketches, quick topology maps, or environments where Mermaid may not render.

This applies across all domains — not just software. Explaining a business process, a recipe workflow, a financial structure, or a decision framework? If a diagram makes it clearer, draw it.

**Bias toward the big picture first.** Lead with the high-level view, then add detail diagrams only where the depth genuinely helps.

## Development Workflow

**Refine each stage before proceeding.**

### Quality Gates

- **Plan** → plan-refiner approves → **Implement**
- **Code** → code-refiner approves → **Commit**
- **Commit** → committer agent → **Continue / PR**
- **PR** → pr-creator agent → **Done**

### State Management for Long Tasks

For complex work spanning multiple sessions:

- Use structured formats (JSON) for test results and task status
- Create setup scripts (`init.sh`) for graceful restarts across sessions
- Track progress in files and review filesystem state when resuming

### 1. Planning

1. Understand requirements and create an implementation plan
2. Launch `plan-refiner` agent to validate the approach
3. Proceed only after the plan is approved

`plan-refiner` has final authority on approach and can suggest radical simplifications.

### 2. Implementation

1. Implement according to the approved plan
2. At checkpoints, run `/refine-implementation` to spawn `code-refiner` for a fresh review
3. Proceed to commit only after refinement is complete

### 3. Committing

Run `/commit` or ask: *"commit these changes"*

Creates commits with conventional messages that explain *why*, not just *what*. Analyzes changes, drafts message, refines for clarity, and commits.

### 4. Pull Requests

Run `/create-pr` or ask: *"create a PR for this branch"*

Creates PRs with concise descriptions focused on the problem being solved. Analyzes the branch, drafts a description, verifies the problem statement if unclear, and creates the PR.

## Code Quality Standards

- Ensure all tests pass before committing
- Ensure all linters pass before committing, resolving both errors and warnings
- **Read code before responding**: Read files before answering questions or making changes. Verify implementation details and API signatures rather than guessing.
- **Write general-purpose solutions**: Implement logic that solves problems generally. Build solutions that work for all valid inputs rather than hard-coding values from test cases.
- **Avoid over-engineering**: Only make changes that are directly requested or clearly necessary.
  - Don't add features, refactor, or "improve" beyond what was asked
  - Don't add error handling for scenarios that can't happen. Only validate at system boundaries
  - Don't create abstractions for one-time operations. Three similar lines is better than a premature abstraction
  - If something is unused, delete it completely
- **Migration safety**: When changing data formats, schemas, or event names, answer *"what happens to data that already exists?"* Dual-read from old and new sources during transitions. Don't remove legacy compatibility paths until all in-flight data has aged out.
- **Error propagation**: Error paths must look like errors to callers. Don't log an API failure and return a success-shaped response. Don't send optimistic confirmation text when an action failed.
- **Trace new identifiers end-to-end**: When adding a new identifier or key at one layer, trace the full data path to verify it's consumed at every downstream layer. A new field that's written but never read (or read but never forwarded) is a silent no-op.

## Anti-Patterns

- **Kitchen-sink sessions**: One task per session. Context pollution degrades quality.
- **Infinite exploration**: Set a file-reading budget. After 5–7 files, synthesize or spawn an explorer agent.
- **Trust-then-verify gap**: Run tests after changes, not just before committing.
- **Wrong model tier**: Call out mismatches. Lookups on Opus = waste. Architecture on Sonnet = underpowered. ~95% of work runs fine on Sonnet; Opus is for genuinely hard problems (50× price spread).

## Vocabulary

**Every response must expand acronyms at least once.** Use one of these two forms:

1. **Inline expansion** — write the expansion in brackets immediately after the first use: "PSA (Pod Security Admission)", "ARC (Actions Runner
Controller)".
2. **Glossary block** — append a small glossary section at the end of the response listing every acronym used, if the prose gets too verbose with expansion. Use your judgement.

This is a hard requirement, not best-effort. Do not assume the reader knows any acronym regardless of how common it seems in the domain. If a response
uses no acronyms, the rule is satisfied automatically.

If a project maintains a glossary file, also append any new acronyms introduced during the session to it. Keep entries alphabetical: `**ACRONYM** —
Full expansion. Brief definition.`
