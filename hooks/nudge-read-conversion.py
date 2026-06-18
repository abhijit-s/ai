#!/usr/bin/env python3
"""
PreToolUse hook (Read): convert-first nudge for binary document formats.

The convert-first counterpart to enforce-tool-registry.py. When Read targets a
format its native loader CANNOT open (Office binaries, Outlook .msg, EPUB),
emit a soft nudge pointing at mcp__markitdown__convert_to_markdown so the model
converts first instead of bouncing off a binary-read failure.

Scope is deliberately narrow. PDF, images, and .ipynb are EXCLUDED — Read opens
those natively and is often the right call (visual layout, scanned pages), so a
nudge there would fight the user's judgment rather than help it.

Style mirrors the registry nudge: always permissionDecision=allow (a bug makes
noise, never blocks), fail-open on any error.
"""

from __future__ import annotations

import json
import os
import sys

# Formats Read cannot open at all — conversion is mandatory, so the nudge is
# unambiguously correct. PDF/images/ipynb/csv/html/json are intentionally absent.
CONVERT_FIRST_EXTS = {
    ".docx", ".doc",
    ".xlsx", ".xls",
    ".pptx", ".ppt",
    ".msg",
    ".epub",
}


def emit(reminder: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": reminder,
        }
    }
    print(json.dumps(payload))
    sys.exit(0)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if (input_data.get("tool_name") or "") != "Read":
        sys.exit(0)

    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in CONVERT_FIRST_EXTS:
        sys.exit(0)

    emit(
        f"TOOL GUIDELINE NUDGE (convert-first): Read can't open {ext} files. "
        f"Convert first with mcp__markitdown__convert_to_markdown, then read the "
        f"Markdown. Load via ToolSearch (select:mcp__markitdown__convert_to_markdown)."
    )


if __name__ == "__main__":
    main()
