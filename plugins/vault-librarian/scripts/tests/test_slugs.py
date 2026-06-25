"""Unit tests for the slug helpers."""

from vault_librarian.slugs import is_slug, slugify


class TestSlugifyKebab:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_underscores_and_slashes_collapse(self):
        assert slugify("foo_bar/baz") == "foo-bar-baz"

    def test_drops_punctuation(self):
        assert slugify("Hello, World!") == "hello-world"

    def test_handles_unicode_drop(self):
        assert slugify("Café Noir") == "caf-noir"  # diacritics removed

    def test_empty(self):
        assert slugify("") == ""
        assert slugify(None) == ""


class TestSlugifySnake:
    def test_basic(self):
        assert slugify("Hello World", "snake") == "hello_world"

    def test_mixed(self):
        assert slugify("foo-bar baz", "snake") == "foo_bar_baz"


class TestSlugifyCamel:
    def test_basic(self):
        assert slugify("hello world", "camel") == "helloWorld"

    def test_preserves_acronyms_as_lowercase(self):
        # The slug is normalised to camelCase; acronyms become lowercase first.
        assert slugify("API Gateway", "camel") == "apiGateway"

    def test_single_word(self):
        assert slugify("Foo", "camel") == "foo"


class TestIsSlug:
    def test_kebab_valid(self):
        assert is_slug("foo-bar", "kebab")
        assert is_slug("a1b2-c3", "kebab")

    def test_kebab_invalid(self):
        assert not is_slug("Foo-Bar", "kebab")     # upper-case
        assert not is_slug("foo_bar", "kebab")     # underscore
        assert not is_slug("-foo", "kebab")        # leading hyphen
        assert not is_slug("", "kebab")

    def test_snake_valid(self):
        assert is_slug("foo_bar", "snake")

    def test_camel_valid(self):
        assert is_slug("fooBar", "camel")
        assert not is_slug("FooBar", "camel")      # PascalCase rejected
