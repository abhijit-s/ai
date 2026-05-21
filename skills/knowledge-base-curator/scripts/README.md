# kb_curator CLI

Single-file Python CLI for vault inventory, audit, classification, and frontmatter editing. Stdlib only — Python 3.10+.

## Quick reference

```bash
# Inventory
python kb_curator.py scan                       # human summary
python kb_curator.py scan --json                # full inventory as JSON

# Audit drift
python kb_curator.py audit                      # grouped findings
python kb_curator.py audit --json               # machine-readable

# Classify a note
python kb_curator.py classify "<path/to/note.md>"
python kb_curator.py classify "<path>" --json

# Apply / repair frontmatter
python kb_curator.py apply "<path>" --dry-run                       # preview
python kb_curator.py apply "<path>"                                 # normalise in place
python kb_curator.py apply "<path>" --category golang --tags golang,concurrency,channels
python kb_curator.py apply "<path>" --title "Buffered vs Unbuffered Channels"

# Taxonomy
python kb_curator.py taxonomy show              # current canonical shape
python kb_curator.py taxonomy refresh           # dry-run diff against filesystem

# Wiki-links
python kb_curator.py links check                # list broken [[wiki-links]] with suggestions
python kb_curator.py links repair --dry-run     # preview safe (single-candidate) repairs
python kb_curator.py links repair               # apply safe repairs
python kb_curator.py links repair --aggressive  # apply best-guess when multiple candidates

# Naming + safe rename (auto-updates inbound [[links]])
python kb_curator.py naming check               # propose canonical filenames
python kb_curator.py naming rename --path "<file>" --dry-run
python kb_curator.py naming rename --path "<file>"

# Emoji prefix injection (driven by emojis: in config; idempotent)
python kb_curator.py emojis apply --dry-run
python kb_curator.py emojis apply

# Content-based tag suggestions
python kb_curator.py tags suggest "<file>"
python kb_curator.py tags suggest "<file>" --apply

# Theme detection — what cross-cutting topics actually cluster in the vault
python kb_curator.py themes detect
python kb_curator.py themes detect --min-cooccurrence 6
```

## Flags

- `--config <path>` — defaults to `../config/taxonomy.yaml` relative to the script.
- `--root <path>` — override the vault root from the config (useful for testing against a copy).

## Exit codes

- `0` — success, no errors.
- `1` — audit found errors (warnings/info do not trigger non-zero).
- `2` — CLI usage error or missing file/config.

## Behaviour notes

- `apply` normalises existing tags to kebab-case automatically.
- `apply` always re-orders `tags` so the first tag mirrors `category` (when the rule is enabled in config).
- `apply` will not invent a `category` that isn't in `taxonomy.yaml`. If you need a new category, edit the config first (Workflow D in `references/workflows.md`).
- `classify` is a scoring heuristic — bag-of-words against slug tokens and central-question keywords. Treat it as a prior, never a verdict.
- `taxonomy refresh --apply` intentionally does not auto-edit `taxonomy.yaml` — taxonomy changes deserve a human eye and a real git diff. The command prints the additions a human/agent should make.

## YAML dependency

PyYAML is used when present (richer fidelity). When absent, a built-in mini-reader handles the project's schema. The frontmatter writer is hand-rolled in both cases for stable output.
