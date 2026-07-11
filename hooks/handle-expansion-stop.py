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
    """Return only the delivered answer — the trailing run of text-only assistant
    messages at the end of the transcript.

    A multi-tool turn is physically several assistant messages: throwaway tool
    preambles ("Let me check BC-2…") followed by the real answer in the final
    block. Concatenating everything since the last user prompt wrongly counts a
    preamble's bare handle as first-use, and can also scan a stale prefix before
    the final glossary block has flushed to disk. Scanning only the trailing
    answer segment matches the rule's intent (the answer is the "document") and
    is immune to both failure modes."""
    try:
        lines = [json.loads(x) for x in open(transcript_path) if x.strip()]
    except (OSError, ValueError):
        return ""

    chunks = []
    for e in reversed(lines):
        if e.get("type") == "user":
            break
        if e.get("type") == "assistant":
            content = e.get("message", {}).get("content", [])
            if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
                break  # a tool-calling message is preamble narration, not the answer
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    chunks.append(b.get("text", ""))
    return "\n".join(reversed(chunks))


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
