This file provides guidance to AI coding agents when working with code across all projects.

## Command Tool Order (read first — applies to every search)

Before choosing a tool, decide what *kind* of search this is:

- **Lexical** — you know the identifier, symbol, literal string, or filename pattern. Follow the ladder below. This is the common case.
- **Conceptual / semantic** — you're looking for *notes or prose about a topic* where the wording may differ from your query ("notes about auth step-up", "where did I reason about outbox ordering", "prior art on X"). Reach for **turbo-rag** first — lexical grep will miss it whenever the file phrases the idea in different words than your query.

### Conceptual searches → turbo-rag (Retrieval-Augmented Generation index)

1. **`mcp__turbo-rag__hybrid_search`** — DEFAULT conceptual search; blends vector similarity with lexical signal. Use when unsure.
2. **`mcp__turbo-rag__semantic_search`** — pure vector similarity; for a concept that shares no keywords with the target text.

- **Scope**: only the indexed corpus roots (personal vault, umbrella workspace + `_Knowledge`, `abhi.easygo.io`, `surge.easygo.io`, and the `surge/app` + `surge/platform` local tiers). Outside those, turbo-rag returns `meta.unregistered_roots` — fall back to the lexical ladder, or price coverage with `estimate_corpus` and (after user approval) `register_corpus`.
- **Fallback**: if a conceptual search returns weak or empty results, drop to the lexical ladder — vocabulary you *do* know may match a file directly.

### Lexical searches → work through this order, stop at the first tool that fits

1. **fff MCP** (`mcp__fff__grep`, `mcp__fff__find_files`) — first choice for all lexical search in any context; git repos additionally get frecency boosting for dirty files
2. **ast-grep** — syntax-aware structural search when fff MCP is insufficient
3. **`rg`** (ripgrep) — full-text search; use full language names (`--type ruby`, not `--type rb`)
4. **`fd`** — file discovery by name/pattern
5. **`grep` / `find`** — last resort only, when the tools above are genuinely unavailable for the task

The **lexical ladder** is also enforced by a `PreToolUse` hook that fires on every bash command and by a `SubagentStart` hook injected into every sub-agent; the CLAUDE.md wording and hook wording are intentionally identical there. That hook fires on bash only — the conceptual/turbo-rag branch above is MCP-level and is not gated by it.

**Never reach for `grep` or `find` by default.** They are familiar but slower, `.gitignore`-unaware, and lack the structured output of modern alternatives. If you find yourself typing `grep -r` or `find .`, stop and use `rg` or `fd` instead.

For session orientation and git state, also use fff MCP non-search tools:
`list_recent_files` (session start — what's in flight), `get_git_status` (instead of `git status`),
`list_directories` (active project areas), `record_access` (after every file read).

> **Canonical preference data lives in the tool registry** — the table above is the human-facing summary; the live, profile-filtered, health-aware view is materialised at `~/.claude/cache/tool-registry-manifest.json` and surfaced to sub-agents via the `SubagentStart` digest hook. See `docs/tool-registry.md` for the full design, profile catalog, and how to add a new MCP server or tool category without touching registry code.

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

**Trigger phrases — these take priority over obsidian-second-brain equivalents:**

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

See **Command Tool Order** at the top of this file.

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

- **fff MCP** (stdio MCP server): search tools covered by Command Tool Order above. Also for session orientation:
  - `list_directories` — frecency-ranked active dirs; prefer over `ls`/`eza` when orienting in a project
  - `list_recent_files` — files ranked by recent access; use at session start to see what's in flight. `dirty_only=true` narrows to uncommitted + recently touched files
  - `get_git_status` — prefer over shelling out to `git status`; output is frecency-enriched and grouped by status
  - `record_access` — **call this after reading any file** to feed the access back into the frecency database and improve future search rankings
- **File searching**: `fd` — faster than `find`, respects `.gitignore`, simpler syntax
  - **Umbrella gotcha**: `~/vaults/workspace/.gitignore` excludes the nested corpus repos (`/surge.easygo.io/`, etc.) so the umbrella git repo stays clean. Default `fd`/`rg` run from the umbrella therefore **silently skip those subtrees entirely** — a search returns nothing, not because the file is missing but because the directory was pruned. Fix: scope to the repo (`cd surge.easygo.io` or pass it as an explicit path), or pass `-I`/`--no-ignore` (`-uu` for fully unrestricted) to reach in. Prefer fff MCP / `git -C <repo>` for these nested corpora — they sidestep the umbrella mask. See [[fff-default-root-personal-vault]].
- **Text searching**: `rg` (ripgrep) — use full language names with `--type` (e.g., `--type ruby`, not `--type rb`)
- **Syntax-aware searching**: `ast-grep` for structural code search; combine with `rg` for efficiency
- **Document conversion**: `markitdown` — convert any non-text file or URL to Markdown before reading. Use when the user shares a file Claude cannot read directly. Run `markitdown <file>` (stdout) or `markitdown <file> -o out.md` (file). Hint the format when piping from stdin: `markitdown -x pdf < file.bin`.
  - Supported inputs: PDF, Word (`.docx`), Excel (`.xlsx`/`.xls`), PowerPoint (`.pptx`), EPUB, Outlook email (`.msg`), CSV (→ Markdown tables), Jupyter notebooks (`.ipynb`), HTML, XML/RSS/Atom feeds, images (metadata + optional LLM description), audio/video (metadata + optional transcription), JSON/JSONL, ZIP archives, and URLs
  - **Always run `markitdown` first** on any of the above before attempting to read the raw bytes — do not try to parse binary formats directly.
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

### Commit & PR Attribution

**Never hardcode Claude attribution into a commit message or PR body.** No `Co-Authored-By: Claude …` trailer, no `🤖 Generated with [Claude Code]` line. Attribution is controlled centrally by the `attribution` block in `settings.json` (set to empty strings here, which suppresses it everywhere). A skill, agent, or template that pastes the trailer into the message body bypasses that setting and re-introduces attribution the user has explicitly turned off. When authoring or editing any commit/PR skill or agent template, omit attribution lines entirely.

### State Management for Long Tasks

For complex work spanning multiple sessions:

- Use structured formats (JSON) for test results and task status
- Create setup scripts (`init.sh`) for graceful restarts across sessions
- Track progress in files and review filesystem state when resuming

## Code Quality Standards

- Ensure all tests pass before committing
- Ensure all linters pass before committing, resolving both errors and warnings
- **Read code before responding**: Read files before answering questions or making changes. Verify implementation details and API signatures rather than guessing.
- **Write general-purpose solutions**: Implement logic that solves problems generally. Build solutions that work for all valid inputs rather than hard-coding values from test cases.
- **Separate engine from configuration (config-not-fork)**: Keep the generic mechanism in code; externalize project/environment-specific *and* tunable values — field names, paths, vocabularies, scoring weights, thresholds, message templates — into one declared config surface (parse with a stdlib parser; let env vars override the common case). **Litmus**: if a project-specific or tunable value appears in engine source, externalize it. Keep only universal algorithms and format-level patterns in code, and state the engine/config line explicitly. Optional accelerators (an external index or service) must degrade to a built-in baseline — never a hard dependency. The tool should be adopted by editing config, not forking.
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

## Response Style

**Use emojis in responses to add visual appeal and sharpen the message.** Deploy them to anchor key points, mark section transitions, and signal status (e.g. ✅ done, ⚠️ caution, 🔴 blocker, ▶ next). Favor impact over decoration — an emoji should help the reader scan and land the point, not clutter the prose. When in doubt, a few well-placed markers beat a scattering of them.

## Vocabulary

**Every response must expand acronyms at least once.** Use one of these two forms:

1. **Inline expansion** — write the expansion in brackets immediately after the first use: "PSA (Pod Security Admission)", "ARC (Actions Runner
Controller)".
2. **Glossary block** — append a small glossary section at the end of the response listing every acronym used, if the prose gets too verbose with expansion. Use your judgement.

This is a hard requirement, not best-effort. Do not assume the reader knows any acronym regardless of how common it seems in the domain. If a response
uses no acronyms, the rule is satisfied automatically.

**The same hard requirement applies to opaque identifiers.** Plan-unit IDs (`U1`, `U2`, … from `ce-plan`) and project handles (`ADR-NNN`, `BC-N`) are as uncorrelatable as an unexpanded acronym — on **first use in any response**, attach the short title (`U4 (author base contracts)`, `ADR-082 (domain event transport)`), never the bare handle alone. Bare handles are fine in later uses within the same response. If a response uses none, the rule is satisfied automatically.

If a project maintains a glossary file, also append any new acronyms introduced during the session to it. Keep entries alphabetical: `**ACRONYM** —
Full expansion. Brief definition.`

## Quick Reference

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
