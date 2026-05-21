"""Slug normalisation and validation.

Three slug styles supported: kebab (`like-this`), snake (`like_this`),
camel (`likeThis`). The active style is read from `slug_case` in the
taxonomy config.
"""

from __future__ import annotations

import re


def slugify(value: str, style: str = "kebab") -> str:
    """Normalise to a slug in the chosen style."""
    if value is None:
        return ""
    s = str(value).strip()
    if style == "camel":
        parts = re.split(r"[\s_\-/]+", s)
        parts = [re.sub(r"[^A-Za-z0-9]+", "", p) for p in parts if p]
        if not parts:
            return ""
        return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
    sep = "-" if style == "kebab" else "_"
    s = s.lower()
    s = re.sub(r"[\s_\-/]+", sep, s)
    s = re.sub(r"[^a-z0-9" + re.escape(sep) + r"]+", "", s)
    s = re.sub(re.escape(sep) + r"+", sep, s).strip(sep)
    return s


def is_slug(value: str, style: str = "kebab") -> bool:
    if not value:
        return False
    if style == "kebab":
        return bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value))
    if style == "snake":
        return bool(re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", value))
    if style == "camel":
        return bool(re.fullmatch(r"[a-z][a-zA-Z0-9]*", value))
    return False


# Back-compat aliases — prefer `slugify`/`is_slug` in new code.
def kebab(value: str) -> str:
    return slugify(value, "kebab")


def is_kebab(value: str) -> bool:
    return is_slug(value, "kebab")
