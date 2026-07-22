#!/usr/bin/env python3
"""
SessionStart hook — main-thread parity for the Vocabulary rule.

Sub-agents get the vocab-acronyms guideline injected pre-emptively via
SubagentStart (inject-guidelines.py), once per spawn. The main thread's
matching lifecycle boundary is SessionStart, not every turn: CLAUDE.md
already states this rule once at true session start, but that text can be
summarized away by context compaction on long sessions. SessionStart fires
again with matcher "compact" right after compaction completes, so hooking
here (rather than UserPromptSubmit) re-asserts the rule exactly when it's at
risk of being lost, without repeating it on every prompt.
"""
import json
import os
import sys

HOOKS_DIR = os.path.expanduser("~/.claude/hooks")
CONFIG_FILE = os.path.join(HOOKS_DIR, "guidelines.json")
SLUG = "vocab-acronyms"


def main():
    try:
        json.load(sys.stdin)  # payload unused; keeps the hook well-formed
    except Exception:
        pass

    guideline_path = os.path.join(HOOKS_DIR, "guidelines", f"{SLUG}.txt")
    if not os.path.exists(guideline_path):
        sys.exit(0)
    with open(guideline_path) as f:
        content = f.read().strip()
    if not content:
        sys.exit(0)

    title = SLUG
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        title = config.get("slugs", {}).get(SLUG, {}).get("title", SLUG)

    output = f"## User Guidelines\n\n### {title}\n\n{content}"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": output,
        }
    }))


if __name__ == "__main__":
    main()
