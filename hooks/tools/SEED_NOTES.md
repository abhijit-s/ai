# Annotation Overlay — Seed Notes

One-shot transcription record. After this seed lands, the canonical
`prefer_over` data lives in `hooks/tools/annotations.yaml`. `AGENTS.md`
prose stays as human-facing documentation; future preference changes
land in the overlay file, not the prose.

## Source mapping

`AGENTS.md` "Command Tool Order" lists the chain:

> 1. fff MCP — first choice for all file search, listing, and git-status
> 2. ast-grep — syntax-aware structural code search
> 3. rg (ripgrep) — full-text search
> 4. fd — file discovery
> 5. grep / find — last resort

Translated to per-category `prefer_over` chains:

| Category         | Chain (head → tail)                                        |
| ---------------- | ---------------------------------------------------------- |
| search-content   | mcp__fff__grep → ast-grep → rg → grep                      |
| find-files       | mcp__fff__find_files → fd → find                           |
| list-dir         | mcp__fff__list_directories → eza → ls                      |
| git-state        | mcp__fff__get_git_status → git-status                      |
| ast-search       | ast-grep                                                   |
| semantic-search  | mcp__turbo-rag__semantic_search                            |

## Capability tags

Derived from `AGENTS.md` parenthetical notes:

- "frecency-ranked", "gitignore-aware", "indexed" — fff MCP tools
- "structural-search", "ast-aware" — ast-grep
- "content-search", "file-discovery", "directory-listing" — base verb category
- "last-resort" — grep, find (matches the bash hook's HARD tier)
- "semantic", "embedding-based" — turbo-rag semantic_search

## compose_with seed

Only one day-1 hint, on `mcp__fff__find_files`:

```yaml
compose_with: [Read, mcp__fff__record_access]
```

Rationale: file discovery is typically followed by Read; after reading,
`record_access` feeds frecency back. Other compose hints land as
patterns emerge (KTD2).

## Re-running the seed

If `AGENTS.md` "Command Tool Order" section changes substantially:

1. Re-read AGENTS.md and identify any added/removed verbs
2. Update the matching entries in `hooks/tools/annotations.yaml`
3. Update this SEED_NOTES table to reflect the new chains
4. Run `node mcp-servers/tool-registry/tests/manifest.test.js` to confirm
   the overlay still parses and the prototype-slice tools still resolve
