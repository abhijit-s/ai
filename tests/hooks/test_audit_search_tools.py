"""Tests for hooks/audit-search-tools.sh.

The bash script's verb-detection regex must stay in lockstep with the
Python BASH_VERB_PATTERNS in hooks/enforce-tool-registry.py so the
audit log and the PreToolUse nudge surface agree on which calls count.
The AE2 block-mode graduation contract relies on that agreement.
"""

import json
import os
import subprocess

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK = os.path.join(REPO_ROOT, "hooks", "audit-search-tools.sh")


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    yield str(home)


def run_audit(command, fake_home, tool_name="Bash"):
    payload = {
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "session_id": "test-session",
        "cwd": "/tmp",
    }
    env = os.environ.copy()
    env["HOME"] = fake_home
    subprocess.run(
        ["bash", HOOK],
        input=json.dumps(payload).encode(),
        env=env,
        capture_output=True,
        timeout=10,
    )
    log_path = os.path.join(fake_home, ".claude", "logs", "search-tool-audit.jsonl")
    if not os.path.exists(log_path):
        return None
    with open(log_path) as f:
        lines = [line for line in f.read().splitlines() if line.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def test_plain_grep_is_logged(fake_home):
    entry = run_audit("grep foo bar", fake_home)
    assert entry is not None
    assert entry["kind"] == "grep"
    assert entry["tier"] == "lo"


def test_piped_grep_is_logged(fake_home):
    entry = run_audit("cat x | grep foo", fake_home)
    assert entry is not None
    assert entry["kind"] == "grep"


def test_subshell_grep_is_logged(fake_home):
    # The lockstep fix — `$(grep ...)` must be detected so the audit
    # log matches what the PreToolUse Python hook nudges on.
    entry = run_audit("echo $(grep foo bar)", fake_home)
    assert entry is not None
    assert entry["kind"] == "grep"


def test_quoted_grep_in_string_is_not_logged(fake_home):
    # False-positive guard: `grep` inside a string literal must not be
    # detected as a verb. Mirrors the Python hook's behavior.
    entry = run_audit('echo "grep is great"', fake_home)
    assert entry is None


def test_subshell_find_is_logged(fake_home):
    entry = run_audit("echo $(find . -name '*.rb')", fake_home)
    assert entry is not None
    assert entry["kind"] == "find"


def test_non_search_command_is_not_logged(fake_home):
    entry = run_audit("echo hello", fake_home)
    assert entry is None


def test_ast_grep_is_logged(fake_home):
    # Without this branch ast-grep calls were invisible to the audit log
    # entirely, so the fff-vs-fallback report could not show the gap.
    entry = run_audit("ast-grep --lang go -p 'func $F($$$) { $$$ }'", fake_home)
    assert entry is not None
    assert entry["kind"] == "ast-grep"
    assert entry["tier"] == "hi"


def test_ast_grep_after_cd_is_logged(fake_home):
    entry = run_audit("cd /tmp/repo && ast-grep -p 'switch { $$$ }' -l go", fake_home)
    assert entry is not None
    assert entry["kind"] == "ast-grep"


def test_ast_grep_is_not_misread_as_bare_grep(fake_home):
    # `-` is outside the boundary class, so the lo-tier grep branch — which
    # runs first — must not claim an ast-grep command.
    entry = run_audit("ast-grep -p 'foo($$$)'", fake_home)
    assert entry["kind"] != "grep"
    assert entry["tier"] != "lo"


def test_mixed_pipeline_is_attributed_to_the_higher_precedence_verb(fake_home):
    # Documented precedence: the first verb in branch order wins, so a mixed
    # command records rg. The record proves the call was search-class, not
    # which tool did the reading.
    entry = run_audit("ast-grep -p 'foo($$$)' -l go | rg -i handler", fake_home)
    assert entry["kind"] == "rg"


def test_heredoc_only_rg_is_not_logged(fake_home):
    # A script being WRITTEN via a heredoc is text, not a search call. Logging
    # it would overstate the fallback side of the headline ratio.
    entry = run_audit("cat > f.py <<'PY'\nrg -n Handler /repo\nPY\necho done", fake_home)
    assert entry is None


def test_real_rg_after_a_heredoc_is_still_logged(fake_home):
    # Stripping must end at the terminator, not swallow the rest of the command.
    entry = run_audit("cat > f.py <<'PY'\nprint(1)\nPY\nrg -n Handler .", fake_home)
    assert entry is not None
    assert entry["kind"] == "rg"
