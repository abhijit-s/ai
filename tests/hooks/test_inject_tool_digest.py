"""U8: SubagentStart hook tests."""

import json
import os
import subprocess
import sys
import tempfile

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK = os.path.join(REPO_ROOT, "hooks", "inject-tool-digest.py")


SEED_MANIFEST = {
    "schema_version": 1,
    "generated_at": "2026-06-10T14:00:00Z",
    "last_success": "2026-06-10T14:00:00Z",
    "tools": {
        "mcp__fff__grep": {
            "name": "mcp__fff__grep",
            "description": "Search file contents (frecency-ranked)",
            "category": ["search-content"],
            "capability_tags": ["content-search", "frecency-ranked"],
            "prefer_over": {"search-content": ["ast-grep", "rg", "grep"]},
            "health": {"state": "healthy"},
        },
        "mcp__fff__find_files": {
            "name": "mcp__fff__find_files",
            "description": "Discover files (frecency)",
            "category": ["find-files"],
            "capability_tags": [],
            "prefer_over": {"find-files": ["fd", "find"]},
            "health": {"state": "healthy"},
        },
        "ast-grep": {
            "name": "ast-grep",
            "description": "Structural code search",
            "category": ["search-content", "ast-search"],
            "capability_tags": [],
            "prefer_over": {"search-content": ["rg", "grep"]},
            "health": {"state": "healthy"},
        },
        "rg": {
            "name": "rg",
            "description": "ripgrep",
            "category": ["search-content"],
            "capability_tags": [],
            "prefer_over": {"search-content": ["grep"]},
            "health": {"state": "healthy"},
        },
        "mcp__turbo-rag__semantic_search": {
            "name": "mcp__turbo-rag__semantic_search",
            "description": "Semantic",
            "category": ["semantic-search"],
            "capability_tags": [],
            "prefer_over": {},
            "health": {"state": "unhealthy"},
        },
    },
    "discovery_errors": [],
}


def run_hook(input_obj: dict, fake_home: str | None = None):
    env = os.environ.copy()
    if fake_home:
        env["HOME"] = fake_home
        # XDG_CONFIG_HOME is set on real machines and would otherwise point the
        # fff root resolver at the developer's own config, leaking an unrelated
        # base_path hint into the digest under test.
        env["XDG_CONFIG_HOME"] = os.path.join(fake_home, ".config")
    proc = subprocess.run(
        ["python3", HOOK],
        input=json.dumps(input_obj).encode(),
        env=env,
        capture_output=True,
        timeout=10,
    )
    return proc


def write_manifest(home_dir: str, manifest: dict):
    cache_dir = os.path.join(home_dir, ".claude", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "tool-registry-manifest.json"), "w") as f:
        json.dump(manifest, f)


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    yield str(home)


def test_code_explorer_profile_includes_expected_tools(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    out = run_hook({"tool_input": {"subagent_type": "code-explorer", "prompt": ""}}, fake_home)
    assert out.returncode == 0, out.stderr.decode()
    payload = json.loads(out.stdout)
    digest = payload["hookSpecificOutput"]["additionalContext"]
    assert "mcp__fff__grep" in digest
    assert "ast-grep" in digest
    assert "rg" in digest
    # turbo-rag is unhealthy → must not appear
    assert "mcp__turbo-rag__semantic_search" not in digest


def test_override_overrides_default_profile(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    out = run_hook(
        {
            "tool_input": {
                "subagent_type": "code-explorer",
                "prompt": "<!-- tools: documentation-refiner -->",
            }
        },
        fake_home,
    )
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # documentation-refiner is search-content only → no find-files entries
    assert "mcp__fff__find_files" not in digest
    assert "override" in digest.lower()


def test_override_mixed_tool_and_category(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    out = run_hook(
        {
            "tool_input": {
                "subagent_type": "code-explorer",
                "prompt": "<!-- tools: mcp__fff__grep,find-files -->",
            }
        },
        fake_home,
    )
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "mcp__fff__grep" in digest
    assert "mcp__fff__find_files" in digest
    assert "ast-grep" not in digest  # not in the override set


def test_unhealthy_tool_absent_from_digest(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    # session-default has semantic-search; turbo-rag is unhealthy → must be absent.
    out = run_hook(
        {"tool_input": {"subagent_type": "session-default", "prompt": ""}}, fake_home
    )
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "mcp__turbo-rag__semantic_search" not in digest


def test_missing_manifest_exits_silently(fake_home):
    # No manifest written.
    out = run_hook(
        {"tool_input": {"subagent_type": "code-explorer", "prompt": ""}}, fake_home
    )
    assert out.returncode == 0
    assert out.stdout.decode().strip() == ""


def test_unknown_subagent_type_falls_back_to_default(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    out = run_hook(
        {"tool_input": {"subagent_type": "pr-comment-reviewer", "prompt": ""}}, fake_home
    )
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # default profile is search-content + find-files + git-state + list-dir
    assert "mcp__fff__grep" in digest
    assert "mcp__fff__find_files" in digest


def test_no_stdin_input_exits_zero():
    proc = subprocess.run(
        ["python3", HOOK], input=b"not json", capture_output=True, timeout=10
    )
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# Queue-driven override tests (live SubagentStart event shape)
# ---------------------------------------------------------------------------


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
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


def test_queue_tools_override_wins_over_profile(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
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
    assert out.returncode == 0
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # documentation-refiner profile is search-content only.
    assert "mcp__fff__find_files" not in digest
    assert "override: documentation-refiner" in digest


def test_queue_inject_only_entry_is_ignored_by_digest_hook(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
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
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # No override applied — falls through to code-explorer profile default.
    assert "override:" not in digest
    assert "code-explorer" in digest


def test_queue_entry_for_different_agent_type_is_ignored(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    _write_queue(
        fake_home,
        "sess-1",
        [
            {
                "ts": _now_iso(),
                "agent_type": "code-reviewer",
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
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    # Override targeted code-reviewer, not us → fall through to our profile.
    assert "override:" not in digest


def test_queue_entry_consumed_after_digest_hook_runs(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
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
    assert out.returncode == 0
    # The tools field on the matching entry should be cleared OR the entry
    # dropped entirely (inject was empty here → dropped).
    path = _queue_path(fake_home, "sess-1")
    with open(path) as f:
        remaining = [json.loads(l) for l in f if l.strip()]
    assert remaining == []


def test_queue_missing_falls_back_to_profile(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    # No queue file written.
    out = run_hook(
        {
            "session_id": "sess-missing",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "code-explorer" in digest
    assert "override:" not in digest


def test_queue_malformed_silently_falls_back(fake_home):
    write_manifest(fake_home, SEED_MANIFEST)
    path = _queue_path(fake_home, "sess-mal")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("not valid jsonl at all\n")
    out = run_hook(
        {
            "session_id": "sess-mal",
            "agent_type": "code-explorer",
            "agent_id": "deadbeef",
        },
        fake_home,
    )
    assert out.returncode == 0
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "override:" not in digest


def test_queue_consumed_entry_with_empty_tools_does_not_override(fake_home):
    """An entry whose tools field is already empty (other hook drained) must not steer."""
    write_manifest(fake_home, SEED_MANIFEST)
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
    digest = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "override:" not in digest
    # Entry preserved for the other hook to consume.
    path = _queue_path(fake_home, "sess-1")
    with open(path) as f:
        remaining = [json.loads(l) for l in f if l.strip()]
    assert len(remaining) == 1
    assert remaining[0]["inject"] == ["vocab-acronyms"]
