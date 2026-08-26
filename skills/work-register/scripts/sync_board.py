#!/usr/bin/env python3
"""Sync per-day work-register files into Obsidian Kanban boards.

Day files are the append-only source of intent; the board owns live status. The sync is
purely additive — a card whose id is already on a board is never moved or rewritten — so
dragging cards between columns survives every subsequent run.

One capture stream, one board per scope. Day files interleave personal and work items as
they occur; the render partitions them, so the default board can be kept open with no
personal work on it. Every card lands on exactly one board — see the note above
`scope_boards` for why that is a correctness property rather than a convention.

Configuration follows the memory-kit philosophy (ADR-020 / ADR-079):

  * Every key has a code default, so the tool runs with zero settings.
  * Absolute, machine-specific declarations live OUT of the vault, in the per-machine
    base config at ~/.config/work-register/config.toml. Nothing user-specific is
    committed to this skill.
  * A corpus governs its own conventions: `.work-register.toml` at a register root is
    both the discovery marker (found by walking up from cwd) and the authority for that
    register's columns, lanes, vocabulary and card shape. It layers OVER the base.
  * Grammar is logic, not config: the day-file item syntax and id format stay in code.

Verbs, each one-directional (see the field-ownership table below):

    sync_board.py                # add cards the board has not seen; never moves one
    sync_board.py --reconcile    # board status (column + checkbox) → day files
    sync_board.py --move ID=COLUMN   # relocate a card, then reconcile the day file
    sync_board.py --probe        # resolve cards' own references; PROPOSES, never moves
    sync_board.py --status       # register health: last capture, stale lanes
    sync_board.py --refresh      # re-render card text from day files; KEEPS placement
    sync_board.py --rebuild      # re-place all from day files; DISCARDS drags
    sync_board.py --migrate      # cards whose scope now names another board; REPORTS only
    sync_board.py --migrate --apply  # …and move them, each keeping its column
    sync_board.py --archive      # trim Done to a recency window; day files untouched
    sync_board.py --archive --before YYYY-MM-DD | --keep N [--include-anchored]
    sync_board.py --init PATH    # stand a register up in a vault that has never had one
    sync_board.py --dry-run --show-config --since YYYY-MM-DD --register NAME --config PATH

Read surface — the two verbs that return cards rather than acting on them. Every verb
above either mutates or renders a verdict, so a session that only wants to know what is
on its plate had no choice but to read the whole board:

    sync_board.py --list [--track NAME] [--scope NAME] [--column NAME] [--open] [--json]
    sync_board.py --show ID      # the day-file section behind one card

Both are strictly read-only: no id is minted, no board is written, no ledger is touched.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
MARKER_NAME = ".work-register.toml"
BASE_CONFIG = Path.home() / ".config" / "work-register" / "config.toml"
LEDGER_NAME = ".sync-state.json"

# Field ownership — the whole sync contract in one table. No field has two owners, so
# there is never a conflict to resolve:
#
#   existence · text · grouping · track  →  day file owns  (board never invents a card)
#   status: column · checkbox            →  board owns     (--reconcile stamps it back)
#
# `track` sits on the day-file side with text and grouping, which is why it needs no
# reconcile path: a declaration the board cannot change is a declaration that cannot drift.
# `scope` adds no fifth row to this table: it is DERIVED from the track, so it inherits
# that ownership instead of becoming a field two surfaces could disagree about. What it
# DOES decide is which board renders the card — so the day file picks the file, the board
# picks the column within it, and the two never contend.
#
# The ledger records every id ever placed, which is what lets a DELETED card stay deleted
# instead of being resurrected on the next sync.

# --- Grammar: logic, not config ---------------------------------------------------
DAY_FILE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
HEADING = re.compile(r"^##\s+(.*?)\s*$")
ITEM = re.compile(r"^(\s*)-\s*\[([ xX])\]\s*(.*)$")
CONTINUATION = re.compile(r"^\s+\S")
FRONTMATTER = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)
SETTINGS_BLOCK = re.compile(r"\n*%%\s*kanban:settings.*\Z", re.DOTALL)
ORDINAL = re.compile(r"^[0-9️⃣\s]+")
# `::name` declares the memory-kit track a card belongs to. The token must stand alone:
# the leading guard is what keeps it out of a URL — `https://…` and `http://[::1]/` both
# carry a non-space character immediately before the colons, so neither matches. A token
# that breaks the shape (uppercase, leading hyphen, over-length) simply is not one.
TRACK_TOKEN = re.compile(r"(?<!\S)::([a-z0-9][a-z0-9-]{0,63})(?!\S)")

DEFAULTS: dict = {
    "schema_version": SCHEMA_VERSION,
    "default_register": None,
    "register": {},
    "ids": {"prefix": "wr", "template": "{date}-{seq:02d}"},
    "board": {
        "column_order": [
            "▶️ Next",
            "⏳ In progress",
            "\U0001f534 Blocked",
            "\U0001f3c1 Done",
        ],
        "default_column": "▶️ Next",
        "done_column": "\U0001f3c1 Done",
        "lanes": [],
        "frontmatter": {"kanban-plugin": "board"},
    },
    "tag_rules": [],
    "track_rules": [],
    # Scope — what tells personal work from work work on ONE board. It is a property of a
    # TRACK, not of a card: personal versus work describes a thread of work rather than an
    # individual item, so it is declared once per track and every card inherits the track
    # it resolves to.
    #
    # `default` is both the fallback and the scope suppression keys on, and that is one
    # meaning rather than two: it is the scope a track is in unless it says otherwise.
    # Left empty the whole dimension is off, so a register that never names a scope
    # renders exactly as it did before there was one to name.
    "scope": {
        "default": "",
        # Whether a card in the default scope carries NO tag. On, only the exception is
        # marked and the common case stays quiet; off, every scope is tagged. Which scope
        # is default and whether suppression applies at all are vocabulary; collapsing the
        # tag once suppressed is grammar, and that half stays in code.
        "suppress_default": True,
        # track name -> scope, for a track no `[[track_rules]]` entry names. A card may
        # declare `::house-move` outright with no rule behind it, and that track still
        # needs a scope.
        "track": {},
        # scope name -> the board that renders it, relative to the register root. The
        # DEFAULT scope's board is the `board` binding every register already has, so a
        # register naming no second scope here renders exactly one file, exactly as it
        # always did. Declaring a scope here creates nothing until a card lands in it.
        "board": {},
    },
    "card": {
        "template": "- [{check}] {marker}{icons}{text}\n\t\n\t{meta}\n\t{id_comment}",
        # The date link is aliased, and that is not cosmetic. A BARE [[YYYY-MM-DD]] is
        # exactly how the Kanban plugin serialises a card's own date field, so the plugin
        # claims the link and sends a click to a daily note instead of to the day file the
        # card came from. The alias is what stops it matching. The link stays relative, so
        # the default carries no assumption about where in the vault the register sits — a
        # corpus that needs path-qualification (basenames not unique across its vault) adds
        # it in its own contract.
        "meta": "\U0001f4c5 [[{date}|{date}]] · \U0001f9ed {group}{track}{tags}",
        "icon_separator": "",
        "icon_suffix": " ",
        "max_icons": 3,
        # How wide a card's text reads in a `--list` line before it is elided. A display
        # tunable, so it sits with the other card-shape knobs rather than in the engine.
        "list_text_width": 72,
        # Collapsing to nothing when there is no track is grammar; the emoji and the
        # separator that lead the segment are vocabulary, so both live in one format
        # string a corpus can restyle without touching code.
        "track_format": " · \U0001f9f5 {track}",
        "track_tag": "#track/{track}",
        # Scope reaches the card as a tag and nothing else. The Kanban plugin's search is
        # what has to match it, and a meta segment would spend a line of the card face
        # saying out loud what suppression exists to leave unsaid.
        "scope_tag": "#scope/{scope}",
    },
    "log": {
        "added": "➕",
        "stamped": "\U0001f516",
        "done": "\U0001f3c1",
        "dry_run": "\U0001f9ea",
        "board": "\U0001f5c2️",
        "config": "⚙️",
        "register": "\U0001f4d3",
        "empty": "\U0001f4ad",
        "reconciled": "\U0001f501",
        "rebuild": "♻️",
        "deleted": "\U0001f5d1️",
        "moved": "\U0001f446",
        "refreshed": "\U0001f504",
        "probe": "\U0001f50e",
        "proposal": "\U0001f4ec",
        "init": "\U0001f331",
        "archived": "\U0001f4e6",
        "ok": "✅",
        "warn": "⚠️",
        "unresolved": "❔",
        # The track marker for a `--list` line. It has a code default like every other
        # key here, so a corpus that restyles its card face can restyle the listing to
        # match without the engine carrying a literal emoji in a format string.
        "track": "\U0001f9f5",
    },
    "probe": {
        # repo shorthand -> owner/repo, so a card may cite `app#733` not the full path.
        "repos": {},
        # frontmatter fields on a canon doc that answer "is this finished?", and the
        # values that count as finished.
        "readiness_fields": ["artifact_readiness", "status"],
        "terminal_readiness": ["implemented", "shipped", "complete", "superseded", "withdrawn"],
        # column a card is proposed for when its reference resolves terminal.
        "propose_column": None,  # defaults to done_column
        # columns whose cards are not probed (already finished / deliberately idle).
        "skip_columns": [],
    },
    # The Done column is a recency WINDOW, not a record — the day files are the record.
    # How wide that window is is vocabulary (it tracks how fast a register moves), so it has
    # a code default and a corpus may say otherwise. Whether an anchored card is protected
    # is grammar, not vocabulary, so it stays a deliberate flag rather than a settable key.
    "archive": {"keep_days": 14},
    "status": {
        "stale_days": 3,
        "capture_gap_days": 2,
        # lanes where sitting a long time is meaningful (Done/Parked are not).
        "watch_columns": [],
    },
    "kanban": {"settings": '{"kanban-plugin":"board","show-checkboxes":true}'},
    "paths": {"register_dir": "Register", "board": "WORK-REGISTER.md"},
}


@dataclass
class Item:
    item_id: str
    date: str
    group: str
    marker: str
    text: str
    done: bool
    tags: list[str] = field(default_factory=list)
    icons: list[str] = field(default_factory=list)
    track: str = ""
    # Derived from `track`, never read off the line: a card with no track has no scope.
    scope: str = ""


# --- Configuration resolution -----------------------------------------------------
def read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def deep_merge(base: dict, overlay: dict) -> dict:
    """Overlay wins. Dicts merge recursively; lists and scalars replace outright."""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def find_marker(start: Path) -> Path | None:
    """Walk up from `start` looking for a register-root marker."""
    for directory in [start, *start.parents]:
        candidate = directory / MARKER_NAME
        if candidate.is_file():
            return candidate
    return None


def check_schema(path: Path, layer: dict) -> None:
    version = layer.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise SystemExit(
            f"work-register: {path} declares schema_version {version}, "
            f"engine speaks {SCHEMA_VERSION}"
        )


class Layered:
    """Ordered config layers: code defaults ← machine base ← corpus marker ← override.

    A register's conventions live at its own root, which the base config may only be
    naming for the first time — so resolution is two-phase: bind paths, then let the
    corpus at `data_root` layer its contract on top (ADR-079). Launching from the
    umbrella (the normal case) means the marker is a child of cwd, not an ancestor, so
    walking up alone would never find it.
    """

    def __init__(self) -> None:
        self.cfg = copy.deepcopy(DEFAULTS)
        self.sources: list[Path] = []

    def apply(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in {source.resolve() for source in self.sources}:
            return
        layer = read_toml(resolved)
        check_schema(resolved, layer)
        self.cfg = deep_merge(self.cfg, layer)
        self.sources.append(resolved)


def load_config(explicit: str | None) -> Layered:
    """Phase 1 — everything resolvable before a register is chosen."""
    layers = Layered()

    if BASE_CONFIG.is_file():
        layers.apply(BASE_CONFIG)

    marker = find_marker(Path.cwd().resolve())
    if marker:
        layers.apply(marker)

    override = explicit or os.environ.get("WORK_REGISTER_CONFIG")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise SystemExit(f"work-register: config not found: {path}")
        layers.apply(path)

    return layers


def resolve_register(cfg: dict, name: str | None) -> tuple[str, dict]:
    registers: dict = cfg.get("register") or {}
    if not registers:
        raise SystemExit(
            "work-register: no register declared. Add a [register.<name>] table to "
            f"{BASE_CONFIG} (paths, per machine) or to a {MARKER_NAME} at the register root."
        )

    chosen = name or os.environ.get("WORK_REGISTER") or cfg.get("default_register")
    if not chosen:
        if len(registers) == 1:
            chosen = next(iter(registers))
        else:
            raise SystemExit(
                "work-register: several registers declared and no default. "
                f"Pass --register <{'|'.join(sorted(registers))}>."
            )
    if chosen not in registers:
        raise SystemExit(
            f"work-register: unknown register {chosen!r}; declared: {', '.join(sorted(registers))}"
        )
    return chosen, registers[chosen]


def resolve_all(explicit_config: str | None, wanted: str | None) -> tuple[Layered, str, dict, dict]:
    """Run both resolution phases and return (layers, name, register, cfg).

    Phase 1 can only NAME a register; its conventions live at the root that name points to,
    so the layer stack is not final until the register is known. Extracted because `--init`
    has to prove its own work by resolving the register it just wrote, and a second copy of
    this sequence would be free to drift from the one every other verb uses.
    """
    layers = load_config(explicit_config)
    name, register = resolve_register(layers.cfg, wanted)

    root = Path(os.environ.get("WORK_REGISTER_ROOT", register.get("data_root", "."))).expanduser()
    corpus_marker = root / MARKER_NAME
    if corpus_marker.is_file():
        layers.apply(corpus_marker)
        name, register = resolve_register(layers.cfg, wanted or name)

    return layers, name, register, deep_merge(layers.cfg, register.get("overrides", {}))


def register_root(register: dict) -> Path:
    data_root = register.get("data_root")
    if not data_root:
        raise SystemExit("work-register: register declares no data_root")
    return Path(os.environ.get("WORK_REGISTER_ROOT", data_root)).expanduser()


def register_paths(cfg: dict, register: dict) -> tuple[Path, Path]:
    """(day-file directory, the DEFAULT scope's board).

    The one-board answer, which is what `--init` proves itself with and what a register
    declaring no second scope has anyway. Everything that renders wants `scope_boards`.
    """
    root = register_root(register)
    register_dir = register.get("register_dir", cfg["paths"]["register_dir"])
    board = register.get("board", cfg["paths"]["board"])
    return root / register_dir, root / board


# --- Boards: one capture stream, one board per scope --------------------------------
#
# Day files stay one stream — personal and work items interleave there as they actually
# occur, because that is how a day happens. The RENDER is what separates them, so the
# default board can be left open all day without personal work on it. A tag cannot do this
# job: the Kanban plugin offers a transient search box and no saved filters, so "everything
# except personal" is not expressible, and the board file holds every card regardless.
#
# DISJOINTNESS is the property the whole design rests on, not a detail. The board owns a
# card's column (see the ownership table at the top of this file); a card on two boards
# would have two owners for that one field, and they would diverge the instant either copy
# was dragged — precisely the failure this split exists to prevent. Three things make a
# second placement unreachable rather than merely unlikely:
#
#   1. `scope_boards` is a FUNCTION, scope -> exactly one path. A scope cannot name two
#      boards because a TOML table cannot hold one key twice; two scopes cannot name one
#      board because that collision is refused at resolution, below.
#   2. `board_for` is TOTAL. A scope the map does not name — including the empty scope of a
#      trackless card — falls to the default board, so nothing can fall off every board and
#      become invisible.
#   3. Every placement site appends to `board_for(...)` exactly once. The partition is built
#      by construction, not checked afterwards.
#
# "Unreachable" is a claim about this code, though, and a board is a file the owner can also
# edit. So `parse_boards` still REPORTS an id found on two boards rather than silently
# keeping one of them.


def scope_boards(cfg: dict, register: dict) -> tuple[str, dict[str, Path]]:
    """(default scope, scope -> board path). The default scope is always present.

    The mapping is VOCABULARY — it names scopes, and scopes are declared in the corpus
    contract — so it lives there rather than in the per-machine binding. Only `data_root` is
    genuinely machine-specific; a board path relative to the register root is vault layout,
    which the corpus is the authority on. The default scope keeps using the register's
    existing `board` key, which is what makes a one-scope register render the file it
    always did, under the name it always had.
    """
    root = register_root(register)
    scope_cfg = cfg.get("scope") or {}
    default_scope = scope_cfg.get("default", "")
    boards: dict[str, Path] = {
        default_scope: root / register.get("board", cfg["paths"]["board"])
    }
    for scope, relative in (scope_cfg.get("board") or {}).items():
        if scope == default_scope:
            raise SystemExit(
                f"work-register: [scope] board.{scope} names the default scope, whose board "
                "is already the register's own `board` binding. One file with two "
                "declarations is the ambiguity this refuses — drop one."
            )
        path = root / relative
        clash = next((s for s, p in boards.items() if p == path), None)
        if clash is not None:
            raise SystemExit(
                f"work-register: [scope] board.{scope} and scope {clash!r} both render to "
                f"{path}. Two scopes sharing a file cannot be told apart again, so a later "
                "config change could not know which cards to move — give each its own file."
            )
        boards[scope] = path
    return default_scope, boards


def board_for(boards: dict[str, Path], default_scope: str, scope: str) -> Path:
    """The one board a scope renders to. Total and single-valued — see the note above."""
    return boards.get(scope) or boards[default_scope]


def board_order(boards: dict[str, Path]) -> list[Path]:
    """Distinct board paths: the default first, then declaration order."""
    return list(dict.fromkeys(boards.values()))


def scope_of(boards: dict[str, Path], path: Path) -> str:
    """Which scope a board renders. Well defined because the paths are proved distinct."""
    return next((scope for scope, candidate in boards.items() if candidate == path), "")


# --- Day-file parsing -------------------------------------------------------------
def id_comment(prefix: str, item_id: str) -> str:
    return f"<!-- {prefix}:{item_id} -->"


def id_pattern(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"<!--\s*{re.escape(prefix)}:([A-Za-z0-9_.-]+)\s*-->")


def split_marker(cfg: dict, text: str) -> tuple[str, str]:
    """Peel a recognised lane marker off the front of an item's text."""
    for lane in cfg["board"].get("lanes", []):
        marker = lane["marker"]
        if text.startswith(marker):
            return marker, text[len(marker) :].lstrip()
    return "", text


def lane_for(cfg: dict, marker: str, done: bool) -> str:
    if done:
        return cfg["board"]["done_column"]
    for lane in cfg["board"].get("lanes", []):
        if marker == lane["marker"]:
            return lane["column"]
    return cfg["board"]["default_column"]


def classify(cfg: dict, haystack: str) -> tuple[list[str], list[str]]:
    """Apply the corpus vocabulary: text → (tags, icons). Order follows the rule order."""
    tags: list[str] = []
    icons: list[str] = []
    for rule in cfg.get("tag_rules", []):
        if not re.search(rule["pattern"], haystack, re.IGNORECASE):
            continue
        tag = rule.get("tag")
        if tag and tag not in tags:
            tags.append(tag)
        icon = rule.get("icon")
        if icon and icon not in icons:
            icons.append(icon)
    return tags, icons[: cfg["card"]["max_icons"]]


def split_track(text: str) -> tuple[str, str]:
    """Peel a `::name` declaration off a line: (track, text without the token).

    The token is a declaration, not prose, so it never reaches the card face. Collapsing
    the whitespace afterwards is what keeps a mid-sentence token from leaving a gap.
    """
    found = TRACK_TOKEN.search(text)
    if not found:
        return "", text
    remainder = TRACK_TOKEN.sub("", text, count=1)
    return found.group(1), re.sub(r"\s{2,}", " ", remainder).strip()


def infer_track(cfg: dict, haystack: str) -> str:
    """Guess a track from the corpus vocabulary. FIRST match wins, unlike tag_rules.

    A card carries several tags because it touches several concerns, but it belongs to one
    track — so the rules are an ordered decision, not an accumulation.
    """
    for rule in cfg.get("track_rules", []):
        if re.search(rule["pattern"], haystack, re.IGNORECASE):
            return rule["track"]
    return ""


def scope_for(cfg: dict, track: str) -> str:
    """The scope a track belongs to. Resolution is by track NAME, and that is the point.

    A scope keyed on the RULE that matched would put a card declaring `::house-move`
    outright — with no rule behind it — in a different scope from one the same track was
    inferred onto. So a rule only contributes its own track's scope to a name-keyed
    lookup, and `[scope] track.<name>` states one directly for a track no rule names. The
    direct statement wins, which is what lets a corpus correct a rule's scope without
    touching the rule.

    A track declaring neither falls to `[scope] default`. A card with NO track gets no
    scope at all: scope describes a thread of work, and here there is no thread to
    describe.
    """
    if not track:
        return ""
    scope = cfg.get("scope") or {}
    declared = (scope.get("track") or {}).get(track)
    if declared:
        return declared
    for rule in cfg.get("track_rules", []):
        if rule.get("track") == track and rule.get("scope"):
            return rule["scope"]
    return scope.get("default", "")


def parse_day_file(cfg: dict, path: Path, date: str, mutate: bool = True) -> tuple[list[Item], bool]:
    """Parse one day file, minting ids for un-stamped items. Returns (items, rewritten).

    With mutate=False the ids are still minted in memory so a dry run reports the real
    placement, but the day file is left untouched.
    """
    prefix = cfg["ids"]["prefix"]
    template = cfg["ids"]["template"]
    finder = id_pattern(prefix)

    lines = path.read_text(encoding="utf-8").splitlines()
    seq = 0
    for line in lines:
        found = finder.search(line)
        if found:
            tail = found.group(1).rsplit("-", 1)[-1]
            if tail.isdigit():
                seq = max(seq, int(tail))

    items: list[Item] = []
    group = ""
    section_track = ""
    rewritten = False
    index = 0

    while index < len(lines):
        line = lines[index]
        heading = HEADING.match(line)
        if heading:
            raw = heading.group(1).strip()
            # Peel the token before the ordinal, so a heading may carry either or both.
            section_track, raw = split_track(raw)
            group = ORDINAL.sub("", raw).strip() or raw
            index += 1
            continue

        match = ITEM.match(line)
        if not match:
            index += 1
            continue

        _, check, body = match.groups()
        last = index
        probe = index + 1
        # Absorb wrapped continuation lines so a hard-wrapped item stays one card.
        while probe < len(lines) and CONTINUATION.match(lines[probe]) and not ITEM.match(lines[probe]):
            body += " " + lines[probe].strip()
            last = probe
            probe += 1

        found = finder.search(body)
        if found:
            item_id = found.group(1)
        else:
            seq += 1
            item_id = template.format(date=date.replace("-", ""), seq=seq)
            lines[last] = lines[last].rstrip() + " " + id_comment(prefix, item_id)
            rewritten = True

        text = re.sub(r"\s{2,}", " ", finder.sub("", body).strip())
        marker, text = split_marker(cfg, text)
        item_track, text = split_track(text)
        tags, icons = classify(cfg, f"{group} {text}")
        # An explicit declaration always beats a guess, and the item's own beats the one
        # it inherits from its section — the same item-over-section binding --probe uses.
        track = item_track or section_track or infer_track(cfg, f"{group} {text}")
        items.append(
            Item(
                item_id=item_id,
                date=date,
                group=group,
                marker=marker,
                text=text,
                done=check.lower() == "x",
                tags=tags,
                icons=icons,
                track=track,
                scope=scope_for(cfg, track),
            )
        )
        index = probe

    if rewritten and mutate:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return items, rewritten


# --- Board read / render ----------------------------------------------------------
def render_card(cfg: dict, item: Item) -> str:
    card = cfg["card"]
    # The track is a meta segment PLUS a tag: the segment reads on the card face, the tag
    # is what the Kanban plugin can filter and group by. It claims no icon slot, so
    # declaring a track never costs a card one of its vocabulary icons.
    track = card.get("track_format", "").format(track=item.track) if item.track else ""
    track_tag = card.get("track_tag", "").format(track=item.track) if item.track else ""
    # Scope is tagged only where saying it earns the space. Suppressing the default scope
    # is what keeps a board whose tracks are all one scope reading exactly as it did
    # before the dimension existed — the exception stands out because the rule stays
    # silent. Which scope is default, and whether to suppress at all, is the corpus's
    # call; collapsing the tag once suppressed is grammar and lives here.
    scope_cfg = cfg.get("scope") or {}
    suppressed = scope_cfg.get("suppress_default", True) and item.scope == scope_cfg.get(
        "default", ""
    )
    scope_tag = (
        card.get("scope_tag", "").format(scope=item.scope)
        if item.scope and not suppressed
        else ""
    )
    all_tags = (
        item.tags
        + ([track_tag] if track_tag else [])
        + ([scope_tag] if scope_tag else [])
    )
    tags = (" " + " ".join(all_tags)) if all_tags else ""
    meta = card["meta"].format(
        date=item.date, group=item.group or item.date, track=track, tags=tags
    )
    # The column already encodes the lane, and a marker on the card face goes stale the
    # moment the card is dragged elsewhere — so it is off by default.
    show_marker = card.get("show_marker", False)
    # Drop an icon identical to the marker ONLY when the marker is actually rendered —
    # otherwise there is nothing to duplicate, and stripping it can leave a card with no
    # icon at all (a 💬 item whose only vocabulary match is also 💬).
    icons = [icon for icon in item.icons if not show_marker or icon != item.marker]
    rendered = card["icon_separator"].join(icons)
    if rendered:
        rendered += card["icon_suffix"]
    return card["template"].format(
        check="x" if item.done else " ",
        marker=f"{item.marker} " if (show_marker and item.marker) else "",
        icons=rendered,
        text=item.text,
        meta=meta,
        id_comment=id_comment(cfg["ids"]["prefix"], item.item_id),
    )


def parse_board(cfg: dict, path: Path) -> tuple[dict[str, list[str]], set[str]]:
    """Read the board into {column: [card blocks]} plus the ids already present."""
    columns: dict[str, list[str]] = {}
    known: set[str] = set()
    if not path.is_file():
        return columns, known

    raw = FRONTMATTER.sub("", SETTINGS_BLOCK.sub("", path.read_text(encoding="utf-8")), count=1)
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is None:
            return
        columns.setdefault(current, [])
        block = "\n".join(buffer).strip("\n")
        if block:
            for card in re.split(r"\n(?=- \[)", block):
                if card.rstrip():
                    columns[current].append(card.rstrip())

    for line in raw.splitlines():
        heading = HEADING.match(line)
        if heading:
            flush()
            current = heading.group(1).strip()
            columns.setdefault(current, [])
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    flush()

    finder = id_pattern(cfg["ids"]["prefix"])
    for cards in columns.values():
        for card in cards:
            known.update(m.group(1) for m in finder.finditer(card))
    return columns, known


def parse_boards(
    cfg: dict, boards: dict[str, Path]
) -> tuple[dict[Path, dict[str, list[str]]], set[str], dict[str, Path], list[str]]:
    """Read every board: (columns per board, all ids, id -> the board holding it, duplicates).

    `duplicates` is the backstop for the disjointness claim above. The partition makes a
    second placement unreachable through this engine, but a board is a file an owner can
    edit and a config can be hand-written — so an id found on two boards is reported rather
    than resolved by quietly keeping whichever was read last.
    """
    per_board: dict[Path, dict[str, list[str]]] = {}
    where: dict[str, Path] = {}
    duplicates: list[str] = []
    known: set[str] = set()
    for path in board_order(boards):
        columns, ids = parse_board(cfg, path)
        per_board[path] = columns
        for item_id in ids:
            if where.get(item_id, path) != path:
                duplicates.append(item_id)
            where[item_id] = path
        known |= ids
    return per_board, known, where, sorted(set(duplicates))


def merged_columns(per_board: dict[Path, dict[str, list[str]]]) -> dict[str, list[str]]:
    """Every board's columns folded into one view, for counting and for name matching."""
    merged: dict[str, list[str]] = {}
    for columns in per_board.values():
        for name, cards in columns.items():
            merged.setdefault(name, []).extend(cards)
    return merged


def boards_status(cfg: dict, per_board: dict[Path, dict[str, list[str]]]) -> dict[str, str]:
    """id -> its column, across every board. No conflict: a card is on exactly one board."""
    status: dict[str, str] = {}
    for columns in per_board.values():
        status.update(board_status(cfg, columns))
    return status


def boards_cards(
    cfg: dict, boards: dict[str, Path], per_board: dict[Path, dict[str, list[str]]]
) -> list[tuple[str, str, bool]]:
    """Every card, board by board and within a board in its own reading order."""
    found: list[tuple[str, str, bool]] = []
    for path in board_order(boards):
        found += board_cards(cfg, per_board.get(path, {}))
    return found


def boards_to_write(
    boards: dict[str, Path], default_scope: str, per_board: dict[Path, dict[str, list[str]]]
) -> list[Path]:
    """Which boards a full render should write.

    The default board always: it is the register's board, and an absent one is what the
    first sync exists to create. A scope board only once it holds a card or already exists —
    so declaring a scope costs nothing until work actually lands in it, and a register whose
    second scope is still empty has one file rather than an empty second one.
    """
    default = boards[default_scope]
    return [
        path
        for path in board_order(boards)
        if path == default or any(per_board.get(path, {}).values()) or path.is_file()
    ]


def write_boards(
    cfg: dict, paths, per_board: dict[Path, dict[str, list[str]]]
) -> list[Path]:
    """Render exactly the boards named. Callers decide which; see `boards_to_write`."""
    written: list[Path] = []
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_board(cfg, per_board.get(path, {})), encoding="utf-8")
        written.append(path)
    return written


def seed_columns(cfg: dict, per_board: dict[Path, dict[str, list[str]]], paths) -> None:
    """Give each named board the configured flow, so its shape is stable across syncs."""
    for path in paths:
        columns = per_board.setdefault(path, {})
        for column in cfg["board"]["column_order"]:
            columns.setdefault(column, [])


def load_ledger(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "placed": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("placed", {})
    return data


def save_ledger(path: Path, ledger: dict) -> None:
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- Archive: Done is a recency window; the day files are the record ---------------
#
# A board where 16 of 38 cards are Done reads as history rather than as a worklist, and it
# compounds twice as fast once a register renders more than one board and each accumulates
# its own. The archive is not a new store, and that is the whole design: it is the DAY
# FILES, which are the source — permanent, dated, append-only, and carrying the reasoning
# `--show` prints. The board is derived and disposable, so taking a Done card off it loses
# nothing, and the ledger already stops a removed card coming back.
#
# NOT the Obsidian Kanban plugin's own archive, and not a `## Archive` section of our own.
# `column_sequence` takes "the configured flow first, then anything else present", so such a
# section is read as just another column: re-rendered as one, its cards still counted by
# `--list` and `--status`, and `--reconcile` stamping the column name back into day files.
#
# Two properties the verb is built around, each learned from a failure that already happened:
#
#   Anchors survive. An `^id` on a card is a link target the owner created by copying a link
#   to it. The board is disposable; `[[WORK-REGISTER#^id]]` pointing into it is not. Six of
#   this register's eleven anchors sit in Done, so archiving blind would break six live links
#   — the same loss `--rebuild` inflicted. Anchored cards are held back and named by id, and
#   --include-anchored is the deliberate opt-in.
#
#   Archived is not deleted. `resurrect_guard` is computed as `placed - on_board`, so without
#   a marker every archived card would land in the set the run reports as "deleted from the
#   board stay deleted" — a count that would then be nonsense. So an archived entry carries an
#   `archived` key holding the date it left. ONE key: its presence is the flag and its value
#   says when, where two keys for one fact could disagree.
#
#   Migration — what happens to the ledger that already exists? Entries written before this
#   verb carry no `archived` key at all, and every card placed then was on a board. So a
#   MISSING key reads as "not archived". The existing ledger keeps working untouched, is
#   never backfilled, and needs no schema bump.


def card_anchor(card: str) -> str:
    """An Obsidian block id (`^abc123`) on a card's first line, or "" — the link target.

    One notion of "this card is anchored", shared by --refresh, which must carry it across a
    re-render, and --archive, which must not take it off the board.
    """
    found = re.match(r"^[^\n]*?\s(\^[A-Za-z0-9-]+)\s*$", card.split("\n")[0])
    return found.group(1) if found else ""


def card_since(entry: dict, item_id: str) -> str | None:
    """When a card last became what it is, best available: ledger, then its own id.

    `since` is stamped on placement and on every move; `day` is the capture date a ledger
    written before `since` existed carries; the id's own date prefix is the last resort, and
    it is always there because the id was minted from it. --status ages a card sitting in a
    working lane with this and --archive ages one sitting in Done — one question, so one
    ladder rather than two that could drift.
    """
    stamped = entry.get("since") or entry.get("day")
    if stamped:
        return stamped
    return f"{item_id[:4]}-{item_id[4:6]}-{item_id[6:8]}" if item_id[:8].isdigit() else None


def archived_ids(ledger: dict) -> set[str]:
    """Ids the archive took off a board. A missing marker means not archived — see above."""
    return {
        item_id
        for item_id, entry in (ledger.get("placed") or {}).items()
        if entry.get("archived")
    }


def archive_selection(
    cfg: dict,
    ledger: dict,
    columns: dict[str, list[str]],
    before: str | None,
    keep: int | None,
    include_anchored: bool,
) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """One board's done column split into what leaves and what is held back.

    Returns (archive, anchored, unidentified), each a list of (id, card block, since).
    Nothing is ever dropped silently: a card held back is returned so the caller can name it.

      anchored      — carries an `^id` that a wiki link may point at
      unidentified  — carries no `wr:` id, so the ledger could not record it leaving, and an
                      untrackable removal is a deletion rather than an archive

    The window is applied to EVERY done card first, so `--keep N` means "the N most recent
    stay" rather than "the N most recent unanchored stay" — an anchored card held back is
    then extra, and the report says so.
    """
    placed = ledger.get("placed") or {}
    finder = id_pattern(cfg["ids"]["prefix"])

    rows: list[tuple] = []
    for card in columns.get(cfg["board"]["done_column"], []):
        found = finder.search(card)
        item_id = found.group(1) if found else ""
        rows.append((item_id, card, card_since(placed.get(item_id, {}), item_id)))
    # Newest first. An undated card sorts oldest: there is no evidence it is recent, and the
    # alternative — reading unknown as new — would keep precisely the cards nothing is known
    # about. It is still only a sort, never a removal: an undated card is archived only if the
    # window it falls outside says so.
    rows.sort(key=lambda row: (row[2] or "", row[0]), reverse=True)

    if keep is not None:
        stale = rows[keep:]
    else:
        stale = [row for row in rows if not row[2] or row[2] < before]

    archive: list[tuple] = []
    anchored: list[tuple] = []
    unidentified: list[tuple] = []
    for row in stale:
        if not row[0]:
            unidentified.append(row)
        elif card_anchor(row[1]) and not include_anchored:
            anchored.append(row)
        else:
            archive.append(row)
    return archive, anchored, unidentified


def board_status(cfg: dict, columns: dict[str, list[str]]) -> dict[str, str]:
    """id → the column it currently sits in. The board's answer to 'where is this?'"""
    finder = id_pattern(cfg["ids"]["prefix"])
    return {
        m.group(1): column
        for column, cards in columns.items()
        for card in cards
        for m in finder.finditer(card)
    }


def column_sequence(cfg: dict, columns: dict[str, list[str]]) -> list[str]:
    """The board's column order: the configured flow first, then anything else present."""
    ordered = list(cfg["board"]["column_order"])
    return ordered + [name for name in columns if name not in ordered]


def board_cards(cfg: dict, columns: dict[str, list[str]]) -> list[tuple[str, str, bool]]:
    """Every card in the board's own reading order: (id, column, checkbox).

    Column order first, then each card's position within its column — so a listing built
    on this reads as a SUBSET of the board rather than a re-sort of it, which is what lets
    a foreign session trust it without opening the board itself.
    """
    finder = id_pattern(cfg["ids"]["prefix"])
    found: list[tuple[str, str, bool]] = []
    for column in column_sequence(cfg, columns):
        for card in columns.get(column, []):
            done = card.lstrip()[:5].lower() == "- [x]"
            found += [(m.group(1), column, done) for m in finder.finditer(card)]
    return found


def marker_for_column(cfg: dict, column: str) -> str:
    for lane in cfg["board"].get("lanes", []):
        if lane["column"] == column:
            return lane["marker"]
    return ""


def reconcile_day_file(cfg: dict, path: Path, status: dict[str, str], mutate: bool) -> list[tuple]:
    """Stamp the board's status back onto a day file's items. Text is never touched.

    Only the checkbox and the lane marker are rewritten — the two fields the board owns.
    An item whose id is absent from the board is left exactly as it is.
    """
    prefix = cfg["ids"]["prefix"]
    finder = id_pattern(prefix)
    done_column = cfg["board"]["done_column"]

    lines = path.read_text(encoding="utf-8").splitlines()
    changes: list[tuple] = []

    for index, line in enumerate(lines):
        match = ITEM.match(line)
        if not match:
            continue
        # An id may sit on a wrapped continuation line; scan the whole item block for it.
        found = finder.search(line)
        probe = index + 1
        while not found and probe < len(lines) and CONTINUATION.match(lines[probe]) and not ITEM.match(lines[probe]):
            found = finder.search(lines[probe])
            probe += 1
        if not found:
            continue

        item_id = found.group(1)
        column = status.get(item_id)
        if column is None:
            continue

        indent, check, body = match.groups()
        done = column == done_column
        want_check = "x" if done else " "
        want_marker = "" if done else marker_for_column(cfg, column)

        stripped = finder.sub("", body).strip()
        _, text = split_marker(cfg, stripped)
        rebuilt_body = f"{want_marker} {text}".strip() if want_marker else text
        if finder.search(body):
            rebuilt_body += " " + id_comment(prefix, item_id)
        rebuilt = f"{indent}- [{want_check}] {rebuilt_body}"

        if rebuilt != line:
            changes.append((item_id, column, line.strip(), rebuilt.strip()))
            lines[index] = rebuilt

    if changes and mutate:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changes


def resolve_column(cfg: dict, columns: dict[str, list[str]], wanted: str) -> str:
    """Match a column by exact name, then by case-insensitive substring.

    Lets a caller say `--move 20260821-04="in progress"` without typing the emoji.
    """
    order = list(cfg["board"]["column_order"])
    candidates = order + [c for c in columns if c not in order]
    for column in candidates:
        if column == wanted:
            return column
    needle = wanted.strip().lower()
    hits = [c for c in candidates if needle in c.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(
            f"work-register: no column matches {wanted!r}. Columns: {', '.join(candidates)}"
        )
    raise SystemExit(f"work-register: {wanted!r} is ambiguous — matches {', '.join(hits)}")


def pop_card(cfg: dict, columns: dict[str, list[str]], item_id: str) -> tuple[str, str] | None:
    """Lift one card out of a board: (the card block, the column it came from).

    Separate from `move_card` because a migration lifts a card out of one board and places
    it on ANOTHER — a different operation from moving it between columns of one, and the
    only one that crosses files.
    """
    finder = id_pattern(cfg["ids"]["prefix"])
    for column, cards in columns.items():
        for candidate in list(cards):
            if any(m.group(1) == item_id for m in finder.finditer(candidate)):
                cards.remove(candidate)
                return candidate, column
    return None


def move_card(cfg: dict, columns: dict[str, list[str]], item_id: str, target: str) -> str:
    """Relocate one card between columns of ONE board. Returns the column it came from."""
    lifted = pop_card(cfg, columns, item_id)
    if lifted is None:
        raise SystemExit(f"work-register: no card with id {item_id} on the board")
    card, origin = lifted

    done = cfg["board"]["done_column"]
    # The checkbox is part of status, so crossing the Done boundary ticks or unticks it.
    if target == done:
        card = re.sub(r"^- \[ \]", "- [x]", card, count=1)
    elif origin == done:
        card = re.sub(r"^- \[[xX]\]", "- [ ]", card, count=1)

    columns.setdefault(target, []).append(card)
    return origin


# --- Placement drift: a card the render would now file somewhere else ---------------
#
# Two flavours, one pass, because they are the same question asked once: is every card on
# the board that the day files would put it on?
#
#   wrong-board — the card's track was reclassified into another scope, so the render names
#                 a different file from the one holding the card.
#   no-source   — the day-file item behind the card is gone, so no board claims it at all.
#
# Neither is repaired here, and that is the point. Moving a card between FILES is a status
# write: the board owns the column, and a card that teleports takes its column into a file
# the owner was not looking at. A sync that quietly relocated cards would be the board
# rearranging itself, which is exactly what `--probe` already refuses to do for the same
# reason. So this reports; `--migrate --apply` is the explicit act that moves.


def placement_drift(
    day_items: dict[str, Item],
    boards: dict[str, Path],
    default_scope: str,
    where: dict[str, Path],
    ledger: dict,
) -> tuple[list[dict], list[dict]]:
    """(cards on the wrong board, cards with no day-file item behind them)."""
    misplaced: list[dict] = []
    orphaned: list[dict] = []
    for item_id, path in where.items():
        item = day_items.get(item_id)
        if item is None:
            orphaned.append({"id": item_id, "board": path})
            continue
        target = board_for(boards, default_scope, item.scope)
        if target != path:
            misplaced.append({
                "id": item_id,
                "from": path,
                "to": target,
                "scope": item.scope or default_scope,
                # Dual-read of the ledger: an entry written before boards were split carries
                # no `scope` key at all, and a card placed then was necessarily in the
                # default scope — there was nowhere else to be. So a missing key reads as the
                # default rather than as unknown, and a pre-existing ledger needs no
                # migration of its own to keep working.
                "was": (ledger.get("placed", {}).get(item_id) or {}).get("scope", default_scope),
                "text": item.text,
            })
    misplaced.sort(key=lambda entry: entry["id"])
    orphaned.sort(key=lambda entry: entry["id"])
    return misplaced, orphaned


def report_drift(cfg: dict, misplaced: list[dict], orphaned: list[dict]) -> None:
    """Print both flavours in the same shape. Reporting only — nothing is moved here."""
    log = cfg["log"]
    width = cfg["card"].get("list_text_width", 72)
    if misplaced:
        print(f"{log['proposal']} {len(misplaced)} card(s) render to a different board now:")
        for entry in misplaced:
            print(f"   {entry['id']}  {entry['from'].name} → {entry['to'].name}")
            print(f"      scope {entry['was']} → {entry['scope']} · {elide(entry['text'], width)}")
    if orphaned:
        print(f"{log['warn']} {len(orphaned)} board card(s) have no day-file item behind them:")
        for entry in orphaned:
            print(f"   {entry['id']}  on {entry['board'].name} — no source; left where it is")


# --- Probe: resolve the references a card cites, and report what they say ----------
#
# The register already points at reality — cards cite `app#733`, `surge-bot#356`, canon
# plan paths. Probing those references needs no session data and no hook, and it is
# deterministic: a merged pull request is a fact, not an inference.
#
# It PROPOSES and never applies. Status is the board's field (see the ownership table at
# the top of this file), so a probe that moved cards would be taking a field it does not
# own — and a board that rearranges itself is one the owner stops trusting.

ISSUE_REF = re.compile(r"\b([a-z][\w.-]*)#(\d+)\b")
CANON_REF = re.compile(r"\b(\d{2}_[a-z_]+/[\w./-]+\.md)\b")
FM_FIELD = re.compile(r"^(\w+):\s*(.+?)\s*$", re.M)


def parse_day_sections(path: Path, date: str, prefix: str) -> list[dict]:
    """Split a day file into sections: heading, the ids under it, and its prose.

    A reference in an item's own text binds to that card (strong). One in the section's
    surrounding context paragraph binds only loosely (weak) — it is shared by every card
    under the heading, so it is reported as advisory rather than driving a proposal.
    """
    finder = id_pattern(prefix)
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: list[dict] = []
    current = {"group": "", "items": [], "prose": []}

    index = 0
    while index < len(lines):
        line = lines[index]
        heading = HEADING.match(line)
        if heading:
            if current["items"] or current["prose"]:
                sections.append(current)
            raw = heading.group(1).strip()
            current = {"group": ORDINAL.sub("", raw).strip() or raw, "items": [], "prose": []}
            index += 1
            continue

        match = ITEM.match(line)
        if match:
            body = match.group(3)
            probe = index + 1
            while probe < len(lines) and CONTINUATION.match(lines[probe]) and not ITEM.match(lines[probe]):
                body += " " + lines[probe].strip()
                probe += 1
            found = finder.search(body)
            if found:
                current["items"].append({"id": found.group(1), "text": finder.sub("", body).strip()})
            index = probe
            continue

        current["prose"].append(line)
        index += 1

    if current["items"] or current["prose"]:
        sections.append(current)
    for section in sections:
        section["prose"] = "\n".join(section["prose"])
        section["date"] = date
    return sections


def extract_refs(cfg: dict, text: str) -> list[tuple]:
    """Pull (kind, key) references out of prose. Unknown repo shorthands are ignored."""
    repos = cfg["probe"]["repos"]
    refs: list[tuple] = []
    for shorthand, number in ISSUE_REF.findall(text):
        if shorthand in repos:
            refs.append(("issue", f"{repos[shorthand]}#{number}"))
    for path in CANON_REF.findall(text):
        refs.append(("canon", path))
    return list(dict.fromkeys(refs))


def resolve_issue(ref: str, cache: dict) -> dict:
    """Ask GitHub what an issue or pull request is doing now. Degrades to unresolved."""
    if ref in cache:
        return cache[ref]
    repo, number = ref.split("#")
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{number}",
             "--jq", "{state:.state, merged:(.pull_request.merged_at // null), "
                     "is_pr:(.pull_request != null), title:.title}"],
            capture_output=True, text=True, timeout=25,
        )
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:120] or "gh failed")
        data = json.loads(out.stdout)
    except Exception as exc:  # offline, unauthenticated, renamed repo — all non-fatal
        result = {"resolved": False, "detail": str(exc)[:100]}
        cache[ref] = result
        return result

    if data.get("merged"):
        state, terminal = "MERGED", True
    elif data["state"] == "closed":
        state, terminal = "CLOSED", True
    else:
        state, terminal = "OPEN", False
    result = {
        "resolved": True, "terminal": terminal, "state": state,
        "kind": "pull request" if data["is_pr"] else "issue", "title": data["title"],
    }
    cache[ref] = result
    return result


def resolve_canon(cfg: dict, rel: str, cache: dict) -> dict:
    """Read a canon doc's readiness frontmatter. Terminal values are config, not code."""
    if rel in cache:
        return cache[rel]
    roots = [Path(r).expanduser() for r in cfg["probe"].get("canon_roots", [])]
    target = next((root / rel for root in roots if (root / rel).is_file()), None)
    if target is None:
        result = {"resolved": False, "detail": "not found under any canon root"}
        cache[rel] = result
        return result

    head = target.read_text(encoding="utf-8")[:4000]
    fm = FRONTMATTER.match(head)
    fields = dict(FM_FIELD.findall(fm.group(1))) if fm else {}
    terminal_values = {v.lower() for v in cfg["probe"]["terminal_readiness"]}
    for name in cfg["probe"]["readiness_fields"]:
        value = fields.get(name)
        if value:
            result = {
                "resolved": True, "terminal": value.strip().lower() in terminal_values,
                "state": f"{name}={value.strip()}", "kind": "canon doc", "title": rel,
            }
            cache[rel] = result
            return result
    result = {"resolved": False, "detail": "no readiness field"}
    cache[rel] = result
    return result


def probe_cards(cfg: dict, day_files: list, placement: dict) -> list[dict]:
    """For every open card, resolve what its references currently say."""
    prefix = cfg["ids"]["prefix"]
    skip = set(cfg["probe"].get("skip_columns") or []) | {cfg["board"]["done_column"]}
    cache: dict = {}
    findings: list[dict] = []

    for path in day_files:
        for section in parse_day_sections(path, path.stem, prefix):
            weak = extract_refs(cfg, section["prose"])
            for item in section["items"]:
                column = placement.get(item["id"])
                if column is None or column in skip:
                    continue
                strong = extract_refs(cfg, item["text"])
                resolved = []
                for kind, key in strong or weak:
                    info = resolve_issue(key, cache) if kind == "issue" else resolve_canon(cfg, key, cache)
                    resolved.append({"ref": key, "binding": "item" if strong else "section", **info})
                if resolved:
                    findings.append({
                        "id": item["id"], "column": column, "text": item["text"],
                        "date": section["date"], "refs": resolved,
                    })
    return findings


def days_since(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        return (_date.today() - _date.fromisoformat(stamp[:10])).days
    except ValueError:
        return None


def render_board(cfg: dict, columns: dict[str, list[str]]) -> str:
    out = ["---"]
    out += [f"{key}: {value}" for key, value in cfg["board"]["frontmatter"].items()]
    out += ["---", ""]

    for name in column_sequence(cfg, columns):
        out += [f"## {name}", ""]
        out += columns.get(name, [])
        out.append("")

    out += ["", "%% kanban:settings", "```", cfg["kanban"]["settings"], "```", "%%"]
    return "\n".join(out) + "\n"


# --- Read surface: return cards, change nothing ------------------------------------
#
# Every other verb either mutates or renders a verdict, so a session that is not the one
# holding the board had only bad options for "what is on my plate?" — read the whole
# board, read a whole day file, or take ids from --status with no text attached. These two
# verbs answer it directly, and they are the reason `track` is worth carrying on a card:
# it is the one field that partitions the board by who is asking.
#
# Strictly read-only. Ids are minted in memory (mutate=False) so a listing still reports
# the real placement of an un-stamped item, but no day file, board or ledger is written.


def day_file_items(cfg: dict, day_files: list[Path]) -> dict[str, Item]:
    """id → Item across the day files. The day-file-owned half of every card."""
    found: dict[str, Item] = {}
    for path in day_files:
        items, _ = parse_day_file(cfg, path, path.stem, mutate=False)
        for item in items:
            found[item.item_id] = item
    return found


def elide(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)].rstrip() + "…"


def card_json(item: Item, column: str, done: bool) -> dict:
    """The programmatic shape of one card. Key names are the contract — keep them stable."""
    return {
        "id": item.item_id,
        "date": item.date,
        "group": item.group,
        "column": column,
        "track": item.track,
        "scope": item.scope,
        "tags": item.tags,
        "done": done,
        "text": item.text,
    }


def find_section(cfg: dict, day_files: list[Path], item_id: str) -> tuple[Path, dict, dict] | None:
    """Locate the day-file section that carries one card: (path, section, item)."""
    prefix = cfg["ids"]["prefix"]
    for path in day_files:
        for section in parse_day_sections(path, path.stem, prefix):
            for item in section["items"]:
                if item["id"] == item_id:
                    return path, section, item
    return None


# --- init: stand a register up in a vault that has never had one -------------------
#
# The engine already holds no vault path — a register is nothing but a `[register.<name>]`
# binding plus a marker at its root. What was missing is the verb that writes those two
# files, so standing a register up in a new vault meant knowing the shape by heart.
#
# Init writes exactly three things and refuses to overwrite any of them:
#
#   <root>/.work-register.toml   the corpus contract  (discovery marker + conventions)
#   ~/.config/work-register/…    the per-machine binding, MERGED into what is there
#   <root>/<register_dir>/       the day-file directory, with a README of the conventions
#
# It deliberately writes no board. The board is derived — the first sync renders it — and
# an empty one scaffolded here would be a second source of truth for one run.

# `__NAME__` is substituted, not `{}`-formatted, so the examples below stay free to contain
# the braces a card template needs.
INIT_CONTRACT = '''schema_version = 1

# work-register corpus contract, for the register named
#   __NAME__
# which is rooted at this directory.
#
# Two jobs:
#   1. Discovery — a session launched anywhere inside this vault resolves its register by
#      walking up to this file, so no absolute path is needed in the engine or the skill.
#   2. Contract authority — this vault governs its own conventions. Columns, lane markers,
#      the tag/icon vocabulary and the card shape are declared here, and layer OVER the
#      per-machine base config, which carries only the path bindings.
#
# Deliberately absent: the `[register.…]` binding itself, which names an absolute path —
# a property of a machine, not of this vault. A copy here would be wrong the moment the
# vault is cloned somewhere else, so it lives in the base config instead.
#
# Grammar is logic, not config: the day-file item syntax and the id format live in the
# engine. What lives here is vocabulary — the part that changes as the work changes.
#
# Every key has a code default, so this file is short on purpose. Add to it only where this
# vault's conventions genuinely differ from the engine's. `--show-config` prints the layers.

# Marker → column, the one piece the engine cannot default: a marker vocabulary is a
# choice, and with no lane declared every unticked item falls to `default_column`. The
# first lane whose marker heads the item text wins. A ticked checkbox overrides the marker
# and routes to `done_column`, so the done column needs no lane of its own.
#
# Markers are written WITHOUT the emoji variation selector, so an item matches whether or
# not the source that typed it carried one.
#
# The columns named here are the engine's default flow: ▶️ Next → ⏳ In progress →
# 🔴 Blocked → 🏁 Done.
[[board.lanes]]
marker = "▶"
column = "▶️ Next"

[[board.lanes]]
marker = "⏳"
column = "⏳ In progress"

[[board.lanes]]
marker = "🔴"
column = "🔴 Blocked"

# Adding a lane is two declarations, not one: the column has to join the flow as well.
# `column_order` REPLACES the engine's list rather than extending it, so spell out the
# whole flow in the order it should read left to right.
#
# [board]
# column_order = ["📥 Wishlist", "▶️ Next", "⏳ In progress", "🔴 Blocked", "🏁 Done"]
#
# [[board.lanes]]
# marker = "📥"
# column = "📥 Wishlist"

# Case-insensitive regex over the item text plus its day-file heading. EVERY match
# contributes, so one card can carry several tags and several icons: the tags are what the
# Kanban plugin can search and colour by, the icons are what make a tile legible without
# reading it. This is the knob that keeps a board readable, and it needs no code change.
#
# [[tag_rules]]
# pattern = "deploy|terraform|\\\\bprod(uction)?\\\\b"
# tag     = "#prod"
# icon    = "🚀"

# Which track a card belongs to when the day file does not say outright. A card declares
# its own with `::track-name` in the item text or in its `##` heading, and an explicit
# declaration always wins.
#
# Matching is shaped like `tag_rules` with one deliberate difference: the FIRST match wins
# and the rest are not consulted. A card touches several concerns, so it carries several
# tags; it belongs to one thread of work, so the rules are an ordered decision. Order
# therefore matters — narrow, unmistakable vocabulary above the broad patterns that would
# otherwise swallow it. A card matching nothing has no track, which is a valid answer.
#
# [[track_rules]]
# pattern = "\\\\bci\\\\b|required check|ruleset"
# track   = "platform-devex"

# Scope — what tells personal work from work work. Day files stay ONE stream, because that
# is how a day happens; the render is what separates them, writing one board per scope so
# the default board can be left open with no personal work on it.
#
# Scope belongs to a TRACK, not to a card: personal versus work describes a thread of work
# rather than an individual item, so it is declared once and every card on that track
# inherits it. A card with no track falls to the default scope's board — nothing is ever
# left off every board.
#
# Every card renders to exactly one board. That is the property the split rests on: the
# board owns a card's column, so a card on two boards would have two owners for one field.
#
# Declaring a scope here creates no file until a card in it exists. Reclassifying a track
# afterwards moves its cards BETWEEN files, which is a status write — `--migrate` reports
# it, `--migrate --apply` performs it, and sync never does it on its own.
#
# [scope]
# default          = "work"    # the scope a track sits in unless it says otherwise
# suppress_default = true      # cards in that scope carry no #scope/… tag
# track.house-move = "personal"
# board.personal   = "PERSONAL-BOARD.md"   # relative to this vault root
'''

INIT_README = '''# Work register — day files

One file per day, named `YYYY-MM-DD.md`. This directory is the **source**; the board is
derived from it and disposable, fully reconstructible from these files.

- `##` headings group a day's items, and a heading becomes each card's 🧭 group.
- One checkbox line is one card. Wrapped lines are joined, so hard-wrap freely.
- A leading marker routes the item to a column. The marker → column map lives in
  `.work-register.toml` at the vault root — read it there rather than memorising it.
- `- [x]` overrides the marker and routes the card to the done column.
- `::track-name`, in an item or in its `##` heading, declares the thread of work it
  belongs to. On a heading it claims every item underneath.
- Day files are append-only. Never hand-edit a past day to reflect new status: move the
  card on the board and run `--reconcile`, which rewrites only the checkbox and marker.
- Date every claim carried in from memory ("as of the 2026-08-20 park — re-verify"). A
  day file is read weeks later, and an undated status rots silently.
- The `<!-- wr:… -->` ids are minted and stamped by the sync. Do not write them by hand.
- `.sync-state.json` is the ledger. It records every id ever placed, which is what keeps a
  card deleted from the board from being resurrected by the next sync.
'''


def slug_for_register(raw: str) -> str:
    """A register name is used as a TOML bare key, so it has to survive being one.

    A vault directory is commonly named for a domain (`notes.example.com`), and the dots in
    a bare key would silently nest three tables instead of naming one register.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "register"


def base_config_block(name: str, root: Path, register_dir: str, board: str) -> str:
    return (
        f"\n[register.{name}]\n"
        "# Absolute paths live only here, per machine and out of the vault. The vault at\n"
        f"# this root governs its own conventions in its {MARKER_NAME}.\n"
        f'data_root    = "{root}"\n'
        f'register_dir = "{register_dir}"\n'
        f'board        = "{board}"\n'
    )


BASE_CONFIG_HEADER = """# work-register — per-machine register registry.
#
# Which registers THIS machine serves and where their roots are. Absolute paths live only
# here, out of the vault, so nothing user-specific is committed to the skill.
#
# Conventions — columns, lanes, the tag/icon vocabulary, card shape — are NOT declared
# here. Each register root owns them in its own .work-register.toml, which layers over
# this file. Every key has a code default, so this file only needs the bindings.

schema_version = 1
"""


def merge_base_config(text: str, block: str, set_default: str | None) -> str:
    """Splice a new register into the base config's TEXT, never its parsed form.

    Re-serialising the parsed document would drop every comment, and the comments are the
    only thing that explains why a machine-local file exists at all. So the register table
    is appended — safe, because a table header ends whatever table preceded it — while
    `default_register` is a bare key and must be spliced in ABOVE the first table header,
    or it would silently become a member of the last table in the file.
    """
    lines = text.splitlines()
    if set_default:
        entry = [
            "# The register a bare invocation resolves to.",
            f'default_register = "{set_default}"',
            "",
        ]
        first_table = next(
            (i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
        )
        lines[first_table:first_table] = entry
    body = "\n".join(lines).rstrip("\n")
    return (body + "\n" if body else "") + block


def init_register(args) -> int:
    log = DEFAULTS["log"]
    root = Path(args.init).expanduser().resolve()
    if not root.is_dir():
        print(
            f"work-register: --init wants an existing vault directory; {root} is not one",
            file=sys.stderr,
        )
        return 2

    name = slug_for_register(args.name or root.name)
    register_dir = args.register_dir or DEFAULTS["paths"]["register_dir"]
    board = args.board or DEFAULTS["paths"]["board"]
    marker = root / MARKER_NAME

    # Refuse before writing anything. A half-initialised register is worse than none: the
    # binding and the contract only mean something together.
    base_text = BASE_CONFIG.read_text(encoding="utf-8") if BASE_CONFIG.is_file() else ""
    before = read_toml(BASE_CONFIG) if BASE_CONFIG.is_file() else {}
    refusals = []
    if marker.is_file():
        refusals.append(f"{marker} already exists")
    if name in (before.get("register") or {}):
        declared = before["register"][name].get("data_root", "?")
        refusals.append(f"{BASE_CONFIG} already declares [register.{name}] → {declared}")
    if refusals:
        for refusal in refusals:
            print(f"work-register: {refusal}", file=sys.stderr)
        print(
            "work-register: init will not overwrite an existing register. Pass --name to "
            "declare a second one, or edit these files by hand.",
            file=sys.stderr,
        )
        return 1

    contract = INIT_CONTRACT.replace("__NAME__", name)
    block = base_config_block(name, root, register_dir, board)
    set_default = name if not before.get("default_register") else None
    merged = (
        merge_base_config(base_text, block, set_default)
        if base_text
        else merge_base_config(BASE_CONFIG_HEADER, block, name)
    )

    print(f"{log['init']} init register {name!r} at {root}")
    print(f"   contract:  {marker}")
    print(f"   binding:   {BASE_CONFIG}  [register.{name}]")
    print(f"   day files: {root / register_dir}")
    print(f"   board:     {root / board}  (not written — the first sync renders it)")
    if set_default:
        print(f"   default_register: unset → {name}")
    else:
        print(f"   default_register: {before.get('default_register')!r} left as it is")

    if args.dry_run:
        print(f"\n{log['dry_run']} dry run — nothing written. {MARKER_NAME} would be:\n")
        print(contract)
        verb = "appended to" if base_text else "created, carrying"
        print(f"{log['dry_run']} and {BASE_CONFIG} would be {verb}:\n")
        print(block.strip("\n"))
        return 0

    marker.write_text(contract, encoding="utf-8")

    # The base config is shared with every other register on this machine, so it is the one
    # write that can destroy work. Back it up, splice, re-parse, and prove nothing that was
    # there before has moved — restoring from the backup if anything has.
    backup = None
    if base_text:
        stamp = datetime.now().astimezone().strftime("%Y%m%d%H%M%S")
        backup = BASE_CONFIG.parent / f"{BASE_CONFIG.name}.bak-{stamp}"
        backup.write_text(base_text, encoding="utf-8")
    BASE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    BASE_CONFIG.write_text(merged, encoding="utf-8")

    try:
        after = read_toml(BASE_CONFIG)
        lost = [key for key, value in before.items() if key != "register" and after.get(key) != value]
        lost += [
            f"register.{key}"
            for key, value in (before.get("register") or {}).items()
            if (after.get("register") or {}).get(key) != value
        ]
        if name not in (after.get("register") or {}):
            lost.append(f"register.{name} (the new binding did not land)")
        if lost:
            # RuntimeError, not SystemExit: SystemExit is a BaseException, so it would sail
            # straight past the handler that restores the backup.
            raise RuntimeError("lost or altered: " + ", ".join(lost))
    except Exception as exc:
        if backup:
            BASE_CONFIG.write_text(base_text, encoding="utf-8")
        marker.unlink(missing_ok=True)
        print(f"work-register: {BASE_CONFIG} merge rejected — {exc}", file=sys.stderr)
        print("work-register: restored the base config and removed the contract", file=sys.stderr)
        return 1

    if backup:
        print(f"   {log['ok']} base config merged · backup {backup.name}")

    (root / register_dir).mkdir(parents=True, exist_ok=True)
    readme = root / register_dir / "README.md"
    if readme.is_file():
        print(f"   {log['warn']} {readme} already exists — left as it is")
    else:
        readme.write_text(INIT_README, encoding="utf-8")

    # Prove it rather than assert it: resolve the register through the same two phases every
    # other verb uses, and report what the engine actually sees.
    layers, resolved, register, cfg = resolve_all(None, name)
    resolved_dir, resolved_board = register_paths(cfg, register)
    print(f"\n{log['config']} resolved through {len(layers.sources)} config layer(s):")
    for source in layers.sources:
        print(f"   - {source}")
    print(f"{log['register']} register: {resolved}  {log['board']} board: {resolved_board}")
    print(f"   day files: {resolved_dir}  (exists: {resolved_dir.is_dir()})")
    print(f"   lanes: {len(cfg['board'].get('lanes', []))}"
          f" · columns: {len(cfg['board']['column_order'])}"
          f" · vocabulary rules: {len(cfg.get('tag_rules', []))}"
          f" · track rules: {len(cfg.get('track_rules', []))}")
    print(f"{log['done']} write a {resolved_dir.name}/YYYY-MM-DD.md day file, then run "
          "sync_board.py to render the board")
    return 0


# --- Entry point ------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", help="named register to sync (default: default_register)")
    parser.add_argument("--config", help="extra config layer, applied last")
    parser.add_argument(
        "--init",
        metavar="PATH",
        help="stand a register up in the vault at PATH: write its .work-register.toml "
        "contract, merge a [register.<name>] binding into the per-machine base config, and "
        "create the day-file directory. Writes no board — the first sync renders it",
    )
    parser.add_argument(
        "--name",
        help="with --init, the register's name (default: a slug of the directory basename)",
    )
    parser.add_argument(
        "--register-dir",
        help="with --init, the day-file directory, relative to the vault root "
        f"(default: {DEFAULTS['paths']['register_dir']})",
    )
    parser.add_argument(
        "--board",
        help="with --init, the board file, relative to the vault root "
        f"(default: {DEFAULTS['paths']['board']})",
    )
    parser.add_argument("--since", help="only ingest day files on or after this date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-config", action="store_true", help="print resolved layers and exit")
    parser.add_argument(
        "--move",
        action="append",
        metavar="ID=COLUMN",
        help="move a card to a column, e.g. --move 20260821-04='in progress' (repeatable). "
        "COLUMN matches on substring, so the emoji is optional",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="resolve the references open cards cite (pull requests, issues, canon docs) "
        "and PROPOSE status changes; never moves a card",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="report register health: last capture, lane counts, cards stale in a lane",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list cards in board order, filtered by --track / --scope / --column / "
        "--open. "
        "Read-only: writes nothing",
    )
    parser.add_argument(
        "--show",
        metavar="ID",
        help="print the day-file section behind one card — its heading, context prose and "
        "item line — plus the card's current column and track. Read-only",
    )
    parser.add_argument("--track", help="with --list, keep only cards on this exact track")
    parser.add_argument(
        "--scope",
        help="with --list, keep only cards in this exact scope — a track's scope, which "
        "every card on that track inherits",
    )
    parser.add_argument(
        "--column",
        help="with --list, keep only cards in this column (substring match, emoji optional)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="with --list, drop cards in the done column. Parked is deferred, not closed, "
        "so it is NOT excluded",
    )
    parser.add_argument(
        "--json", action="store_true", help="with --list, emit JSON instead of lines"
    )
    parser.add_argument(
        "--brief", action="store_true", help="with --status, print only the verdict line"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-render card faces from the day files while KEEPING each card's column "
        "(propagates a text correction without touching placement)",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="stamp the board's status (column + checkbox) back onto the day files",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="discard the board and re-place every item at its day-file lane; DISCARDS drags "
        "(use after changing the lane set)",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="report cards whose scope now names a different board from the one holding "
        "them, and board cards with no day-file item. Reports only — pass --apply to move",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --migrate, actually relocate the reported cards between boards, keeping "
        "each card's column. Without it --migrate changes nothing",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="trim the done column on every board to a recency window, removing the cards "
        "outside it. The day files are the record and are never touched. Bare, the window is "
        "[archive] keep_days; --before and --keep name one explicitly",
    )
    parser.add_argument(
        "--before",
        metavar="YYYY-MM-DD",
        help="with --archive, archive done cards last moved before this date",
    )
    parser.add_argument(
        "--keep",
        type=int,
        metavar="N",
        help="with --archive, keep the N most recent done cards on EACH board and archive "
        "the rest",
    )
    parser.add_argument(
        "--include-anchored",
        action="store_true",
        help="with --archive, also archive cards carrying an Obsidian block id (^abc123). "
        "Off by default: an [[…#^id]] link points at each one, so removing it breaks a live "
        "link. Held-back cards are always reported by id",
    )
    args = parser.parse_args()

    if args.apply and not args.migrate:
        parser.error("--apply is meaningless on its own; it qualifies --migrate")
    for flag, value in (("--before", args.before), ("--keep", args.keep),
                        ("--include-anchored", args.include_anchored or None)):
        if value is not None and not args.archive:
            parser.error(f"{flag} is meaningless on its own; it qualifies --archive")
    if args.before and args.keep is not None:
        parser.error(
            "--before and --keep ask different questions — a date cut and a column size. "
            "Pass one"
        )
    if args.keep is not None and args.keep < 0:
        parser.error("--keep wants a card count, so it cannot be negative")

    # Before resolution, not after: on a machine with no base config there is no register to
    # resolve yet, and writing one is the whole point of the verb.
    if args.init:
        return init_register(args)

    layers, name, register, cfg = resolve_all(args.config, args.register)
    log = cfg["log"]

    if args.show_config:
        print(f"{log['config']} config layers (later wins):")
        for source in layers.sources:
            print(f"   - {source}")
        if not layers.sources:
            print("   - (code defaults only)")
        registers = cfg.get("register") or {}
        print(f"{log['register']} registers: {', '.join(sorted(registers)) or '(none)'}")
        print(f"   default: {cfg.get('default_register') or '(unset)'} · resolved: {name}")
        print(f"   lanes: {len(cfg['board'].get('lanes', []))}"
              f" · vocabulary rules: {len(cfg.get('tag_rules', []))}"
              f" · track rules: {len(cfg.get('track_rules', []))}")
        default_scope, boards = scope_boards(cfg, register)
        print(f"   scope default: {default_scope or '(none)'}"
              f" · boards: {len(board_order(boards))}")
        return 0

    register_dir, _ = register_paths(cfg, register)
    default_scope, boards = scope_boards(cfg, register)

    if not register_dir.is_dir():
        raise SystemExit(f"work-register: register dir not found: {register_dir}")

    # Refuse before a single id is minted. --rebuild re-places every card from scratch, which
    # on a split register means re-partitioning them across every board at once: every
    # Obsidian block anchor is lost, every staleness clock resets, and one mis-scoped track
    # silently relocates its whole slice into a file nobody was watching. Redesigning the
    # verb is a separate question; refusing to run it blind is not.
    if args.rebuild and len(board_order(boards)) > 1:
        raise SystemExit(
            "work-register: --rebuild refuses on a register that renders "
            f"{len(board_order(boards))} boards. It would re-partition every card across all "
            "of them in one pass, discarding drags, block anchors and staleness. Use "
            "--refresh for a text correction, and --migrate --apply to move a reclassified "
            "track's cards between boards."
        )

    every_day_file = sorted(p for p in register_dir.iterdir() if DAY_FILE.match(p.name))
    day_files = every_day_file
    if args.since:
        day_files = [p for p in day_files if p.stem >= args.since]
    if not day_files:
        print(f"{log['empty']} work-register [{name}]: no day files to ingest")
        return 0

    ledger_path = register_dir / LEDGER_NAME
    ledger = load_ledger(ledger_path)
    per_board, on_board, where, duplicated = parse_boards(cfg, boards)
    # --brief exists to be consumed by a hook, so it must emit exactly one line; --json is
    # a machine surface, so its stdout must be nothing but the document.
    if not (args.status and args.brief) and not args.json:
        listed = board_order(boards)
        if len(listed) == 1:
            print(f"{log['register']} register: {name}  {log['board']} board: {listed[0]}")
        else:
            print(f"{log['register']} register: {name}  {log['board']} {len(listed)} boards:")
            for scope, path in boards.items():
                print(f"   {scope or '(no scope)'}: {path}")
        if duplicated:
            # The disjointness backstop. Reachable only by hand-editing a board, but a wrong
            # answer here is two owners for one card's column, so it is said out loud.
            print(f"   {log['warn']} {len(duplicated)} card id(s) appear on more than one "
                  f"board: {', '.join(duplicated)}")

    # --- list: the read surface — filtered cards, in the board's own order -------
    if args.list:
        items = day_file_items(cfg, day_files)
        merged = merged_columns(per_board)
        wanted = resolve_column(cfg, merged, args.column) if args.column else None
        done_column = cfg["board"]["done_column"]

        rows, orphans = [], 0
        for item_id, column, done in boards_cards(cfg, boards, per_board):
            item = items.get(item_id)
            if item is None:
                orphans += 1
                continue
            if wanted and column != wanted:
                continue
            # --open drops the done column and nothing else: Parked is deferred work, not
            # closed work, and `[probe] skip_columns` is probe vocabulary, not this filter.
            if args.open and column == done_column:
                continue
            if args.track and item.track != args.track:
                continue
            if args.scope and item.scope != args.scope:
                continue
            rows.append((item, column, done))

        if args.json:
            print(json.dumps(
                [card_json(item, column, done) for item, column, done in rows],
                indent=2, ensure_ascii=False,
            ))
            return 0

        asked = " · ".join(filter(None, [
            f"track {args.track}" if args.track else "",
            f"scope {args.scope}" if args.scope else "",
            f"column {wanted}" if wanted else "",
            "open only" if args.open else "",
        ])) or "no filter"
        if not rows:
            print(f"{log['empty']} no cards match ({asked}) — the register is fine, "
                  "this slice is just empty")
            return 0
        width = cfg["card"].get("list_text_width", 72)
        for item, column, _ in rows:
            track = f" · {log['track']} {item.track}" if item.track else ""
            print(f"   {item.item_id} · {column}{track} · {elide(item.text, width)}")
        print(f"{log['done']} {len(rows)} card(s) · {asked}")
        if orphans:
            print(f"   {log['warn']} {orphans} card(s) on the board have no day-file item")
        return 0

    # --- show: the reasoning behind one card, not just its face ------------------
    if args.show:
        target = args.show.strip()
        located = find_section(cfg, day_files, target)
        if located is None:
            print(f"{log['unresolved']} no card with id {target!r} in any day file "
                  f"under {register_dir}", file=sys.stderr)
            return 1
        path, section, entry = located
        item = day_file_items(cfg, day_files).get(target)
        column = boards_status(cfg, per_board).get(target) or "(not on the board)"
        track = f" · {log['track']} {item.track}" if item and item.track else ""
        print(f"   {target} · {column}{track}")
        print(f"   {log['register']} {path.name} — {section['group'] or '(no heading)'}")
        prose = section["prose"].strip("\n")
        if prose.strip():
            print()
            print(prose)
        print()
        print(f"   → {entry['text']}")
        return 0

    # --- move: relocate cards by id (the /-command surface) ----------------------
    if args.move:
        moved = []
        # A move is between COLUMNS of one board. Which board is not the caller's to pick:
        # it is wherever the card already is, because scope decides the file and a move is
        # not a scope change. Crossing files is `--migrate`, and only after a config edit.
        touched: set[Path] = set()
        for spec in args.move:
            if "=" not in spec:
                raise SystemExit(f"work-register: --move expects ID=COLUMN, got {spec!r}")
            item_id, wanted = spec.split("=", 1)
            item_id = item_id.strip()
            path = where.get(item_id)
            if path is None:
                raise SystemExit(f"work-register: no card with id {item_id} on any board")
            columns = per_board[path]
            target = resolve_column(cfg, columns, wanted)
            origin = move_card(cfg, columns, item_id, target)
            touched.add(path)
            moved.append((item_id, origin, target, path))
            print(f"   {log['moved']} {item_id}: {origin} → {target}")
        seed_columns(cfg, per_board, touched)
        if args.dry_run:
            print(f"{log['dry_run']} dry run — {len(moved)} move(s) not written")
            return 0
        write_boards(cfg, sorted(touched), per_board)
        for item_id, _, target, path in moved:
            entry = ledger["placed"].setdefault(item_id, {})
            entry["column"] = target
            entry["scope"] = scope_of(boards, path)
            entry["since"] = datetime.now().astimezone().date().isoformat()
        save_ledger(ledger_path, ledger)
        # A move IS a status change, so carry it straight back to the day files.
        status = boards_status(cfg, per_board)
        touched = sum(
            len(reconcile_day_file(cfg, path, status, mutate=True)) for path in day_files
        )
        print(f"{log['done']} {len(moved)} move(s) · {touched} day-file line(s) reconciled")
        return 0

    # --- status: is the register still telling the truth? ------------------------
    if args.status:
        placement = boards_status(cfg, per_board)
        watch = set(cfg["status"].get("watch_columns") or []) or (
            set(cfg["board"]["column_order"])
            - {cfg["board"]["done_column"], cfg["probe"].get("propose_column") or ""}
        )
        last_capture = day_files[-1].stem if day_files else None
        capture_age = days_since(last_capture)

        stale = []
        for item_id, column in placement.items():
            if column not in watch:
                continue
            age = days_since(card_since(ledger["placed"].get(item_id, {}), item_id))
            if age is not None and age >= cfg["status"]["stale_days"]:
                stale.append((age, item_id, column))
        stale.sort(reverse=True)

        # Scanned unfiltered: a --since window would report every card outside it as having
        # no day-file item, which is a false alarm rather than a finding.
        misplaced, orphaned = placement_drift(
            day_file_items(cfg, every_day_file), boards, default_scope, where, ledger
        )

        problems = []
        if capture_age is not None and capture_age >= cfg["status"]["capture_gap_days"]:
            problems.append(f"last capture {capture_age}d ago")
        if stale:
            problems.append(f"{len(stale)} card(s) stale >{cfg['status']['stale_days']}d")
        # Only the wrong-board flavour reaches the verdict line. It is a consequence of a
        # config edit the owner just made, so it is actionable now and it is new. A card with
        # no day-file source is older drift with no settled disposition yet, so it is
        # reported in the detail rather than escalated into a one-line session nag.
        if misplaced:
            problems.append(f"{len(misplaced)} card(s) on the wrong board")
        verdict = (
            f"{log['warn']} work-register [{name}]: " + " · ".join(problems)
            if problems else f"{log['ok']} work-register [{name}]: current"
        )
        print(verdict)
        if args.brief:
            return 0
        counts = {c: len(v) for c, v in merged_columns(per_board).items() if v}
        print("   " + " · ".join(f"{c} {n}" for c, n in counts.items()))
        for age, item_id, column in stale[:10]:
            print(f"   {log['warn']} {item_id} — {age}d in {column}")
        if misplaced or orphaned:
            report_drift(cfg, misplaced, orphaned)
            if misplaced:
                print("   move them with: sync_board.py --migrate --apply")
        return 0

    # --- migrate: a card the render would now file on a different board ----------
    #
    # A scope change is a config edit, and its consequence is that cards move between FILES.
    # That is a status write, so it never happens as a side effect: not on sync, not on
    # refresh, not on reconcile. It takes this verb, and then it takes --apply as well —
    # two deliberate acts, because the failure being guarded against is a card quietly
    # leaving the board the owner was looking at.
    if args.migrate:
        # Unfiltered for the same reason --status is: a --since window turns every card
        # outside it into a phantom no-source orphan.
        misplaced, orphaned = placement_drift(
            day_file_items(cfg, every_day_file), boards, default_scope, where, ledger
        )
        if not misplaced and not orphaned:
            print(f"{log['ok']} every card is on the board its scope names")
            return 0
        report_drift(cfg, misplaced, orphaned)

        if not misplaced:
            print(f"\n{log['warn']} nothing to migrate — a card with no day-file item has no "
                  "scope to move it to, so it stays where it is")
            return 0
        if not args.apply:
            print(f"\n{log['proposal']} nothing has been moved. Each card keeps its column; "
                  "only the file rendering it changes.")
            print("   apply with:\n   sync_board.py --migrate --apply")
            return 0
        if args.dry_run:
            print(f"\n{log['dry_run']} dry run — {len(misplaced)} card(s) not moved")
            return 0

        touched: set[Path] = set()
        for entry in misplaced:
            lifted = pop_card(cfg, per_board[entry["from"]], entry["id"])
            if lifted is None:
                continue
            card, column = lifted
            # The column travels with the card. It is the board's field and this is not a
            # status change — only a change of which file holds that status.
            per_board.setdefault(entry["to"], {}).setdefault(column, []).append(card)
            touched |= {entry["from"], entry["to"]}
            placed = ledger["placed"].setdefault(entry["id"], {})
            placed["column"] = column
            placed["scope"] = scope_of(boards, entry["to"])
            print(f"   {log['moved']} {entry['id']}: {entry['from'].name} → "
                  f"{entry['to'].name}  [{column}]")
        seed_columns(cfg, per_board, touched)
        written = write_boards(cfg, sorted(touched), per_board)
        save_ledger(ledger_path, ledger)
        print(f"{log['done']} {len(misplaced)} card(s) migrated · "
              f"{len(written)} board(s) written")
        return 0

    # --- archive: trim the done column to a recency window -----------------------
    #
    # A one-directional REMOVAL from the board, and nothing else. The day files are the
    # archive, so they are not read for status and not written at all — which is what makes
    # this safe to run on a register the owner is mid-drag on.
    if args.archive:
        done_column = cfg["board"]["done_column"]
        before = args.before
        if args.keep is None and before is None:
            keep_days = cfg["archive"]["keep_days"]
            before = (_date.today() - timedelta(days=keep_days)).isoformat()
            window = f"done before {before} · [archive] keep_days {keep_days}"
        elif args.keep is not None:
            window = f"the {args.keep} most recent kept, per board"
        else:
            window = f"done before {before}"

        # Unfiltered on purpose: a --since window would make every card outside it read as
        # having no day-file item, which would put "(no day-file item)" on a card that has one.
        items = day_file_items(cfg, every_day_file)
        width = cfg["card"].get("list_text_width", 72)
        stamp = datetime.now().astimezone().date().isoformat()

        leaving: list[tuple[str, Path]] = []
        anchored: list[tuple] = []
        unidentified: list[tuple] = []
        touched: set[Path] = set()

        print(f"{log['archived']} archive · {done_column} · {window}")
        for path in board_order(boards):
            columns = per_board.get(path, {})
            chosen, held, unnamed = archive_selection(
                cfg, ledger, columns, before, args.keep, args.include_anchored
            )
            anchored += held
            unidentified += unnamed
            for item_id, card, since in chosen:
                item = items.get(item_id)
                mark = card_anchor(card)
                onto = f" ⇠ {path.name}" if len(board_order(boards)) > 1 else ""
                print(f"   {log['archived']}{onto} {item_id} · done {since or 'undated'}"
                      f"{' · ' + mark if mark else ''} · "
                      f"{elide(item.text if item else '(no day-file item)', width)}")
                columns[done_column].remove(card)
                leaving.append((item_id, path))
                touched.add(path)

        # Held back, never dropped. Both lists are named by id: a card that stayed for a
        # reason the owner cannot see is indistinguishable from one the verb missed.
        if anchored:
            print(f"   {log['warn']} {len(anchored)} anchored card(s) left on the board — an "
                  "[[…#^id]] link may point at each:")
            for item_id, card, _ in anchored:
                print(f"      {item_id} {card_anchor(card)}")
            print("      archive them too with: --archive --include-anchored")
        if unidentified:
            print(f"   {log['warn']} {len(unidentified)} done card(s) carry no "
                  f"{cfg['ids']['prefix']}: id — left on the board, because the ledger could "
                  "not record them leaving and an untracked removal is a deletion")

        if not leaving:
            print(f"{log['ok']} nothing to archive · {window}")
            return 0
        if args.dry_run:
            print(f"{log['dry_run']} dry run — would archive {len(leaving)} card(s) from "
                  f"{len(touched)} board(s); no board, day file or ledger written")
            return 0

        seed_columns(cfg, per_board, touched)
        written = write_boards(cfg, sorted(touched), per_board)
        for item_id, path in leaving:
            entry = ledger["placed"].setdefault(item_id, {})
            entry["column"] = done_column
            entry["scope"] = scope_of(boards, path)
            # Presence is the flag, the value says when. A card already in `placed` is now
            # excluded from the resurrect guard and still blocks a re-add, which is exactly
            # the pair of properties an archive needs and a deletion does not.
            entry["archived"] = stamp
        save_ledger(ledger_path, ledger)
        print(f"{log['done']} {len(leaving)} card(s) archived · {len(written)} board(s) "
              "written · day files untouched")
        return 0

    # --- probe: what do the cards' own references say now? -----------------------
    if args.probe:
        placement = boards_status(cfg, per_board)
        findings = probe_cards(cfg, day_files, placement)
        target = cfg["probe"].get("propose_column") or cfg["board"]["done_column"]
        proposals, advisory, unresolved = [], [], []

        print(f"{log['probe']} probing {len(findings)} open card(s) with references")
        for finding in findings:
            terminal = [r for r in finding["refs"] if r.get("resolved") and r.get("terminal")]
            missing = [r for r in finding["refs"] if not r.get("resolved")]
            for ref in finding["refs"]:
                if ref.get("resolved"):
                    mark = log["proposal"] if ref["terminal"] else log["ok"]
                    print(f"   {mark} {finding['id']} → {ref['ref']} {ref['state']}"
                          f" ({ref['kind']}, {ref['binding']}-bound)")
                else:
                    print(f"   {log['unresolved']} {finding['id']} → {ref['ref']}: {ref['detail']}")
            item_bound = [r for r in terminal if r["binding"] == "item"]
            if item_bound:
                proposals.append((finding, item_bound))
            elif terminal:
                # Terminal, but the reference came from the section's shared context
                # rather than the item itself — it may be cited as background, not as the
                # thing that closes the card. Surface it; do not propose on it.
                advisory.append((finding, terminal))
            unresolved.extend(missing)

        print()
        if not proposals:
            print(f"{log['ok']} no proposals — every item-bound reference is still open")
        else:
            print(f"{log['proposal']} {len(proposals)} proposal(s) — nothing has been moved:")
            for finding, terminal in proposals:
                why = ", ".join(f"{r['ref']} {r['state']}" for r in terminal)
                print(f"   {finding['id']}  [{finding['column']}] → {target}")
                print(f"      {finding['text'][:76]}")
                print(f"      because: {why}")
            args_line = " ".join(f'--move {f["id"]}="{target}"' for f, _ in proposals)
            print(f"\n   apply with:\n   sync_board.py {args_line}")
        if advisory:
            print(f"\n{log['warn']} {len(advisory)} card(s) cite a FINISHED reference in their "
                  "section context, not in the item itself — review, do not assume:")
            for finding, terminal in advisory:
                why = ", ".join(f"{r['ref']} {r['state']}" for r in terminal)
                print(f"   {finding['id']}  [{finding['column']}]  {why}")
        if unresolved:
            print(f"\n{log['unresolved']} {len(unresolved)} reference(s) could not be resolved "
                  "(offline, unauthenticated, or moved) — treat as unknown, not as done")
        return 0

    # --- refresh: day-file text → existing cards, placement preserved ------------
    # Text is the day file's field, so a correction there must reach the board. Placement
    # is the board's, so it is read back and re-applied rather than recomputed.
    if args.refresh:
        placement = boards_status(cfg, per_board)
        rendered: dict[str, str] = {}
        for path in day_files:
            items, _ = parse_day_file(cfg, path, path.stem, mutate=False)
            for item in items:
                if item.item_id in placement:
                    rendered[item.item_id] = render_card(cfg, item)

        changed = 0
        # Placement is preserved per board as well as per column: a refresh re-renders a
        # card's FACE, and which file holds it is not part of the face. A card whose scope
        # changed therefore stays put here and is reported by --migrate instead.
        touched: set[Path] = set()
        for board_path in board_order(boards):
            for column, cards in per_board.get(board_path, {}).items():
                for index, card in enumerate(list(cards)):
                    ids = [m.group(1) for m in id_pattern(cfg["ids"]["prefix"]).finditer(card)]
                    if len(ids) != 1 or ids[0] not in rendered:
                        continue
                    fresh = rendered[ids[0]]
                    # The checkbox belongs to the board, so keep whatever it says today.
                    if card.lstrip().startswith("- [x]"):
                        fresh = re.sub(r"^- \[ \]", "- [x]", fresh, count=1)
                    # So does an Obsidian block id (^abc123): the owner created it by copying
                    # a link to that card, and re-rendering must not break the link.
                    anchor = card_anchor(card)
                    if anchor and anchor not in fresh:
                        head, sep, tail = fresh.partition("\n")
                        fresh = f"{head} {anchor}{sep}{tail}"
                    if fresh != card:
                        cards[index] = fresh
                        changed += 1
                        touched.add(board_path)
                        print(f"   {log['refreshed']} {ids[0]} re-rendered in {column}")

        if args.dry_run:
            print(f"{log['dry_run']} dry run — would re-render {changed} card(s)")
            return 0
        write_boards(cfg, sorted(touched), per_board)
        print(f"{log['done']} {changed} card(s) re-rendered · placement preserved")
        return 0

    # --- reconcile: board → day files, status only -------------------------------
    if args.reconcile:
        status = boards_status(cfg, per_board)
        total = 0
        for path in day_files:
            for item_id, column, before, after in reconcile_day_file(
                cfg, path, status, mutate=not args.dry_run
            ):
                total += 1
                print(f"   {log['reconciled']} {item_id} → {column}")
                print(f"       - {before}\n       + {after}")
        if not args.dry_run:
            for item_id, column in status.items():
                ledger["placed"].setdefault(item_id, {})["column"] = column
            save_ledger(ledger_path, ledger)
        verb = "would update" if args.dry_run else "updated"
        print(f"{log['done']} {verb} {total} item line(s) across {len(day_files)} day file(s)")
        return 0

    # --- rebuild: discard the board, re-place from day files ---------------------
    if args.rebuild:
        print(f"   {log['rebuild']} rebuilding — existing card placement is discarded")
        per_board, on_board, where = {boards[default_scope]: {}}, set(), {}

    # --- sync: day files → boards, additive -------------------------------------
    # A card is skipped if it is already on a board OR was ever placed (the ledger), so
    # deleting a card from the board keeps it deleted instead of resurrecting it.
    known = on_board | (set() if args.rebuild else set(ledger["placed"]))
    added: list[Item] = []
    stamped: list[str] = []
    seen: dict[str, Item] = {}
    # Archived, not deleted. Both are ids in `placed` that no board holds, and both are
    # correctly refused a re-add above — but only one of them was the owner throwing the card
    # away. Without this the "deleted stay deleted" count would swallow every archived card.
    archived = set() if args.rebuild else archived_ids(ledger)
    resurrect_guard = (
        sorted(set(ledger["placed"]) - on_board - archived) if not args.rebuild else []
    )
    split = len(board_order(boards)) > 1

    for path in day_files:
        items, rewritten = parse_day_file(cfg, path, path.stem, mutate=not args.dry_run)
        if rewritten:
            stamped.append(path.name)
        for item in items:
            seen[item.item_id] = item
            if item.item_id in known:
                continue
            # The one placement site. `board_for` is total and single-valued, so this
            # appends each card to exactly one board — the partition is built here rather
            # than checked afterwards.
            target = board_for(boards, default_scope, item.scope)
            per_board.setdefault(target, {}).setdefault(
                lane_for(cfg, item.marker, item.done), []
            ).append(render_card(cfg, item))
            known.add(item.item_id)
            added.append(item)

    targets = boards_to_write(boards, default_scope, per_board)
    seed_columns(cfg, per_board, targets)

    prefix = log["dry_run"] if args.dry_run else log["added"]
    for item in added:
        icons = "".join(item.icons)
        lane = lane_for(cfg, item.marker, item.done)
        # Which board a card landed on is only worth saying when there is more than one.
        onto = f" ⇢ {item.scope or default_scope}" if split else ""
        print(f"   {prefix} [{lane}]{onto} {item.item_id} {icons} {item.text[:64]}")
    if resurrect_guard:
        print(f"   {log['deleted']} {len(resurrect_guard)} card(s) deleted from the board stay deleted")
    if archived:
        print(f"   {log['archived']} {len(archived)} card(s) archived off the board stay off "
              "— the day files hold them")

    # Detected, never applied: a scope change moves cards between files, which is a status
    # write. Sync's job is to add what is new, so it says what it found and stops.
    misplaced, _ = placement_drift(seen, boards, default_scope, where, ledger)
    if misplaced:
        print(f"   {log['warn']} {len(misplaced)} card(s) now render to a different board — "
              "`--migrate` to see them; nothing has been moved")

    if args.dry_run:
        print(f"{log['dry_run']} dry run — would add {len(added)} card(s); nothing written")
        return 0

    write_boards(cfg, targets, per_board)

    for item in added:
        ledger["placed"][item.item_id] = {
            "day": item.date,
            "column": lane_for(cfg, item.marker, item.done),
            "scope": scope_of(boards, board_for(boards, default_scope, item.scope)),
            "since": datetime.now().astimezone().date().isoformat(),
        }
    save_ledger(ledger_path, ledger)

    if stamped:
        print(f"   {log['stamped']} ids stamped into: {', '.join(stamped)}")
    print(f"{log['done']} {len(added)} card(s) added · {len(ledger['placed'])} tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
