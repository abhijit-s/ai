"""Tests for ``hooks/inject-guidelines.py`` queue integration.

These tests cover the per-session override queue integration only. The
existing prompt-based ``<!-- inject: ... -->`` behaviour is covered
implicitly through the legacy code path (kept for backwards
compatibility with synthetic test inputs).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK = os.path.join(REPO_ROOT, "hooks", "inject-guidelines.py")
GUIDELINES_SRC = os.path.join(REPO_ROOT, "hooks", "guidelines")
GUIDELINES_JSON_SRC = os.path.join(REPO_ROOT, "hooks", "guidelines.json")


def _queue_path(home: str, session_id: str) -> str:
    return os.path.join(
        home, ".claude", "cache", "subagent-overrides", f"{session_id}.jsonl"
    )


def _write_queue(home: str, session_id: str, entries: list[dict]) -> None:
    path = _queue_path(home, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _setup_guideline_files(home: str) -> None:
    """Copy the real guidelines.json + guidelines/ tree into the fake HOME.

    inject-guidelines.py reads from ``~/.claude/hooks/`` (not the repo
    path) so we have to mirror the layout under the fake HOME.
    """
    target_dir = os.path.join(home, ".claude", "hooks")
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy(GUIDELINES_JSON_SRC, os.path.join(target_dir, "guidelines.json"))
    shutil.copytree(
        GUIDELINES_SRC,
        os.path.join(target_dir, "guidelines"),
        dirs_exist_ok=True,
    )


def run_hook(input_obj: dict, fake_home: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = fake_home
    return subprocess.run(
        ["python3", HOOK],
        input=json.dumps(input_obj).encode(),
        env=env,
        capture_output=True,
        timeout=10,
    )


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _setup_guideline_files(str(home))
    yield str(home)


def test_queue_inject_override_wins_over_profile(fake_home):
    _write_queue(
        fake_home,
        "sess-1",
        [
            {
                "ts": _now_iso(),
                "agent_type": "code-explorer",
                "tool_use_id": "tu_a",
                "tools": [],
                "inject": ["vocab-acronyms"],
            }
        ],
    )
    out = run_hook(
        {
            "session_id": "sess-1",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    assert out.returncode == 0
    payload = json.loads(out.stdout)
    body = payload["hookSpecificOutput"]["additionalContext"]
    # The default code-explorer profile carries tool-hierarchy + bash-commands
    # + vocab-acronyms; the override narrows to just vocab-acronyms.
    assert "Vocabulary" in body
    assert "Tool Hierarchy" not in body
    assert "Bash Command" not in body


def test_queue_tools_only_entry_is_ignored_by_guidelines_hook(fake_home):
    _write_queue(
        fake_home,
        "sess-1",
        [
            {
                "ts": _now_iso(),
                "agent_type": "code-explorer",
                "tool_use_id": "tu_a",
                "tools": ["documentation-refiner"],
                "inject": [],
            }
        ],
    )
    out = run_hook(
        {
            "session_id": "sess-1",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    body = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # No override applied → code-explorer profile default emitted.
    assert "Tool Hierarchy" in body
    assert "Bash Command" in body


def test_queue_entry_for_different_agent_type_is_ignored(fake_home):
    _write_queue(
        fake_home,
        "sess-1",
        [
            {
                "ts": _now_iso(),
                "agent_type": "code-reviewer",
                "tool_use_id": "tu_a",
                "tools": [],
                "inject": ["vocab-acronyms"],
            }
        ],
    )
    out = run_hook(
        {
            "session_id": "sess-1",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    body = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # Falls through to code-explorer profile.
    assert "Tool Hierarchy" in body


def test_queue_entry_consumed_after_guidelines_hook_runs(fake_home):
    _write_queue(
        fake_home,
        "sess-1",
        [
            {
                "ts": _now_iso(),
                "agent_type": "code-explorer",
                "tool_use_id": "tu_a",
                "tools": [],
                "inject": ["vocab-acronyms"],
            }
        ],
    )
    out = run_hook(
        {
            "session_id": "sess-1",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    assert out.returncode == 0
    # Entry had only inject; consume_override drains and drops it.
    path = _queue_path(fake_home, "sess-1")
    with open(path) as f:
        remaining = [json.loads(l) for l in f if l.strip()]
    assert remaining == []


def test_queue_missing_falls_back_to_profile(fake_home):
    out = run_hook(
        {
            "session_id": "sess-missing",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    body = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Tool Hierarchy" in body  # default code-explorer profile


def test_legacy_prompt_override_still_works(fake_home):
    """Test compatibility: when no queue entry exists but the prompt
    carries an ``<!-- inject: ... -->`` comment (synthetic test fixtures),
    parsing falls back to the prompt path."""
    out = run_hook(
        {
            "tool_input": {
                "subagent_type": "code-explorer",
                "prompt": "<!-- inject: vocab-acronyms -->",
            },
        },
        fake_home,
    )
    body = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Vocabulary" in body
    assert "Tool Hierarchy" not in body
