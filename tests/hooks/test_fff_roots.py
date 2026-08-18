"""fff root resolution shared by the SessionStart and SubagentStart hooks."""

import json
import os
import subprocess

import pytest

from hooks.lib import fff_roots


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB = os.path.join(REPO_ROOT, "hooks", "lib", "fff_roots.py")
PRELOAD_HOOK = os.path.join(REPO_ROOT, "hooks", "preload-search-tools.sh")

CONFIG = """
[mcp]
default = "personal"

[[mcp.roots]]
name = "personal"
path = "{root}/vault/personal"

[[mcp.roots]]
name = "canon"
path = "{root}/vault/canon"

[[mcp.roots]]
name = "umbrella"
path = "{root}/vault"
"""


@pytest.fixture
def fff_config(tmp_path, monkeypatch):
    """Point the resolver at a throwaway fff config; yield the roots' parent."""
    root = tmp_path / "tree"
    for sub in ("vault/personal", "vault/canon", "elsewhere"):
        (root / sub).mkdir(parents=True)

    config_dir = tmp_path / "config" / "fff"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(CONFIG.format(root=root))

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    yield root


@pytest.fixture
def no_fff_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))


def test_listing_puts_default_first_and_marks_it(fff_config):
    lines = fff_roots.roots_listing().splitlines()
    assert lines[0].strip().startswith("@personal")
    assert "(default)" in lines[0]
    assert [l for l in lines if "(default)" in l] == [lines[0]]
    assert {"@canon", "@umbrella"} <= {l.split()[0] for l in lines}


def test_listing_shows_the_path_to_pass_as_base_path(fff_config):
    assert str(fff_config / "vault" / "canon") in fff_roots.roots_listing()


def test_most_specific_root_wins_over_umbrella(fff_config):
    match = fff_roots.resolve_root(
        str(fff_config / "vault" / "canon" / "deep" / "note.md"),
        fff_roots.load_roots()[1],
    )
    assert match["name"] == "canon"


def test_hint_names_the_base_path_for_a_non_default_root(fff_config):
    hint = fff_roots.root_hint(str(fff_config / "vault" / "canon"))
    assert "`canon`" in hint
    assert f"base_path={fff_config / 'vault' / 'canon'}" in hint


def test_hint_is_silent_when_cwd_sits_in_the_default_root(fff_config):
    assert fff_roots.root_hint(str(fff_config / "vault" / "personal")) is None


def test_hint_steers_to_the_lexical_ladder_outside_every_root(fff_config):
    hint = fff_roots.root_hint(str(fff_config / "elsewhere"))
    assert "no root covering this working tree" in hint
    assert "ast-grep" in hint


def test_worktree_cwd_gets_the_direct_base_path_note(fff_config):
    worktree = fff_config / "vault" / "canon" / ".claude" / "worktrees" / "agent-1"
    worktree.mkdir(parents=True)
    assert "you're in a worktree" in fff_roots.root_hint(str(worktree))


def test_missing_config_yields_no_roots_and_no_hint(no_fff_config):
    assert fff_roots.load_roots() == (None, [])
    assert fff_roots.roots_listing() is None
    assert fff_roots.root_hint("/anywhere") is None


def test_unparseable_config_is_swallowed(tmp_path, monkeypatch):
    config_dir = tmp_path / "config" / "fff"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("this is not = valid = toml [[[")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert fff_roots.load_roots() == (None, [])


def run_lib_cli(cwd: str, xdg: str):
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = xdg
    return subprocess.run(
        ["python3", LIB, "--cwd", cwd], env=env, capture_output=True, timeout=10
    )


def test_cli_prints_listing_and_hint(fff_config):
    xdg = os.environ["XDG_CONFIG_HOME"]
    out = run_lib_cli(str(fff_config / "vault" / "canon"), xdg)
    assert out.returncode == 0
    block = out.stdout.decode()
    assert "Indexed fff roots" in block
    assert "@personal" in block
    assert "`canon`" in block


def test_cli_signals_failure_when_config_is_missing(tmp_path):
    out = run_lib_cli(str(tmp_path), str(tmp_path / "empty"))
    assert out.returncode == 1
    assert out.stdout.decode().strip() == ""


def run_preload_hook(payload: dict, xdg: str):
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = xdg
    return subprocess.run(
        [PRELOAD_HOOK],
        input=json.dumps(payload).encode(),
        env=env,
        capture_output=True,
        timeout=15,
    )


def test_session_start_context_carries_roots_and_list_roots_tool(fff_config):
    xdg = os.environ["XDG_CONFIG_HOME"]
    out = run_preload_hook(
        {"source": "startup", "cwd": str(fff_config / "vault" / "canon")}, xdg
    )
    assert out.returncode == 0
    context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "mcp__fff__list_roots" in context
    assert "@personal" in context
    assert f"base_path={fff_config / 'vault' / 'canon'}" in context


def test_session_start_falls_back_when_fff_config_is_unreadable(tmp_path):
    out = run_preload_hook(
        {"source": "startup", "cwd": str(tmp_path)}, str(tmp_path / "empty")
    )
    context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "fff config unreadable" in context


def test_subagent_sessions_are_skipped(fff_config):
    out = run_preload_hook({"source": "subagent"}, os.environ["XDG_CONFIG_HOME"])
    assert out.returncode == 0
    assert out.stdout.decode().strip() == ""
