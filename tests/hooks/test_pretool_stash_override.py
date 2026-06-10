"""Tests for ``hooks/pretool-stash-override.py`` and the per-session override queue.

The hook is responsible for parsing override comments from the spawn
prompt of a ``PreToolUse`` event where ``tool_name == "Agent"`` and
stashing them in a per-session FIFO (First-In-First-Out) queue file at
``~/.claude/cache/subagent-overrides/<session_id>.jsonl``.

These tests cover the contract end-to-end (hook invocation via
subprocess) plus the queue library's atomicity, TTL (Time To Live)
cleanup, and malformed-file handling.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK = os.path.join(REPO_ROOT, "hooks", "pretool-stash-override.py")


def _queue_path(home: str, session_id: str) -> str:
    return os.path.join(
        home, ".claude", "cache", "subagent-overrides", f"{session_id}.jsonl"
    )


def _read_entries(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


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
    yield str(home)


# ---------------------------------------------------------------------------
# Hook contract tests
# ---------------------------------------------------------------------------


def test_non_agent_tool_writes_nothing(fake_home):
    out = run_hook(
        {
            "session_id": "sess-1",
            "tool_use_id": "toolu_01abc",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        },
        fake_home,
    )
    assert out.returncode == 0
    assert not os.path.exists(_queue_path(fake_home, "sess-1"))


def test_agent_with_only_tools_override(fake_home):
    out = run_hook(
        {
            "session_id": "sess-1",
            "tool_use_id": "toolu_01abc",
            "tool_name": "Agent",
            "tool_input": {
                "description": "explore",
                "subagent_type": "code-explorer",
                "prompt": "Please explore <!-- tools: documentation-refiner --> thanks",
            },
        },
        fake_home,
    )
    assert out.returncode == 0
    entries = _read_entries(_queue_path(fake_home, "sess-1"))
    assert len(entries) == 1
    e = entries[0]
    assert e["agent_type"] == "code-explorer"
    assert e["tool_use_id"] == "toolu_01abc"
    assert e["tools"] == ["documentation-refiner"]
    assert e["inject"] == []
    assert e["ts"]  # ISO-8601 timestamp present


def test_agent_with_only_inject_override(fake_home):
    out = run_hook(
        {
            "session_id": "sess-1",
            "tool_use_id": "toolu_01abc",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "code-reviewer",
                "prompt": "<!-- inject: vocab-acronyms,bash-commands -->",
            },
        },
        fake_home,
    )
    assert out.returncode == 0
    entries = _read_entries(_queue_path(fake_home, "sess-1"))
    assert len(entries) == 1
    assert entries[0]["tools"] == []
    assert entries[0]["inject"] == ["vocab-acronyms", "bash-commands"]


def test_agent_with_both_overrides(fake_home):
    out = run_hook(
        {
            "session_id": "sess-1",
            "tool_use_id": "toolu_01abc",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "code-explorer",
                "prompt": (
                    "<!-- tools: mcp__fff__grep,find-files -->\n"
                    "<!-- inject: vocab-acronyms -->"
                ),
            },
        },
        fake_home,
    )
    assert out.returncode == 0
    entries = _read_entries(_queue_path(fake_home, "sess-1"))
    assert len(entries) == 1
    assert entries[0]["tools"] == ["mcp__fff__grep", "find-files"]
    assert entries[0]["inject"] == ["vocab-acronyms"]


def test_agent_with_neither_override_writes_nothing(fake_home):
    out = run_hook(
        {
            "session_id": "sess-1",
            "tool_use_id": "toolu_01abc",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "code-explorer",
                "prompt": "Plain spawn with no override comments.",
            },
        },
        fake_home,
    )
    assert out.returncode == 0
    assert not os.path.exists(_queue_path(fake_home, "sess-1"))


def test_missing_session_id_exits_silently(fake_home):
    out = run_hook(
        {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "code-explorer",
                "prompt": "<!-- tools: documentation-refiner -->",
            },
        },
        fake_home,
    )
    assert out.returncode == 0
    overrides_dir = os.path.join(fake_home, ".claude", "cache", "subagent-overrides")
    # Either the directory was never created or it is empty.
    assert not os.path.exists(overrides_dir) or os.listdir(overrides_dir) == []


def test_invalid_json_input_exits_zero(fake_home):
    proc = subprocess.run(
        ["python3", HOOK],
        input=b"not json",
        env={**os.environ, "HOME": fake_home},
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# Queue library tests (TTL, malformed file, atomic write)
# ---------------------------------------------------------------------------


def test_stale_entries_dropped_on_next_write(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    # Importing late so the HOME env var is honoured.
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    path = _queue_path(fake_home, "sess-1")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Hand-craft a stale entry (40 minutes ago).
    stale_ts = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 40 * 60)
    )
    with open(path, "w") as f:
        f.write(
            json.dumps(
                {
                    "ts": stale_ts,
                    "agent_type": "code-explorer",
                    "tool_use_id": "stale",
                    "tools": ["foo"],
                    "inject": [],
                }
            )
            + "\n"
        )

    oq.append_override(
        "sess-1", "code-reviewer", "toolu_01new", ["bar"], []
    )

    entries = _read_entries(path)
    assert len(entries) == 1
    assert entries[0]["agent_type"] == "code-reviewer"
    assert entries[0]["tool_use_id"] == "toolu_01new"


def test_malformed_existing_file_treated_as_empty(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    path = _queue_path(fake_home, "sess-1")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("not json at all\n{also broken\n")

    # Should not raise — malformed lines are silently skipped.
    oq.append_override("sess-1", "code-explorer", "toolu_01x", ["foo"], [])

    entries = _read_entries(path)
    assert len(entries) == 1
    assert entries[0]["tools"] == ["foo"]


def test_consume_override_returns_and_removes_entry(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    # Two entries: only the second one matches our (agent_type, field).
    oq.append_override("sess-1", "code-reviewer", "tu_a", ["foo"], [])
    oq.append_override("sess-1", "code-explorer", "tu_b", ["bar"], [])

    result = oq.consume_override("sess-1", "code-explorer", "tools")
    assert result == ["bar"]
    # Entry was removed (only tools field, now empty → drop).
    entries = _read_entries(_queue_path(fake_home, "sess-1"))
    assert len(entries) == 1
    assert entries[0]["tool_use_id"] == "tu_a"


def test_consume_override_keeps_entry_when_other_field_still_set(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    oq.append_override("sess-1", "code-explorer", "tu_x", ["foo"], ["vocab-acronyms"])
    result = oq.consume_override("sess-1", "code-explorer", "tools")
    assert result == ["foo"]
    entries = _read_entries(_queue_path(fake_home, "sess-1"))
    assert len(entries) == 1
    # tools cleared, inject preserved
    assert entries[0]["tools"] == []
    assert entries[0]["inject"] == ["vocab-acronyms"]

    # Second consume drains the other field — entry should now be removed.
    result2 = oq.consume_override("sess-1", "code-explorer", "inject")
    assert result2 == ["vocab-acronyms"]
    assert _read_entries(_queue_path(fake_home, "sess-1")) == []


def test_consume_override_no_match_returns_none(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    oq.append_override("sess-1", "code-explorer", "tu_x", ["foo"], [])

    # Wrong agent_type
    assert oq.consume_override("sess-1", "code-reviewer", "tools") is None
    # Right agent_type, wrong field (inject is empty)
    assert oq.consume_override("sess-1", "code-explorer", "inject") is None
    # Original entry still present.
    assert len(_read_entries(_queue_path(fake_home, "sess-1"))) == 1


def test_consume_override_missing_file_returns_none(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    assert oq.consume_override("sess-never-existed", "code-explorer", "tools") is None


def test_consume_override_malformed_file_returns_none(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    path = _queue_path(fake_home, "sess-1")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("garbage\n")

    # All lines malformed → no usable entries → returns None.
    assert oq.consume_override("sess-1", "code-explorer", "tools") is None


def test_atomic_write_no_partial_files_left_behind(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    oq.append_override("sess-1", "code-explorer", "tu_a", ["foo"], [])
    oq.append_override("sess-1", "code-explorer", "tu_b", ["bar"], [])

    overrides_dir = os.path.join(
        fake_home, ".claude", "cache", "subagent-overrides"
    )
    files = os.listdir(overrides_dir)
    # Exactly one canonical file; no .tmp-* leftover.
    assert files == ["sess-1.jsonl"]


def test_fifo_order_preserved(fake_home, monkeypatch):
    monkeypatch.setenv("HOME", fake_home)
    import importlib
    import hooks.lib.override_queue as oq

    importlib.reload(oq)

    oq.append_override("sess-1", "code-explorer", "tu_a", ["first"], [])
    oq.append_override("sess-1", "code-explorer", "tu_b", ["second"], [])

    # Earliest matching entry is consumed first.
    assert oq.consume_override("sess-1", "code-explorer", "tools") == ["first"]
    assert oq.consume_override("sess-1", "code-explorer", "tools") == ["second"]
    assert oq.consume_override("sess-1", "code-explorer", "tools") is None
