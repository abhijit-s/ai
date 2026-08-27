"""U6: shared Python client for the registry manifest + profiles."""

import json
import os
import tempfile

import pytest

from hooks.lib import tool_registry_client as trc


@pytest.fixture
def sample_manifest():
    return {
        "schema_version": 1,
        "tools": {
            "mcp__fff__grep": {
                "name": "mcp__fff__grep",
                "category": ["search-content"],
                "health": {"state": "healthy"},
            },
            "mcp__fff__find_files": {
                "name": "mcp__fff__find_files",
                "category": ["find-files"],
                "health": {"state": "healthy"},
            },
            "ast-grep": {
                "name": "ast-grep",
                "category": ["search-content", "ast-search"],
                "health": {"state": "healthy"},
            },
            "rg": {
                "name": "rg",
                "category": ["search-content"],
                "health": {"state": "healthy"},
            },
            "mcp__turbo-rag__semantic_search": {
                "name": "mcp__turbo-rag__semantic_search",
                "category": ["semantic-search"],
                "health": {"state": "unhealthy"},
            },
        },
    }


@pytest.fixture
def sample_profiles():
    return {
        "version": 1,
        "profiles": {
            "default": {"tools": [], "categories": ["search-content", "find-files"]},
            "code-explorer": {
                "tools": [],
                "categories": ["search-content", "find-files", "ast-search"],
            },
            "documentation-refiner": {"tools": [], "categories": ["search-content"]},
        },
    }


def test_resolve_profile_unions_explicit_and_category(sample_manifest, sample_profiles):
    allowed = trc.resolve_profile("code-explorer", sample_profiles, sample_manifest)
    assert "mcp__fff__grep" in allowed
    assert "ast-grep" in allowed
    assert "rg" in allowed
    assert "mcp__fff__find_files" in allowed
    # turbo-rag is not in any of code-explorer's categories
    assert "mcp__turbo-rag__semantic_search" not in allowed


def test_resolve_profile_unknown_returns_empty(sample_manifest, sample_profiles):
    assert trc.resolve_profile("phantom", sample_profiles, sample_manifest) == set()


def test_resolve_profile_referencing_category_with_no_tools_returns_empty(
    sample_profiles,
):
    empty_manifest = {"schema_version": 1, "tools": {}}
    assert trc.resolve_profile("code-explorer", sample_profiles, empty_manifest) == set()


def test_parse_override_single_profile_name():
    tokens = trc.parse_override("foo <!-- tools: documentation-refiner --> bar")
    assert tokens == ["documentation-refiner"]


def test_parse_override_comma_list():
    tokens = trc.parse_override("<!-- tools: mcp__fff__grep, find-files -->")
    assert tokens == ["mcp__fff__grep", "find-files"]


def test_parse_override_multiline():
    tokens = trc.parse_override(
        "<!-- tools:\n  mcp__fff__grep,\n  find-files\n-->"
    )
    assert tokens == ["mcp__fff__grep", "find-files"]


def test_parse_override_first_one_wins():
    tokens = trc.parse_override(
        "<!-- tools: code-explorer --> ...other... <!-- tools: documentation-refiner -->"
    )
    assert tokens == ["code-explorer"]


def test_parse_override_no_match_returns_none():
    assert trc.parse_override("plain prompt") is None
    assert trc.parse_override("") is None
    assert trc.parse_override(None) is None


def test_parse_override_coexists_with_inject_prefix():
    # The inject: prefix is parsed by inject-guidelines.py — must not collide.
    tokens = trc.parse_override(
        "<!-- inject: tool-hierarchy --> <!-- tools: code-explorer -->"
    )
    assert tokens == ["code-explorer"]


def test_resolve_override_single_profile_token(sample_manifest, sample_profiles):
    allowed = trc.resolve_override(["documentation-refiner"], sample_profiles, sample_manifest)
    assert "mcp__fff__grep" in allowed
    assert "ast-grep" in allowed
    # documentation-refiner is search-content only
    assert "mcp__fff__find_files" not in allowed


def test_resolve_override_mixed_tool_and_category(sample_manifest, sample_profiles):
    allowed = trc.resolve_override(
        ["mcp__fff__grep", "find-files"], sample_profiles, sample_manifest
    )
    assert allowed == {"mcp__fff__grep", "mcp__fff__find_files"}


def test_load_manifest_missing_returns_empty(tmp_path):
    m = trc.load_manifest(str(tmp_path / "nope.json"))
    assert m["tools"] == {}
    assert m["schema_version"] == 1


def test_load_manifest_corrupt_returns_empty(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text("{ not json")
    m = trc.load_manifest(str(p))
    assert m["tools"] == {}


def test_load_manifest_schema_mismatch_returns_empty(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema_version": 999, "tools": {"ghost": {}}}))
    m = trc.load_manifest(str(p))
    assert m["tools"] == {}


def test_load_profiles_default_path_reads_repo_profiles():
    # Sanity: the real hooks/profiles.json parses and contains expected keys.
    profiles = trc.load_profiles()
    assert "default" in profiles["profiles"]
    assert "session-default" in profiles["profiles"]
    assert "code-explorer" in profiles["profiles"]


def test_resolve_profile_session_default_includes_semantic_search_when_healthy(
    sample_manifest,
):
    profiles = {
        "version": 1,
        "profiles": {
            "session-default": {
                "tools": [],
                "categories": [
                    "search-content",
                    "find-files",
                    "git-state",
                    "list-dir",
                    "ast-search",
                    "semantic-search",
                ],
            }
        },
    }
    # Make turbo-rag healthy for this assertion
    sample_manifest["tools"]["mcp__turbo-rag__semantic_search"]["health"]["state"] = "healthy"
    allowed = trc.resolve_profile("session-default", profiles, sample_manifest)
    assert "mcp__turbo-rag__semantic_search" in allowed


# --- command-path-aware code-repo detection ---------------------------------
#
# cwd alone could not see a code-repo call: sessions launch from the umbrella
# vault by convention and reach into the repos by absolute path or `cd`.

CODE_ROOTS = ["/Users/x/workspace/surge/app", "/Users/x/workspace/surge/surge-bot"]


def test_command_target_paths_finds_cd_and_argument_paths():
    paths = trc.command_target_paths(
        "cd /Users/x/workspace/surge/app && rg -n 'Handler' services/bff /tmp/other"
    )
    assert "/Users/x/workspace/surge/app" in paths
    assert "/tmp/other" in paths


def test_command_target_paths_ignores_relative_and_flag_tokens():
    paths = trc.command_target_paths("rg -n --glob '!*.md' Handler services/bff")
    assert paths == []


def test_command_target_paths_of_empty_command_is_empty():
    assert trc.command_target_paths("") == []


def test_cd_into_code_repo_is_detected_from_an_unrelated_cwd():
    assert trc.command_touches_code_repo(
        "cd /Users/x/workspace/surge/app && rg foo", "/Users/x/vaults/workspace", CODE_ROOTS
    )


def test_absolute_path_argument_into_code_repo_is_detected():
    assert trc.command_touches_code_repo(
        "rg -n Handler /Users/x/workspace/surge/app/services/bff",
        "/Users/x/vaults/workspace",
        CODE_ROOTS,
    )


def test_tilde_path_argument_into_code_repo_is_detected(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert trc.command_touches_code_repo(
        "rg -n Handler ~/clone/sub", "/elsewhere", ["~/clone"]
    )


def test_cwd_under_code_repo_still_wins_with_no_path_argument():
    assert trc.command_touches_code_repo(
        "rg foo", "/Users/x/workspace/surge/app/services", CODE_ROOTS
    )


def test_command_outside_every_code_repo_is_not_detected():
    assert not trc.command_touches_code_repo(
        "rg foo /Users/x/vaults/workspace/Memory", "/Users/x/vaults/workspace", CODE_ROOTS
    )


def test_code_repo_prefix_is_matched_on_path_boundary():
    # `.../surge/app-scratch` must not count as `.../surge/app`.
    assert not trc.command_touches_code_repo(
        "rg foo /Users/x/workspace/surge/app-scratch", "/Users/x/vaults/workspace", CODE_ROOTS
    )


# --- heredoc bodies are data, not commands ----------------------------------


def test_strip_heredocs_removes_body_and_terminator():
    stripped = trc.strip_heredocs("cat > f.py <<'PY'\nrg -n Handler /repo\nPY\necho done")
    assert "rg" not in stripped
    assert "/repo" not in stripped
    assert "echo done" in stripped


def test_strip_heredocs_keeps_the_rest_of_the_introducer_line():
    stripped = trc.strip_heredocs("cat <<EOF > /tmp/out\nbody\nEOF")
    assert "/tmp/out" in stripped
    assert "body" not in stripped


def test_strip_heredocs_handles_dash_and_quoted_delimiters():
    for intro in ("<<EOF", "<<-EOF", "<<'EOF'", '<<"EOF"'):
        stripped = trc.strip_heredocs(f"cat {intro}\nrg secret /repo\nEOF\nls")
        assert "rg" not in stripped, intro
        assert "ls" in stripped, intro


def test_strip_heredocs_is_idempotent():
    once = trc.strip_heredocs("cat <<'PY'\nrg x /repo\nPY\nuv run pytest")
    assert trc.strip_heredocs(once) == once


def test_strip_heredocs_unterminated_body_runs_to_the_end():
    stripped = trc.strip_heredocs("cat <<'PY'\nrg x /repo")
    assert "rg" not in stripped


def test_strip_heredocs_leaves_a_plain_command_untouched():
    assert trc.strip_heredocs("rg -n Handler /repo") == "rg -n Handler /repo"


def test_heredoc_path_does_not_count_as_a_code_repo_target():
    command = "cat > note.md <<'MD'\nsee /Users/x/workspace/surge/app/main.go\nMD"
    assert not trc.command_touches_code_repo(
        trc.strip_heredocs(command), "/elsewhere", CODE_ROOTS
    )
