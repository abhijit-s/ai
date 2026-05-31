"""Unit tests for the YAML reader and the frontmatter writer.

The frontmatter writer is hand-rolled (not yaml.dump) precisely so the
output is stable. These tests pin that stability — break them and you
break diff hygiene across the entire vault.
"""

from kb_curator.yaml_io import yaml_dump_frontmatter, yaml_load


class TestYamlLoad:
    def test_mapping(self):
        out = yaml_load("a: 1\nb: two\n")
        assert out == {"a": 1, "b": "two"}

    def test_nested_mapping(self):
        out = yaml_load("vault:\n  root: /tmp\n  exclude_dirs: [a, b]\n")
        # exclude_dirs flow-style not supported by mini-parser, but block style is.
        assert out["vault"]["root"] == "/tmp"

    def test_sequence_of_mappings(self):
        out = yaml_load("items:\n  - name: a\n  - name: b\n")
        assert out == {"items": [{"name": "a"}, {"name": "b"}]}

    def test_single_quoted_string_preserves_specials(self):
        # Single-quoted regex literals — exactly how taxonomy.yaml stores them.
        out = yaml_load("pat: '^\\d{2}-(.+)$'\n")
        assert out["pat"] == r"^\d{2}-(.+)$"

    def test_bool_and_int_coercion(self):
        # Avoid YAML 1.1 magic words (`on`/`off`) that PyYAML coerces to booleans.
        out = yaml_load("enabled: true\ndisabled: false\nn: 42\n")
        assert out == {"enabled": True, "disabled": False, "n": 42}

    def test_comments_ignored(self):
        out = yaml_load("# comment\nk: v\n# another\n")
        assert out == {"k": "v"}


class TestFrontmatterDump:
    def test_field_order_is_canonical(self):
        # Pass keys in a deliberately scrambled order; output must match the
        # contract: title → placement → provenance → aliases → tags → other.
        data = {
            "tags": ["a"],
            "category": "foo",
            "title": "T",
            "pillar": "p",
            "kind": "deep-dive",
        }
        out = yaml_dump_frontmatter(data)
        lines = out.splitlines()
        # Title comes first; tags last among recognised keys.
        assert lines[0] == "title: T"
        assert lines[1] == "pillar: p"
        assert lines[2] == "category: foo"
        assert lines[3] == "kind: deep-dive"
        assert "tags:" in lines
        assert lines.index("tags:") > lines.index("kind: deep-dive")

    def test_empty_fields_dropped(self):
        out = yaml_dump_frontmatter({"title": "T", "category": "", "tags": []})
        assert "category" not in out
        assert "tags" not in out

    def test_quoted_when_special_chars_present(self):
        out = yaml_dump_frontmatter({"title": "Foo: bar"})
        assert 'title: "Foo: bar"' in out

    def test_extra_keys_appear_last_in_sorted_order(self):
        out = yaml_dump_frontmatter({
            "title": "T", "zzz": "last", "extra": "mid"
        })
        lines = out.splitlines()
        assert lines.index("extra: mid") < lines.index("zzz: last")
