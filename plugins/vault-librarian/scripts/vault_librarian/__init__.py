"""vault_librarian — curate Markdown knowledge vaults.

Public surface lives in submodules:

  yaml_io      YAML loader (PyYAML or scoped fallback) and frontmatter writer.
  slugs        slugify / is_slug helpers for kebab|snake|camel.
  model        Pillar / Area / Taxonomy dataclasses and config loader.
  frontmatter  Note dataclass, frontmatter parsing, vault walking.
  links        Wiki-link / Markdown-link parsing, broken-link detection.
  derivation   Classification, naming, kind/placement/date derivation.
  commands     One `cmd_<verb>` function per CLI subcommand.
  cli          argparse wiring and entrypoint.

The CLI is the only stable interface; importing internals is fine for
ad-hoc scripting, but signatures may shift across versions.
"""

__version__ = "0.2.0"
