#!/usr/bin/env python3
"""Sync per-day work-register files into an Obsidian Kanban board.

Day files are the append-only source of intent; the board owns live status. The sync is
purely additive — a card whose id is already on the board is never moved or rewritten —
so dragging cards between columns survives every subsequent run.

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
    sync_board.py --dry-run --show-config --since YYYY-MM-DD --register NAME --config PATH
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
from datetime import date as _date, datetime
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
    "card": {
        "template": "- [{check}] {marker}{icons}{text}\n\t\n\t{meta}\n\t{id_comment}",
        "meta": "\U0001f4c5 [[{date}]] · \U0001f9ed {group}{track}{tags}",
        "icon_separator": "",
        "icon_suffix": " ",
        "max_icons": 3,
        # Collapsing to nothing when there is no track is grammar; the emoji and the
        # separator that lead the segment are vocabulary, so both live in one format
        # string a corpus can restyle without touching code.
        "track_format": " · \U0001f9f5 {track}",
        "track_tag": "#track/{track}",
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
        "ok": "✅",
        "warn": "⚠️",
        "unresolved": "❔",
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


def register_paths(cfg: dict, register: dict) -> tuple[Path, Path]:
    data_root = register.get("data_root")
    if not data_root:
        raise SystemExit("work-register: register declares no data_root")
    root = Path(os.environ.get("WORK_REGISTER_ROOT", data_root)).expanduser()
    register_dir = register.get("register_dir", cfg["paths"]["register_dir"])
    board = register.get("board", cfg["paths"]["board"])
    return root / register_dir, root / board


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
    all_tags = item.tags + ([track_tag] if track_tag else [])
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


def load_ledger(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "placed": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("placed", {})
    return data


def save_ledger(path: Path, ledger: dict) -> None:
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def board_status(cfg: dict, columns: dict[str, list[str]]) -> dict[str, str]:
    """id → the column it currently sits in. The board's answer to 'where is this?'"""
    finder = id_pattern(cfg["ids"]["prefix"])
    return {
        m.group(1): column
        for column, cards in columns.items()
        for card in cards
        for m in finder.finditer(card)
    }


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


def move_card(cfg: dict, columns: dict[str, list[str]], item_id: str, target: str) -> str:
    """Relocate one card between columns. Returns the column it came from."""
    finder = id_pattern(cfg["ids"]["prefix"])
    card = origin = None
    for column, cards in columns.items():
        for candidate in list(cards):
            if any(m.group(1) == item_id for m in finder.finditer(candidate)):
                card, origin = candidate, column
                cards.remove(candidate)
                break
        if card:
            break
    if card is None:
        raise SystemExit(f"work-register: no card with id {item_id} on the board")

    done = cfg["board"]["done_column"]
    # The checkbox is part of status, so crossing the Done boundary ticks or unticks it.
    if target == done:
        card = re.sub(r"^- \[ \]", "- [x]", card, count=1)
    elif origin == done:
        card = re.sub(r"^- \[[xX]\]", "- [ ]", card, count=1)

    columns.setdefault(target, []).append(card)
    return origin



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

    ordered = list(cfg["board"]["column_order"])
    ordered += [name for name in columns if name not in ordered]
    for name in ordered:
        out += [f"## {name}", ""]
        out += columns.get(name, [])
        out.append("")

    out += ["", "%% kanban:settings", "```", cfg["kanban"]["settings"], "```", "%%"]
    return "\n".join(out) + "\n"


# --- Entry point ------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", help="named register to sync (default: default_register)")
    parser.add_argument("--config", help="extra config layer, applied last")
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
    args = parser.parse_args()

    layers = load_config(args.config)
    name, register = resolve_register(layers.cfg, args.register)

    # Phase 2 — the corpus at data_root governs its own conventions.
    root = Path(os.environ.get("WORK_REGISTER_ROOT", register.get("data_root", "."))).expanduser()
    corpus_marker = root / MARKER_NAME
    if corpus_marker.is_file():
        layers.apply(corpus_marker)
        name, register = resolve_register(layers.cfg, args.register or name)

    cfg = deep_merge(layers.cfg, register.get("overrides", {}))
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
        return 0

    register_dir, board_path = register_paths(cfg, register)

    if not register_dir.is_dir():
        raise SystemExit(f"work-register: register dir not found: {register_dir}")

    day_files = sorted(p for p in register_dir.iterdir() if DAY_FILE.match(p.name))
    if args.since:
        day_files = [p for p in day_files if p.stem >= args.since]
    if not day_files:
        print(f"{log['empty']} work-register [{name}]: no day files to ingest")
        return 0

    ledger_path = register_dir / LEDGER_NAME
    ledger = load_ledger(ledger_path)
    columns, on_board = parse_board(cfg, board_path)
    # --brief exists to be consumed by a hook, so it must emit exactly one line.
    if not (args.status and args.brief):
        print(f"{log['register']} register: {name}  {log['board']} board: {board_path}")

    # --- move: relocate cards by id (the /-command surface) ----------------------
    if args.move:
        moved = []
        for spec in args.move:
            if "=" not in spec:
                raise SystemExit(f"work-register: --move expects ID=COLUMN, got {spec!r}")
            item_id, wanted = spec.split("=", 1)
            target = resolve_column(cfg, columns, wanted)
            origin = move_card(cfg, columns, item_id.strip(), target)
            moved.append((item_id.strip(), origin, target))
            print(f"   {log['moved']} {item_id.strip()}: {origin} → {target}")
        for column in cfg["board"]["column_order"]:
            columns.setdefault(column, [])
        if args.dry_run:
            print(f"{log['dry_run']} dry run — {len(moved)} move(s) not written")
            return 0
        board_path.write_text(render_board(cfg, columns), encoding="utf-8")
        for item_id, _, target in moved:
            entry = ledger["placed"].setdefault(item_id, {})
            entry["column"] = target
            entry["since"] = datetime.now().astimezone().date().isoformat()
        save_ledger(ledger_path, ledger)
        # A move IS a status change, so carry it straight back to the day files.
        status = board_status(cfg, columns)
        touched = sum(
            len(reconcile_day_file(cfg, path, status, mutate=True)) for path in day_files
        )
        print(f"{log['done']} {len(moved)} move(s) · {touched} day-file line(s) reconciled")
        return 0

    # --- status: is the register still telling the truth? ------------------------
    if args.status:
        placement = board_status(cfg, columns)
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
            entry = ledger["placed"].get(item_id, {})
            age = days_since(entry.get("since") or entry.get("day") or item_id[:8] and
                             f"{item_id[:4]}-{item_id[4:6]}-{item_id[6:8]}")
            if age is not None and age >= cfg["status"]["stale_days"]:
                stale.append((age, item_id, column))
        stale.sort(reverse=True)

        problems = []
        if capture_age is not None and capture_age >= cfg["status"]["capture_gap_days"]:
            problems.append(f"last capture {capture_age}d ago")
        if stale:
            problems.append(f"{len(stale)} card(s) stale >{cfg['status']['stale_days']}d")
        verdict = (
            f"{log['warn']} work-register [{name}]: " + " · ".join(problems)
            if problems else f"{log['ok']} work-register [{name}]: current"
        )
        print(verdict)
        if args.brief:
            return 0
        counts = {c: len(v) for c, v in columns.items() if v}
        print("   " + " · ".join(f"{c} {n}" for c, n in counts.items()))
        for age, item_id, column in stale[:10]:
            print(f"   {log['warn']} {item_id} — {age}d in {column}")
        return 0

    # --- probe: what do the cards' own references say now? -----------------------
    if args.probe:
        placement = board_status(cfg, columns)
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
        placement = board_status(cfg, columns)
        rendered: dict[str, str] = {}
        for path in day_files:
            items, _ = parse_day_file(cfg, path, path.stem, mutate=False)
            for item in items:
                if item.item_id in placement:
                    rendered[item.item_id] = render_card(cfg, item)

        changed = 0
        for column, cards in columns.items():
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
                anchor = re.match(r"^[^\n]*?\s(\^[A-Za-z0-9-]+)\s*$", card.split("\n")[0])
                if anchor and anchor.group(1) not in fresh:
                    head, sep, tail = fresh.partition("\n")
                    fresh = f"{head} {anchor.group(1)}{sep}{tail}"
                if fresh != card:
                    cards[index] = fresh
                    changed += 1
                    print(f"   {log['refreshed']} {ids[0]} re-rendered in {column}")

        if args.dry_run:
            print(f"{log['dry_run']} dry run — would re-render {changed} card(s)")
            return 0
        if changed:
            board_path.write_text(render_board(cfg, columns), encoding="utf-8")
        print(f"{log['done']} {changed} card(s) re-rendered · placement preserved")
        return 0

    # --- reconcile: board → day files, status only -------------------------------
    if args.reconcile:
        status = board_status(cfg, columns)
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
        columns, on_board = {}, set()

    # --- sync: day files → board, additive --------------------------------------
    # A card is skipped if it is on the board OR was ever placed (the ledger), so
    # deleting a card from the board keeps it deleted instead of resurrecting it.
    known = on_board | (set() if args.rebuild else set(ledger["placed"]))
    added: list[Item] = []
    stamped: list[str] = []
    resurrect_guard = sorted(set(ledger["placed"]) - on_board) if not args.rebuild else []

    for path in day_files:
        items, rewritten = parse_day_file(cfg, path, path.stem, mutate=not args.dry_run)
        if rewritten:
            stamped.append(path.name)
        for item in items:
            if item.item_id in known:
                continue
            columns.setdefault(lane_for(cfg, item.marker, item.done), []).append(
                render_card(cfg, item)
            )
            known.add(item.item_id)
            added.append(item)

    for column in cfg["board"]["column_order"]:
        columns.setdefault(column, [])

    prefix = log["dry_run"] if args.dry_run else log["added"]
    for item in added:
        icons = "".join(item.icons)
        lane = lane_for(cfg, item.marker, item.done)
        print(f"   {prefix} [{lane}] {item.item_id} {icons} {item.text[:64]}")
    if resurrect_guard:
        print(f"   {log['deleted']} {len(resurrect_guard)} card(s) deleted from the board stay deleted")

    if args.dry_run:
        print(f"{log['dry_run']} dry run — would add {len(added)} card(s); nothing written")
        return 0

    board_path.parent.mkdir(parents=True, exist_ok=True)
    board_path.write_text(render_board(cfg, columns), encoding="utf-8")

    for item in added:
        ledger["placed"][item.item_id] = {
            "day": item.date,
            "column": lane_for(cfg, item.marker, item.done),
            "since": datetime.now().astimezone().date().isoformat(),
        }
    save_ledger(ledger_path, ledger)

    if stamped:
        print(f"   {log['stamped']} ids stamped into: {', '.join(stamped)}")
    print(f"{log['done']} {len(added)} card(s) added · {len(ledger['placed'])} tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
