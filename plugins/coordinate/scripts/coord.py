#!/usr/bin/env python3
"""coord — a deterministic arbiter for the COORD coordination protocol.

The `coordinate` skill defines an ADVISORY message-passing protocol for
concurrent Claude sessions contending over a shared mutable resource
(a git tree, a dev database, a deploy lane, the WARP tunnel...). Pure
message-passing is deterministic in the human's head but not in code:
two sessions can each *believe* they hold. This tool extracts the
protocol's structural semantics into a file-backed shared ledger so the
outcome of any contention is a pure function of ledger state, serialized
by a single `flock`.

It stays faithful to the skill's advisory nature: the ledger is a
coordination aid, not a lock server that can stop a non-participant. The
real last line of defense remains a live resource probe before mutating
(`coord probe`), which is why `warp` carries an in-cluster probe command.

Stdlib only. Every field/verb maps 1:1 to the skill. See tests/ for the
executable spec of the deterministic core.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

# --- Paths & config -------------------------------------------------------
# The ledger is shared across every session on the machine, so it lives at a
# fixed, predictable path — not under a per-user $TMPDIR that two sessions
# might resolve differently. COORD_DIR overrides for tests and odd setups.
COORD_DIR = Path(os.environ.get("COORD_DIR", "/tmp/cc-coord"))
LEDGER_PATH = COORD_DIR / "ledger.json"
LOCK_PATH = COORD_DIR / "ledger.lock"

# Resource registry is CONFIG, not code — the engine is generic; `warp` is one
# row. Resolved relative to this file so it travels with the plugin; override
# with COORD_RESOURCES.
DEFAULT_RESOURCES = Path(__file__).resolve().parent.parent / "config" / "resources.toml"
RESOURCES_PATH = Path(os.environ.get("COORD_RESOURCES", str(DEFAULT_RESOURCES)))

DEFAULT_HOLD = "10m"  # skill default lease TTL when hold= omitted

PRIO_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
VALID_PRIOS = tuple(PRIO_RANK)

# Exit codes — scriptable so a skill/hook can branch on the outcome.
EXIT_OK = 0
EXIT_QUEUED = 10       # a claim/request did not take the resource; caller waits
EXIT_PROBE_FAIL = 11   # a reachability probe failed
EXIT_USER_ERR = 2


# --- Duration parsing -----------------------------------------------------
_DUR_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)
_DUR_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 60}  # bare number = minutes


def parse_duration(text: str) -> int:
    """'15m'->900, '30s'->30, '2h'->7200, '5'->300 (bare = minutes)."""
    if text is None:
        raise ValueError("empty duration")
    m = _DUR_RE.match(str(text))
    if not m:
        raise ValueError(f"bad duration: {text!r} (use 30s / 15m / 2h / 1d)")
    return int(m.group(1)) * _DUR_UNIT[m.group(2).lower()]


def fmt_duration(secs: int) -> str:
    secs = int(round(secs))
    if secs < 0:
        secs = 0
    if secs % 3600 == 0 and secs >= 3600:
        return f"{secs // 3600}h"
    if secs % 60 == 0 and secs >= 60:
        return f"{secs // 60}m"
    return f"{secs}s"


# --- Deterministic core (pure functions — the tested heart) ---------------
def precedence_key(waiter: dict) -> tuple:
    """Total order over queued waiters. Lower tuple == wins the next window.

    Reconciles the skill's two anti-starvation statements:
      * "P0 preempts everything"  -> P0 always sorts first (component 0).
      * a preempted/denied waiter "holds a reservation for the next free
        window, ahead of any new same-or-lower-priority CLAIM", and "a
        long-suffering P3 is not endlessly jumped by fresh P2s" -> a
        `reserved` waiter (one that already survived a free window) sorts
        ahead of NON-reserved claims of ANY priority except P0 (component
        1). This is the next-free-window guarantee: each time the resource
        frees, the reserved waiter is first in line, so with bounded leases
        it cannot be starved.

    Then, among equals, higher priority; then the ledger's monotonic `seq`
    (message-delivery order — skew-free, never a wall clock); then lexical
    id as the final deterministic tiebreak.
    """
    prio = waiter.get("prio", "P3")
    return (
        0 if prio == "P0" else 1,
        0 if waiter.get("reserved") else 1,
        PRIO_RANK.get(prio, 3),
        int(waiter.get("seq", 0)),
        str(waiter.get("id", "")),
    )


def resolve_next(queue: list[dict]) -> dict | None:
    """The waiter that should take the resource at the next free window."""
    return min(queue, key=precedence_key) if queue else None


def should_preempt(holder_prio: str, requester_prio: str) -> bool:
    """A REQUEST of STRICTLY higher priority than the hold must be honored;
    equal or lower waits. Equal priority (even P0 vs P0) does NOT preempt."""
    return PRIO_RANK.get(requester_prio, 3) < PRIO_RANK.get(holder_prio, 3)


def is_expired(hold: dict, now: float) -> bool:
    """A lease auto-lapses at expiry — the escape hatch for a crashed holder."""
    return hold is not None and float(hold.get("expires_at", 0)) <= now


# --- Liveness: reap a dead holder EARLY, without waiting out the TTL --------
#
# A holder is a stateless CLI invocation whose own pid dies instantly, so it
# cannot vouch for its own liveness. It hands over a token tied to the SESSION's
# lifetime — the session messaging socket (`$CLAUDE_CODE_MESSAGING_SOCKET`,
# /tmp/cc-socks/<pid>.sock). Liveness of that AF_UNIX socket is NOT file
# existence: bind() creates the inode and only an explicit unlink() removes it,
# so a `kill -9` / OOM / crash leaves the .sock file behind. The authoritative
# probe is `connect()`:
#   * connect succeeds        -> a listener answered -> ALIVE
#   * connect ECONNREFUSED    -> inode present, no listener -> DEFINITELY DEAD
#   * anything else (ENOENT, EACCES, timeout, unexpected errno) -> UNKNOWN
#
# FAIL-SAFE DIRECTION (asymmetric costs): only ever reap on a POSITIVE dead
# signal. A false DEAD reaps a live holder's lease and lets two sessions hold
# one resource — the exact catastrophe this tool prevents. A false LIVE merely
# delays reaping to the TTL backstop — safe. So every ambiguity fails toward
# ALIVE. `kill -0` is a cheap NEGATIVE prefilter only: ESRCH proves the pid is
# gone (definitely dead); success is inconclusive (pid reuse is fast on macOS),
# so it never proves life — connect() remains the authority.

def parse_token(spec: str | None) -> dict | None:
    """Parse a caller-supplied liveness token into {type, value[, pid]}.
    Forms: 'pid:1234' | 'sock:/path' | a bare integer (pid) | a bare path."""
    if not spec:
        return None
    if spec.startswith("pid:"):
        return {"type": "pid", "value": spec[4:]}
    if spec.startswith("sock:"):
        spec = spec[5:]
    elif spec.isdigit():
        return {"type": "pid", "value": spec}
    tok = {"type": "socket", "value": spec}
    pid = _pid_from_sock(spec)
    if pid is not None:
        tok["pid"] = pid  # enables the cheap ESRCH prefilter
    return tok


def _pid_from_sock(path: str) -> int | None:
    m = re.match(r"^(\d+)\.sock$", os.path.basename(str(path)))
    return int(m.group(1)) if m else None


def _kill0_dead(pid) -> bool:
    """True IFF the pid definitely does not exist (ESRCH). Never returns a
    positive liveness claim — a reused pid would look alive."""
    try:
        os.kill(int(pid), 0)
        return False              # exists — inconclusive, not proof of life
    except ProcessLookupError:
        return True               # ESRCH — definitely gone
    except PermissionError:
        return False              # exists, not ours to signal
    except (ValueError, OverflowError, TypeError):
        return False


def _connect_once(path: str, timeout: float) -> str | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(str(path))
        return None                       # listener answered -> ALIVE
    except ConnectionRefusedError:
        return "socket refused (no listener)"   # ECONNREFUSED -> candidate DEAD
    except OSError:
        return None                       # ENOENT/EACCES/timeout/... -> fail toward ALIVE
    finally:
        s.close()


def _connect_dead(path: str, timeout: float = 0.5) -> str | None:
    """Connect-probe an AF_UNIX path. Return a reason string IFF definitely dead
    (ECONNREFUSED), else None (alive or unclassifiable — fail toward alive).

    A single ECONNREFUSED is confirmed with one retry: a live listener whose
    accept backlog is momentarily full also refuses, and a false DEAD (reaping
    a live holder) is catastrophic — so a transient refusal must clear on retry
    to count. A genuinely dead socket refuses every time."""
    first = _connect_once(path, timeout)
    if first is None:
        return None
    time.sleep(0.05)
    return _connect_once(path, timeout)   # dead only if it refuses again


def enrich_token(token: dict | None) -> dict | None:
    """Stamp a socket token with its inode identity at CLAIM time. The socket
    path is named by pid (/tmp/cc-socks/<pid>.sock), so pid-reuse can bind a NEW
    session's listener to the SAME path — connect() alone would then report the
    long-gone original holder LIVE. The inode is a session-unique NONCE that IS
    verifiable at probe time (stat): a live process keeps its bound inode, while
    a recycled-pid session must unlink + rebind, minting a new inode. Recording
    it upgrades the probe from 'is A process at this path alive' to 'is THIS
    holder alive'. Best-effort: if the socket can't be stat'd, the token stays
    weak (connect-only, pid-reuse blind) and is surfaced as such."""
    if not token or token.get("type") != "socket" or not token.get("value"):
        return token
    try:
        st = os.stat(token["value"])
        token["ino"] = st.st_ino
        token["ctime"] = st.st_ctime
    except OSError:
        pass  # can't stat now -> remain weak; fail-safe still holds
    return token


def dead_reason(token: dict | None) -> str | None:
    """A reason string IFF the token's session is DEFINITELY dead; else None.
    Only a positive-dead signal reaps; every ambiguity fails toward alive."""
    if not token:
        return None
    if token.get("type") == "pid":
        return "pid gone (ESRCH)" if _kill0_dead(token.get("value")) else None
    val = token.get("value")
    pid = token.get("pid")
    if pid is None:
        pid = _pid_from_sock(val) if val else None
    if pid is not None and _kill0_dead(pid):
        return "pid gone (ESRCH)"         # authoritative negative — no listener possible
    if not val:
        return None
    # Nonce check: if we recorded the socket's inode at claim, a differing inode
    # now means the socket was recreated (pid reuse) -> the ORIGINAL holder is
    # gone. A live holder never changes its bound inode, so a mismatch is a
    # POSITIVE dead signal, safe to reap on even if connect() succeeds.
    recorded_ino = token.get("ino")
    if recorded_ino is not None:
        try:
            if os.stat(val).st_ino != recorded_ino:
                return "socket recreated (pid-reuse; nonce inode mismatch) -> original holder gone"
        except OSError:
            pass  # can't stat -> fall through to connect (fail toward alive)
    return _connect_dead(val)             # connect() is the authority


def reap_reason(hold: dict | None, now: float) -> str | None:
    """Why a hold should be reaped, or None if it stands. Liveness reaps a dead
    holder EARLY; the TTL is the backstop for a hold with no token (or a token
    that outlives its session)."""
    if hold is None:
        return None
    d = dead_reason(hold.get("token"))
    if d:
        return f"holder dead: {d} -> reaped"
    if is_expired(hold, now):
        return "lease lapsed (TTL)"
    return None


def reaper_note(hold: dict | None) -> str:
    """How this hold will be reaped — surfaced in query/status so a holder that
    is un-probeable (anonymous) or only weakly probeable (pid-reuse blind) is
    VISIBLE, not silently un-reapable."""
    if hold is None:
        return "-"
    tok = hold.get("token")
    if not tok:
        return "ttl-only (no liveness token)"
    if tok.get("type") == "pid":
        return f"liveness (pid {tok.get('value')}, ESRCH) + ttl"
    if tok.get("ino") is not None:
        return "liveness (connect + inode-nonce verified) + ttl"
    return "liveness (pid-path, weak: pid-reuse blind) + ttl"


# --- Ledger (file-backed, flock-serialized) -------------------------------
def _empty_state() -> dict:
    return {"version": 1, "seq": 0, "resources": {}}


def _empty_resource() -> dict:
    return {"holder": None, "queue": [], "standing": []}


class Ledger:
    """Read-modify-write the shared ledger under an exclusive flock so
    concurrent sessions serialize and can never interleave a corrupt write.
    Use as a context manager: the lock is held for the whole transaction."""

    def __init__(self, path: Path = LEDGER_PATH, lock: Path = LOCK_PATH):
        self.path = Path(path)
        self.lock_path = Path(lock)
        self._fh = None
        self.state: dict = _empty_state()

    def __enter__(self) -> "Ledger":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A dedicated lock file, never the data file: os.replace() swaps the
        # data file's inode, which would drop an flock held on the data fd.
        self._fh = open(self.lock_path, "a+")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        self.state = self._load()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._save()
        finally:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        return False

    def _load(self) -> dict:
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return _empty_state()
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            # A truncated/garbage ledger must never wedge coordination. Reset;
            # bounded leases mean stale state was going to lapse anyway.
            return _empty_state()
        state.setdefault("version", 1)
        state.setdefault("seq", 0)
        state.setdefault("resources", {})
        return state

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True))
        os.replace(tmp, self.path)  # atomic on POSIX

    # -- helpers ----------------------------------------------------------
    def _res(self, name: str) -> dict:
        return self.state["resources"].setdefault(name, _empty_resource())

    def _next_seq(self) -> int:
        self.state["seq"] = int(self.state.get("seq", 0)) + 1
        return self.state["seq"]

    def _expire(self, res: dict, now: float) -> dict | None:
        """Reap the holder if it should be reaped — liveness (dead session,
        early) OR the TTL backstop — and drop dead/lapsed queue reservations.
        Every read path (query/claim/status/sweep) calls this, so the reap
        happens at all four points. Returns the reaped holder (with a `reason`)
        for reporting, else None."""
        holder = res.get("holder")
        reason = reap_reason(holder, now)
        reaped = None
        if reason:
            reaped = dict(holder)
            reaped["reason"] = reason
            res["holder"] = None
        # A queued waiter's session can die too; reap on liveness or its own
        # request TTL so a dead waiter's reservation cannot linger.
        res["queue"] = [
            w for w in res["queue"]
            if not dead_reason(w.get("token")) and float(w.get("expires_at", 0)) > now
        ]
        return reaped

    def _promote(self, res: dict, now: float) -> dict | None:
        """Resource just freed: hand it to the rightful next waiter and mark
        everyone left behind as reserved (they survived a free window)."""
        winner = resolve_next(res["queue"])
        if winner is None:
            return None
        res["queue"] = [w for w in res["queue"] if w["id"] != winner["id"]]
        for w in res["queue"]:
            w["reserved"] = True
        res["holder"] = {
            "id": winner["id"],
            "session": winner.get("session"),
            "prio": winner.get("prio", "P3"),
            "for": winner.get("for"),
            "profile": winner.get("profile"),
            "next": winner.get("next"),
            "token": winner.get("token"),
            "granted_at": now,
            "expires_at": now + int(winner.get("hold_secs", parse_duration(DEFAULT_HOLD))),
            "hold_secs": int(winner.get("hold_secs", parse_duration(DEFAULT_HOLD))),
        }
        return res["holder"]

    def _make_holder(self, w: dict, now: float) -> dict:
        return {
            "id": w["id"],
            "session": w.get("session"),
            "prio": w.get("prio", "P3"),
            "for": w.get("for"),
            "profile": w.get("profile"),
            "next": w.get("next"),
            "token": w.get("token"),
            "granted_at": now,
            "expires_at": now + int(w["hold_secs"]),
            "hold_secs": int(w["hold_secs"]),
        }

    def _reap_and_promote(self, res: dict, now: float) -> dict | None:
        """Passive read paths (query/status/sweep) reap a dead/lapsed holder AND
        promote the next waiter, so the reported state is already correct and a
        dead holder never pins a resource that has a waiting successor."""
        reaped = self._expire(res, now)
        if res.get("holder") is None and res["queue"]:
            self._promote(res, now)
        return reaped

    # -- verbs ------------------------------------------------------------
    def acquire(self, name, cid, prio, hold_secs, session, purpose, profile, nxt,
                now=None, token=None):
        """CLAIM / REQUEST core. Returns a result dict describing the outcome."""
        now = time.time() if now is None else now
        res = self._res(name)
        lapsed = self._expire(res, now)

        # Renewal — same id (and session if known) re-holding: extend the lease.
        # But "each renewal re-enters the queue": a renewal must NOT let a holder
        # indefinitely extend over a strictly-higher-priority waiter, or it would
        # defeat preemption. If such a waiter is queued, refuse to extend (the
        # current lease stands so the holder can finish its atomic operation, but
        # it will lapse rather than grow — forcing a release the waiter can win).
        h = res.get("holder")
        if h and h["id"] == cid and (session is None or h.get("session") in (None, session)):
            blocker = resolve_next([w for w in res["queue"] if w["id"] != cid])
            if blocker is not None and should_preempt(prio, blocker["prio"]):
                return {"status": "queued", "reason": "preempt-owed", "renewed": False,
                        "holder": h, "preempt": True, "blocked_by": blocker,
                        "renew_refused": True, "lapsed": lapsed}
            h["expires_at"] = now + hold_secs
            h["hold_secs"] = hold_secs
            h["prio"] = prio
            if purpose:
                h["for"] = purpose
            if nxt:
                h["next"] = nxt
            if token is not None:
                h["token"] = token  # refresh the liveness token on renew
            return {"status": "granted", "renewed": True, "holder": h, "lapsed": lapsed}

        caller = {
            "id": cid, "session": session, "prio": prio,
            "for": purpose, "profile": profile, "next": nxt, "token": token,
            "hold_secs": hold_secs, "requested_at": now,
            "expires_at": now + hold_secs, "reserved": False,
        }

        if res.get("holder") is None:
            # Free — but a queued waiter may outrank a fresh claimer.
            others = [w for w in res["queue"] if w["id"] != cid]
            winner = resolve_next(others + [caller])
            if winner["id"] == cid:
                res["holder"] = self._make_holder(caller, now)
                res["queue"] = [w for w in others]
                for w in res["queue"]:
                    w["reserved"] = True  # passed over this free window
                return {"status": "granted", "renewed": False,
                        "holder": res["holder"], "lapsed": lapsed}
            # A better waiter exists; caller must queue behind it.
            self._enqueue(res, caller, reserved=False)
            return {"status": "queued", "reason": "yield-to-waiter",
                    "blocked_by": winner, "holder": None, "lapsed": lapsed}

        # Held by someone else — queue and report whether a yield is owed.
        self._enqueue(res, caller, reserved=False)
        holder = res["holder"]
        if should_preempt(holder["prio"], prio):
            return {"status": "queued", "reason": "preempt-owed",
                    "holder": holder, "preempt": True, "lapsed": lapsed}
        return {"status": "queued", "reason": "wait",
                "holder": holder, "preempt": False, "lapsed": lapsed}

    def _enqueue(self, res: dict, caller: dict, reserved: bool) -> None:
        existing = next((w for w in res["queue"] if w["id"] == caller["id"]), None)
        if existing:
            seq = existing["seq"]  # keep original position — no line-jumping on renew
            existing.update(caller)
            existing["seq"] = seq
            existing["reserved"] = existing.get("reserved") or reserved
        else:
            caller = dict(caller)
            caller["seq"] = self._next_seq()
            caller["reserved"] = reserved
            res["queue"].append(caller)

    def release(self, name, cid, session, now=None):
        now = time.time() if now is None else now
        res = self._res(name)
        self._expire(res, now)
        h = res.get("holder")
        freed = False
        if h and (h["id"] == cid or (session and h.get("session") == session)):
            res["holder"] = None
            freed = True
        # Also drop a matching queued claim (cancel a pending request).
        before = len(res["queue"])
        res["queue"] = [w for w in res["queue"]
                        if not (w["id"] == cid or (session and w.get("session") == session and cid is None))]
        dequeued = before - len(res["queue"])
        new_holder = self._promote(res, now) if freed else None
        return {"freed": freed, "dequeued": dequeued, "new_holder": new_holder}

    def release_session(self, session, now=None):
        """Release EVERY hold and pending claim for a session — used by the
        SessionEnd hook to close the exited-holder gap deterministically."""
        now = time.time() if now is None else now
        released = []
        for name, res in self.state["resources"].items():
            self._expire(res, now)
            touched = False
            if res.get("holder") and res["holder"].get("session") == session:
                res["holder"] = None
                touched = True
            before = len(res["queue"])
            res["queue"] = [w for w in res["queue"] if w.get("session") != session]
            if touched or len(res["queue"]) != before:
                if touched:
                    self._promote(res, now)
                released.append(name)
        return released

    def grant(self, name, re_id, session, now=None):
        """Holder yields to requester re_id: RELEASE + promote (which picks the
        best waiter — the requester, unless something now outranks it)."""
        now = time.time() if now is None else now
        res = self._res(name)
        self._expire(res, now)
        h = res.get("holder")
        yielded = False
        if h and (session is None or h.get("session") == session):
            res["holder"] = None
            yielded = True
        new_holder = self._promote(res, now) if yielded else None
        return {"yielded": yielded, "new_holder": new_holder, "re": re_id}

    def deny(self, name, re_id, now=None):
        """Holder refuses to yield yet: the denied waiter keeps a reservation
        for the next free window so a DENY cannot become starvation."""
        now = time.time() if now is None else now
        res = self._res(name)
        self._expire(res, now)
        target = next((w for w in res["queue"] if w["id"] == re_id), None)
        if target:
            target["reserved"] = True
        return {"denied": re_id, "found": target is not None, "holder": res.get("holder")}

    def standing_release(self, name, session, now=None):
        now = time.time() if now is None else now
        res = self._res(name)
        self._expire(res, now)
        if session and session not in res["standing"]:
            res["standing"].append(session)
        # A session that waives contention should not linger in the queue.
        res["queue"] = [w for w in res["queue"] if w.get("session") != session]
        return {"resource": name, "session": session}

    def query(self, name, now=None):
        now = time.time() if now is None else now
        res = self._res(name)
        reaped = self._reap_and_promote(res, now)
        q = sorted(res["queue"], key=precedence_key)
        return {"resource": name, "holder": res.get("holder"),
                "queue": q, "standing": list(res.get("standing", [])),
                "reaper": reaper_note(res.get("holder")), "reaped": reaped, "now": now}

    def status(self, now=None):
        now = time.time() if now is None else now
        out = []
        for name in sorted(self.state["resources"]):
            out.append(self.query(name, now))
        return out

    def sweep(self, now=None):
        """Reap every dead/lapsed holder across all resources (liveness first,
        TTL backstop) and promote where a window opened. On-demand and run by
        the SessionEnd hook — the unclean-exit complement to the clean-exit
        release."""
        now = time.time() if now is None else now
        swept = []
        for name, res in self.state["resources"].items():
            reaped = self._reap_and_promote(res, now)
            if reaped is not None:
                swept.append({"resource": name, "lapsed_id": reaped.get("id"),
                              "reason": reaped.get("reason", "lease lapsed (TTL)")})
        return swept


# --- Resource registry (config-not-fork) ----------------------------------
def load_registry() -> dict:
    import tomllib
    try:
        data = tomllib.loads(RESOURCES_PATH.read_text())
    except FileNotFoundError:
        return {}
    except Exception as e:  # a broken registry must not break coordination
        sys.stderr.write(f"warning: could not parse {RESOURCES_PATH}: {e}\n")
        return {}
    return data.get("resource", {})


def resource_meta(name: str) -> dict:
    return load_registry().get(name, {})


# --- COORD line formatting ------------------------------------------------
def _q(value: str) -> str:
    s = str(value)
    return f'"{s}"' if (" " in s or "\t" in s) else s


def coord_line(verb: str, resource: str | None = None, **fields) -> str:
    parts = ["COORD", verb]
    if resource is not None:
        parts.append(f"resource={resource}")
    order = ["id", "re", "profile", "prio", "hold", "for", "next"]
    for key in order:
        if fields.get(key) is not None:
            parts.append(f"{key}={_q(fields[key])}")
    for key, val in fields.items():
        if key not in order and val is not None:
            parts.append(f"{key}={_q(val)}")
    return " ".join(parts)


# --- Session resolution ---------------------------------------------------
def resolve_session(explicit: str | None) -> str:
    return (explicit
            or os.environ.get("CLAUDE_SESSION_ID")
            or os.environ.get("COORD_SESSION")
            or "anon")


def gen_id(prefix: str = "c") -> str:
    return f"{prefix}{int(time.time() * 1000) % 1_000_000:06d}"


def resolve_token(args) -> dict | None:
    """Resolve a liveness token from flags, else the session's own messaging
    socket ($CLAUDE_CODE_MESSAGING_SOCKET) — exported into every real session,
    so liveness reaping is automatic with no skill change. Enriched (inode
    nonce stamped) at claim time. Returns None only when nothing is available
    (an anonymous, TTL-only holder)."""
    spec = None
    if getattr(args, "pid", None):
        spec = f"pid:{args.pid}"
    elif getattr(args, "socket", None):
        spec = args.socket
    elif getattr(args, "holder_token", None):
        spec = args.holder_token
    else:
        spec = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    return enrich_token(parse_token(spec))


# --- CLI ------------------------------------------------------------------
def _print_holder(h: dict | None, now: float) -> str:
    if not h:
        return "  holder: (free)"
    ttl = fmt_duration(float(h.get("expires_at", now)) - now)
    prof = f" profile={h['profile']}" if h.get("profile") else ""
    return (f"  holder: id={h['id']} prio={h.get('prio')}{prof} "
            f"ttl={ttl} for={h.get('for') or '-'} session={h.get('session') or '-'}\n"
            f"          reaper: {reaper_note(h)}")


def _print_query(view: dict) -> None:
    now = view["now"]
    print(f"resource={view['resource']}")
    print(_print_holder(view["holder"], now))
    if view["queue"]:
        print("  queue (next-first):")
        for w in view["queue"]:
            tag = " reserved" if w.get("reserved") else ""
            print(f"    - id={w['id']} prio={w.get('prio')} seq={w.get('seq')}"
                  f"{tag} for={w.get('for') or '-'} session={w.get('session') or '-'}")
    else:
        print("  queue: (empty)")
    if view.get("standing"):
        print(f"  standing-release: {', '.join(view['standing'])}")


def cmd_claim(args, verb="CLAIM"):
    session = resolve_session(args.session)
    cid = args.id or gen_id("k")
    hold_secs = parse_duration(args.hold or resource_meta(args.resource).get("default_hold", DEFAULT_HOLD))
    token = resolve_token(args)
    with Ledger() as led:
        r = led.acquire(args.resource, cid, args.prio, hold_secs, session,
                        args.__dict__.get("for"), args.profile, args.next, token=token)
    line = coord_line(verb, args.resource, id=cid, profile=args.profile,
                      prio=args.prio, hold=fmt_duration(hold_secs),
                      **{"for": args.__dict__.get("for"), "next": args.next})
    print(line)
    if r["status"] == "granted":
        note = "renewed" if r.get("renewed") else "held"
        sys.stderr.write(f"# {note}: you hold '{args.resource}' as {cid} "
                         f"(lease {fmt_duration(hold_secs)}, reaper: {reaper_note(r['holder'])}). "
                         f"Probe before mutating.\n")
        return EXIT_OK
    # queued
    if r.get("renew_refused"):
        b = r["blocked_by"]
        sys.stderr.write(f"# renewal REFUSED: {b['id']} (prio {b['prio']}) is queued and "
                         f"strictly outranks your {args.prio} hold. Your current lease still "
                         f"stands — finish your atomic operation, then 'coord release' so "
                         f"{b['id']} is promoted. Do NOT keep the resource past it.\n")
        return EXIT_QUEUED
    if r.get("reason") == "preempt-owed":
        sys.stderr.write(f"# queued: '{args.resource}' held by {r['holder']['id']} "
                         f"(prio {r['holder']['prio']}); your {args.prio} is strictly higher "
                         f"-> yield owed. Await RELEASE/GRANT, then re-claim.\n")
    elif r.get("reason") == "yield-to-waiter":
        b = r["blocked_by"]
        sys.stderr.write(f"# queued: '{args.resource}' free but reserved for {b['id']} "
                         f"(prio {b.get('prio')}). You are next after it.\n")
    else:
        h = r["holder"]
        sys.stderr.write(f"# queued: '{args.resource}' held by {h['id']} (prio {h['prio']}); "
                         f"equal/lower priority waits until its lease frees.\n")
    return EXIT_QUEUED


def cmd_request(args):
    return cmd_claim(args, verb="REQUEST")


def cmd_release(args):
    session = resolve_session(args.session)
    with Ledger() as led:
        if args.all:
            names = led.release_session(session)
            for n in names:
                print(coord_line("RELEASE", n, **{"for": "session ended"}))
            sys.stderr.write(f"# released {len(names)} hold(s) for session {session}\n")
            return EXIT_OK
        r = led.release(args.resource, args.id, session)
    print(coord_line("RELEASE", args.resource, id=args.id,
                     **{"for": args.__dict__.get("for")}))
    if r["new_holder"]:
        sys.stderr.write(f"# freed; '{args.resource}' promoted to {r['new_holder']['id']} "
                         f"(prio {r['new_holder']['prio']}).\n")
    elif not r["freed"] and not r["dequeued"]:
        sys.stderr.write(f"# no-op: you did not hold or queue for '{args.resource}'.\n")
    return EXIT_OK


def cmd_grant(args):
    session = resolve_session(args.session)
    with Ledger() as led:
        r = led.grant(args.resource, args.re, session)
    print(coord_line("GRANT", args.resource, re=args.re,
                     **{"for": args.__dict__.get("for")}))
    if r["new_holder"]:
        sys.stderr.write(f"# yielded; '{args.resource}' now held by {r['new_holder']['id']}.\n")
    return EXIT_OK


def cmd_deny(args):
    with Ledger() as led:
        r = led.deny(args.resource, args.re)
    print(coord_line("DENY", args.resource, re=args.re,
                     **{"for": args.__dict__.get("for")}))
    if not r["found"]:
        sys.stderr.write(f"# note: no queued waiter '{args.re}' on '{args.resource}'.\n")
    return EXIT_OK


def cmd_standing_release(args):
    session = resolve_session(args.session)
    with Ledger() as led:
        led.standing_release(args.resource, session)
    print(coord_line("STANDING-RELEASE", args.resource,
                     **{"for": args.__dict__.get("for")}))
    return EXIT_OK


def cmd_query(args):
    print(coord_line("QUERY", args.resource))
    with Ledger() as led:
        view = led.query(args.resource)
    if args.json:
        print(json.dumps(view, indent=2, default=str))
    else:
        _print_query(view)
    return EXIT_OK


def cmd_status(args):
    with Ledger() as led:
        views = led.status()
    if args.json:
        print(json.dumps(views, indent=2, default=str))
        return EXIT_OK
    if not views:
        print("(no resources tracked)")
        return EXIT_OK
    for v in views:
        _print_query(v)
        print()
    return EXIT_OK


def cmd_sweep(args):
    with Ledger() as led:
        swept = led.sweep()
    for s in swept:
        print(coord_line("RELEASE", s["resource"], id=s["lapsed_id"],
                         **{"for": s.get("reason", "lease lapsed (swept)")}))
    sys.stderr.write(f"# swept {len(swept)} reaped hold(s)\n")
    return EXIT_OK


def cmd_probe(args):
    """Step 5 — verify the real resource state, don't trust the protocol."""
    meta = resource_meta(args.resource)
    probe = args.cmd or meta.get("probe")
    if not probe:
        sys.stderr.write(f"# no probe configured for '{args.resource}'. "
                         f"Verify reachability manually before mutating.\n")
        return EXIT_OK
    sys.stderr.write(f"# probing '{args.resource}': {probe}\n")
    try:
        proc = subprocess.run(shlex.split(probe), capture_output=True,
                              text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"# PROBE TIMEOUT after {args.timeout}s — resource NOT reachable.\n")
        return EXIT_PROBE_FAIL
    except Exception as e:
        sys.stderr.write(f"# PROBE ERROR: {e}\n")
        return EXIT_PROBE_FAIL
    if proc.returncode == 0:
        sys.stderr.write("# PROBE OK — resource reachable.\n")
        if proc.stdout.strip():
            print(proc.stdout.strip())
        return EXIT_OK
    sys.stderr.write(f"# PROBE FAILED (exit {proc.returncode}) — do NOT mutate.\n")
    if proc.stderr.strip():
        sys.stderr.write(proc.stderr.strip() + "\n")
    return EXIT_PROBE_FAIL


def cmd_resources(args):
    reg = load_registry()
    if not reg:
        print(f"(no registry at {RESOURCES_PATH})")
        return EXIT_OK
    for name in sorted(reg):
        meta = reg[name]
        print(f"{name}")
        if meta.get("description"):
            print(f"  {meta['description']}")
        if meta.get("default_hold"):
            print(f"  default_hold: {meta['default_hold']}")
        if meta.get("probe"):
            print(f"  probe: {meta['probe']}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coord", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common_claim(sp):
        sp.add_argument("resource")
        sp.add_argument("--prio", choices=VALID_PRIOS, default="P3")
        sp.add_argument("--hold", help="lease TTL, e.g. 15m / 30s / 2h (default 10m)")
        sp.add_argument("--id", help="hold id (generated if omitted)")
        sp.add_argument("--for", dest="for", help="short human purpose")
        sp.add_argument("--next", help="anticipated future need")
        sp.add_argument("--profile", help="resource-specific profile, e.g. Dev/Prod for warp")
        sp.add_argument("--session", help="session id (defaults to $CLAUDE_SESSION_ID)")
        sp.add_argument("--holder-token", dest="holder_token",
                        help="liveness token: a session socket path, 'sock:<path>', "
                             "'pid:<n>', or a bare pid. Defaults to "
                             "$CLAUDE_CODE_MESSAGING_SOCKET so a dead holder is reaped "
                             "early. Omit everything for a TTL-only (anonymous) hold.")
        sp.add_argument("--socket", help="shorthand for --holder-token <socket path>")
        sp.add_argument("--pid", help="shorthand for --holder-token pid:<n>")

    sp = sub.add_parser("claim", help="take the resource now / renew a lease")
    add_common_claim(sp); sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("request", help="ask a current holder to yield")
    add_common_claim(sp); sp.set_defaults(func=cmd_request)

    sp = sub.add_parser("release", help="free a hold you own")
    sp.add_argument("resource", nargs="?")
    sp.add_argument("--id")
    sp.add_argument("--for", dest="for")
    sp.add_argument("--session")
    sp.add_argument("--all", action="store_true", help="release every hold for this session")
    sp.set_defaults(func=cmd_release)

    sp = sub.add_parser("grant", help="yield to a requester (RELEASE + promote)")
    sp.add_argument("resource"); sp.add_argument("--re", required=True)
    sp.add_argument("--for", dest="for"); sp.add_argument("--session")
    sp.set_defaults(func=cmd_grant)

    sp = sub.add_parser("deny", help="refuse to yield yet (reserves the denied waiter)")
    sp.add_argument("resource"); sp.add_argument("--re", required=True)
    sp.add_argument("--for", dest="for")
    sp.set_defaults(func=cmd_deny)

    sp = sub.add_parser("standing-release", help="waive contention for the whole session")
    sp.add_argument("resource"); sp.add_argument("--for", dest="for")
    sp.add_argument("--session")
    sp.set_defaults(func=cmd_standing_release)

    sp = sub.add_parser("query", help="who holds a resource + its queue")
    sp.add_argument("resource"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("status", help="all resources at a glance")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("sweep", help="expire lapsed holds across all resources")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("probe", help="verify a resource is actually reachable")
    sp.add_argument("resource"); sp.add_argument("--cmd", help="override probe command")
    sp.add_argument("--timeout", type=int, default=15)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("resources", help="list the resource registry (config)")
    sp.set_defaults(func=cmd_resources)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_USER_ERR


if __name__ == "__main__":
    sys.exit(main())
