#!/usr/bin/env python3
"""
Stop / SubagentStop hook — backstop for the Vocabulary rule on the CHAT surface.

When a turn finishes, scan the assistant's prose for project taxonomy handles
used bare on first use. If any, block ONCE (loop-guarded via stop_hook_active)
and hand back the exact glossary to append — the sanctioned end-glossary form.
A response that already expands inline or carries a glossary passes silently.

Reuses the shared detector in handle_lint.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handle_lint  # noqa: E402


def turn_prose(transcript_path):
    """Concatenate the current turn's assistant text — every text block emitted
    after the last genuine user prompt (tool-result 'user' entries don't count)."""
    try:
        lines = [json.loads(x) for x in open(transcript_path) if x.strip()]
    except (OSError, ValueError):
        return ""

    def is_user_prompt(e):
        if e.get("type") != "user":
            return False
        c = e.get("message", {}).get("content")
        if isinstance(c, str):
            return True
        if isinstance(c, list):
            return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
        return False

    last_user = max((i for i, e in enumerate(lines) if is_user_prompt(e)), default=-1)
    chunks = []
    for e in lines[last_user + 1:]:
        if e.get("type") == "assistant":
            for b in e.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "text":
                    chunks.append(b.get("text", ""))
    return "\n".join(chunks)


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)

    if payload.get("stop_hook_active"):   # already nudged this turn — never loop
        sys.exit(0)

    text = turn_prose(payload.get("transcript_path", ""))
    if not text.strip():
        sys.exit(0)

    findings = handle_lint.scan_text(text)
    if not findings:
        sys.exit(0)

    # De-dup by handle, build a ready-to-paste glossary.
    seen, entries = set(), []
    for f in findings:
        if f["handle"] in seen:
            continue
        seen.add(f["handle"])
        if f["suggestion"]:
            entries.append(f"{f['handle']} ({f['suggestion']})")
        else:
            entries.append(f"{f['handle']} (plan-local — expand from context)")

    reason = (
        "Vocabulary rule: your response uses project taxonomy handles bare on first "
        "use — " + ", ".join(sorted(seen)) + ". Append a one-line glossary at the end "
        "(or expand each inline). Suggested glossary:\n\n"
        "*Glossary — " + "; ".join(entries) + ".*\n\n"
        "Fix plan-local handles from your own context; the parenthetical titles for "
        "ADR/BC/CD are the authoritative slugs."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


if __name__ == "__main__":
    main()
