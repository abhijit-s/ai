#!/usr/bin/env python3
"""kb_curator entrypoint — see `kb_curator/` package for implementation.

Invoke as:
    python3 scripts/kb_curator.py <command> [args]

This shim adds the script directory to `sys.path` so the sibling
`kb_curator/` package is importable, then delegates to `cli.main`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_curator.cli import main  # noqa: E402 (must follow sys.path tweak)

if __name__ == "__main__":
    raise SystemExit(main())
