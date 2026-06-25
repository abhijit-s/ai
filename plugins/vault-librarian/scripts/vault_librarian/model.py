"""Domain model: Pillar, Area, Taxonomy + config loader.

The Taxonomy dataclass is the in-memory mirror of `taxonomy.yaml`. All
command modules accept a Taxonomy instance — nothing reaches into the raw
YAML directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .yaml_io import yaml_load


@dataclass
class Area:
    slug: str
    path: str
    central_question: str = ""
    pillar_slug: str = ""
    pillar_slug_path: str = ""

    @property
    def full_path(self) -> str:
        return f"{self.pillar_slug_path}/{self.path}"


@dataclass
class Pillar:
    slug: str
    path: str
    central_question: str = ""
    areas: list[Area] = field(default_factory=list)


@dataclass
class Taxonomy:
    """In-memory mirror of taxonomy.yaml.

    Every command receives a Taxonomy and reads policy from it — no code
    should read raw YAML or hardcode policy values. When you add a config
    field, add it here, populate it in `load_taxonomy`, and document the
    default at the call site that uses it.
    """

    vault_root: Path
    exclude_dirs: set[str]
    required_fm: list[str]
    optional_fm: list[str]
    rules: dict
    pillars: list[Pillar]
    controlled_tags: set[str]
    naming: dict = field(default_factory=dict)
    emojis: dict = field(default_factory=dict)
    inference_rules: list = field(default_factory=list)
    path_conventions: dict = field(default_factory=dict)
    link_syntax: str = "obsidian"
    slug_case: str = "kebab"
    dates_source: str = "git"

    def categories(self) -> dict[str, Area]:
        return {a.slug: a for p in self.pillars for a in p.areas}

    def area_for_path(self, rel_path: Path) -> Area | None:
        parts = rel_path.parts
        if len(parts) < 2:
            return None
        pillar_dir, area_dir = parts[0], parts[1]
        for p in self.pillars:
            if p.path != pillar_dir:
                continue
            for a in p.areas:
                if a.path == area_dir:
                    return a
        return None


def load_taxonomy(config_path: Path) -> Taxonomy:
    raw = yaml_load(config_path.read_text())
    vault = raw.get("vault") or {}
    fm = raw.get("frontmatter") or {}
    pillars: list[Pillar] = []
    for p in raw.get("pillars") or []:
        pillar = Pillar(
            slug=p["slug"],
            path=p["path"],
            central_question=p.get("central_question", ""),
        )
        for a in p.get("areas") or []:
            pillar.areas.append(Area(
                slug=a["slug"],
                path=a["path"],
                central_question=a.get("central_question", ""),
                pillar_slug=pillar.slug,
                pillar_slug_path=pillar.path,
            ))
        pillars.append(pillar)
    return Taxonomy(
        vault_root=Path(vault.get("root", "")).expanduser(),
        exclude_dirs=set(vault.get("exclude_dirs") or []),
        required_fm=list(fm.get("required") or ["title", "category", "tags"]),
        optional_fm=list(fm.get("optional") or []),
        rules=fm.get("rules") or {},
        pillars=pillars,
        controlled_tags=set(raw.get("tags") or []),
        naming=raw.get("naming") or {},
        emojis=raw.get("emojis") or {},
        inference_rules=raw.get("inference_rules") or [],
        path_conventions=raw.get("path_conventions") or {},
        link_syntax=(raw.get("link_syntax") or {}).get("type", "obsidian"),
        slug_case=raw.get("slug_case", "kebab"),
        dates_source=(raw.get("dates") or {}).get("source", "git"),
    )
