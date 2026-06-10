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
