#!/usr/bin/env python3
"""vault_librarian entrypoint — see `vault_librarian/` package for implementation.

Invoke as:
    python3 scripts/vault_librarian.py <command> [args]

This shim adds the script directory to `sys.path` so the sibling
`vault_librarian/` package is importable, then delegates to `cli.main`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_librarian.cli import main  # noqa: E402 (must follow sys.path tweak)

if __name__ == "__main__":
    raise SystemExit(main())
