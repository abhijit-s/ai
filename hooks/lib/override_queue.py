"""
Per-session override queue for SubagentStart hooks.

Background
----------
Claude Code fires two distinct events around every sub-agent spawn:

* ``PreToolUse`` (with ``tool_name == "Agent"``) — carries the full spawn
  prompt at ``tool_input.prompt`` and a ``tool_use_id`` like
  ``toolu_01...``.
* ``SubagentStart`` — carries an ``agent_id`` (different ID space, hex)
  but *no* prompt. The two IDs cannot be correlated directly.

The override-comment mechanisms (``<!-- tools: ... -->`` and
``<!-- inject: ... -->``) live inside the spawn prompt, which means the
``SubagentStart`` hook cannot see them. This module bridges that gap by
having a ``PreToolUse:Task`` hook stash any parsed overrides into a
per-session FIFO (First-In-First-Out) queue, which the ``SubagentStart``
hooks then consume.

Correlation strategy
--------------------
The queue is keyed by ``(session_id, agent_type)``. Events fire in spawn
order on a given session, so the earliest queued entry for a given
``agent_type`` matches the earliest ``SubagentStart`` for that type.

DOCUMENTED LIMITATION: two parallel same-type spawns with different
overrides may get matched out of order in the race window. This is
acceptable for v1.

Storage
-------
One file per session at ``~/.claude/cache/subagent-overrides/<session_id>.jsonl``.
Append-only JSONL (JSON Lines), one entry per spawn with overrides.

Entry schema::

    {"ts": "<ISO-8601>", "agent_type": "...", "tool_use_id": "...",
     "tools": [...], "inject": [...]}

``tools`` and ``inject`` are lists (possibly empty if only one override
comment was present).

TTL (Time To Live)
------------------
Entries older than ``TTL_SECONDS`` (30 min) are dropped on every write.
Bounded growth — no separate sweeper needed.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Iterable


TTL_SECONDS = 30 * 60  # 30 minutes


def _overrides_dir() -> str:
    return os.path.join(
        os.path.expanduser("~"), ".claude", "cache", "subagent-overrides"
    )


def _session_file(session_id: str) -> str:
    # Defensive: strip any path separators a malformed session_id might carry.
    safe = session_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_overrides_dir(), f"{safe}.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> float:
    """Parse an ISO-8601 ``Z`` timestamp into epoch seconds. Returns 0.0 on failure."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _read_entries(path: str) -> list[dict]:
    """Read entries from a JSONL file. Malformed lines are skipped silently."""
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except (OSError, PermissionError):
        return []
    return out


def _drop_stale(entries: Iterable[dict], now: float | None = None) -> list[dict]:
    """Return entries newer than ``TTL_SECONDS`` ago."""
    now = now if now is not None else time.time()
    cutoff = now - TTL_SECONDS
    return [e for e in entries if _parse_ts(e.get("ts", "")) >= cutoff]


def _atomic_write(path: str, entries: Iterable[dict]) -> None:
    """Atomically rewrite ``path`` with the given entries (tmp + rename)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".tmp-", suffix=".jsonl"
    )
    try:
        with os.fdopen(fd, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def append_override(
    session_id: str,
    agent_type: str,
    tool_use_id: str,
    tools: list[str],
    inject: list[str],
) -> None:
    """Append a new override entry to the per-session queue.

    Drops stale entries (older than TTL_SECONDS) in the same write to
    keep the file bounded. Always succeeds silently or raises — callers
    should swallow exceptions if they want a best-effort stash.
    """
    path = _session_file(session_id)
    existing = _drop_stale(_read_entries(path))
    existing.append(
        {
            "ts": _now_iso(),
            "agent_type": agent_type,
            "tool_use_id": tool_use_id,
            "tools": list(tools),
            "inject": list(inject),
        }
    )
    _atomic_write(path, existing)


def consume_override(session_id: str, agent_type: str, field: str) -> list[str] | None:
    """Consume the earliest queue entry matching ``agent_type`` whose ``field`` is non-empty.

    ``field`` is either ``"tools"`` or ``"inject"``.

    Behaviour:

    * Returns the consumed list if found, else ``None``.
    * The matched entry is mutated in place to clear ``field`` (so the
      other hook can't double-consume it).
    * If the resulting entry has BOTH ``tools`` and ``inject`` empty, the
      entry is dropped entirely from the file.
    * The file is atomically rewritten on every consume.
    * Missing file, malformed file, or no matching entry → returns
      ``None`` and leaves the file untouched.
    """
    if field not in ("tools", "inject"):
        return None
    path = _session_file(session_id)
    if not os.path.exists(path):
        return None

    entries = _drop_stale(_read_entries(path))
    if not entries:
        # File present but empty/stale — write back the cleaned (empty) state
        # so subsequent reads stay fast. Skip if the file was already empty.
        try:
            _atomic_write(path, entries)
        except OSError:
            pass
        return None

    consumed: list[str] | None = None
    out: list[dict] = []
    for entry in entries:
        if (
            consumed is None
            and entry.get("agent_type") == agent_type
            and entry.get(field)
        ):
            consumed = list(entry.get(field) or [])
            # Clear this field; drop the entry entirely if both fields empty.
            entry[field] = []
            other = "inject" if field == "tools" else "tools"
            if not entry.get(other):
                continue  # drop entry
        out.append(entry)

    if consumed is None:
        return None

    try:
        _atomic_write(path, out)
    except OSError:
        # Best-effort: even if rewrite fails, we still return the consumed
        # value. Worst case: the entry gets re-consumed on the next spawn,
        # which mirrors the documented FIFO race-window limitation.
        pass
    return consumed
