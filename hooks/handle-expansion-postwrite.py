#!/usr/bin/env python3
"""
PostToolUse hook (Write|Edit|MultiEdit) — the Vocabulary rule on the VAULT-WRITE
surface. After a Markdown file under the vault is written/edited, scan it; if it
uses taxonomy handles bare on first use, inject a reminder (non-blocking) naming
each handle with its authoritative slug so the next step can add a glossary or
inline expansion. Reuses the shared detector in handle_lint.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handle_lint  # noqa: E402

VAULT = os.path.expanduser("~/vaults/workspace")


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)

    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(".md") or not os.path.abspath(path).startswith(VAULT):
        sys.exit(0)
    if not os.path.exists(path) or handle_lint.in_skip(path):
        sys.exit(0)

    findings = handle_lint.scan_text(open(path, errors="replace").read())
    if not findings:
        sys.exit(0)

    seen, entries = set(), []
    for f in findings:
        if f["handle"] in seen:
            continue
        seen.add(f["handle"])
        entries.append(f"{f['handle']} ({f['suggestion']})" if f["suggestion"]
                       else f"{f['handle']} (plan-local — expand from context)")

    rel = os.path.relpath(path, VAULT)
    msg = (
        f"handle-lint: {rel} uses taxonomy handles bare on first use — "
        + ", ".join(sorted(seen)) + ". Per the Vocabulary rule, edit the file to "
        "expand each on first use (inline slug) or add a one-line glossary at the end. "
        "Suggested: *Glossary — " + "; ".join(entries) + ".*"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
