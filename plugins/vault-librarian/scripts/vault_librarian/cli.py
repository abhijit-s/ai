"""Argparse wiring + dispatch.

The CLI surface is the only stable API. Each subcommand maps to a handler
in `commands.py`; this module is just plumbing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import commands
from .model import load_taxonomy


DEFAULT_CONFIG = (Path(__file__).resolve().parent.parent.parent
                  / "config" / "taxonomy.yaml")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vault_librarian",
        description="Curate a Markdown knowledge vault — see README.md.",
    )
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                   help="Path to taxonomy.yaml")
    p.add_argument("--root", type=Path,
                   help="Override vault root from config")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scan", help="Inventory the vault")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("audit", help="Find taxonomy / frontmatter drift")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("classify", help="Suggest classification for a markdown file")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("apply", help="Write/repair frontmatter for a file")
    sp.add_argument("path")
    sp.add_argument("--category")
    sp.add_argument("--tags", help="Comma-separated list")
    sp.add_argument("--title")
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("taxonomy", help="Inspect, refresh, or initialise the taxonomy")
    sp.add_argument("taxonomy_action", choices=["show", "refresh", "init"])
    sp.add_argument("--apply", action="store_true", help="(refresh) Write changes")
    sp.add_argument("--root", help="(init) Vault root to scan; defaults to config")
    sp.add_argument("--out", help="(init) Output file; default writes to stdout")
    sp.add_argument("--pillar-pattern",
                    help="(init) Override pillar regex for discovery")

    sp = sub.add_parser("links", help="Check or repair wiki-links")
    sp.add_argument("action", choices=["check", "repair"])
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--aggressive", action="store_true",
                    help="Apply repairs even when multiple suggestions exist (uses closest match).")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("naming", help="Check or apply canonical filenames")
    sp.add_argument("action", choices=["check", "rename"])
    sp.add_argument("--path", help="File to rename (required for 'rename')")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("emojis", help="Inject emoji prefixes into titles per config")
    sp.add_argument("action", choices=["apply"])
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("tags", help="Suggest tags for a file using inference rules")
    sp.add_argument("action", choices=["suggest"])
    sp.add_argument("path")
    sp.add_argument("--apply", action="store_true",
                    help="Write the suggested tags into frontmatter")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("themes", help="Detect tag co-occurrence clusters")
    sp.add_argument("action", choices=["detect"])
    sp.add_argument("--min-cooccurrence", type=int, default=4,
                    help="Pair must co-occur in at least N notes to enter a cluster.")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("enrich",
                        help="Backfill derived fields (pillar, sub_area, topic, kind, created, updated)")
    sp.add_argument("--path", help="Limit to one file (default: whole vault)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--force", action="store_true",
                    help="Overwrite existing derived fields")
    sp.add_argument("--no-dates", action="store_true",
                    help="Skip git-derived created/updated dates")

    return p


DISPATCH = {
    "scan": commands.cmd_scan,
    "audit": commands.cmd_audit,
    "classify": commands.cmd_classify,
    "apply": commands.cmd_apply,
    "taxonomy": commands.cmd_taxonomy,
    "links": commands.cmd_links,
    "naming": commands.cmd_naming,
    "emojis": commands.cmd_emojis,
    "tags": commands.cmd_tags,
    "themes": commands.cmd_themes,
    "enrich": commands.cmd_enrich,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.config.exists():
        print(f"error: config not found: {args.config}", file=sys.stderr)
        return 2
    tax = load_taxonomy(args.config)
    if args.root:
        tax.vault_root = Path(args.root).expanduser().resolve()
    if not tax.vault_root.exists():
        print(f"error: vault root does not exist: {tax.vault_root}", file=sys.stderr)
        return 2
    return DISPATCH[args.command](args, tax)
