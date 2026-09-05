"""Executable spec for the coord deterministic core + ledger.

Run: python3 -m pytest plugins/coordinate/scripts/tests/ -q
 or: python3 plugins/coordinate/scripts/tests/test_coord.py   (stdlib unittest)

Covers the six robustness properties from the coordinate skill:
  1. lease TTL frees a dead hold                     -> TestLease
  2. two concurrent claims resolve deterministically -> TestContention, TestConcurrentFlock
  3. higher prio preempts, equal/lower waits         -> TestPreemption
  4. anti-starvation (P3 not jumped by fresh P2s)    -> TestAntiStarvation
  5. concurrent flock writes don't corrupt           -> TestConcurrentFlock
  6. graceful no-op when uncontended                 -> TestUncontended, TestGraceful
"""
import os
import sys
import json
import time
import socket
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import coord  # noqa: E402
from coord import (  # noqa: E402
    Ledger, precedence_key, resolve_next, should_preempt, is_expired,
    parse_duration, fmt_duration, coord_line,
    parse_token, enrich_token, dead_reason, reap_reason, reaper_note,
    resolve_probe, ProbeUnresolved, _ctx_lookup,
)


def fresh_ledger():
    """A Ledger over a throwaway temp dir, so tests never touch /tmp/cc-coord."""
    d = tempfile.mkdtemp(prefix="coordtest-")
    return Ledger(path=Path(d) / "ledger.json", lock=Path(d) / "ledger.lock"), d


# --- Pure-function unit tests (no filesystem) -----------------------------
class TestDuration(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("30s"), 30)
        self.assertEqual(parse_duration("15m"), 900)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("5"), 300)  # bare == minutes

    def test_bad(self):
        with self.assertRaises(ValueError):
            parse_duration("soon")

    def test_fmt_roundtrip(self):
        self.assertEqual(fmt_duration(900), "15m")
        self.assertEqual(fmt_duration(7200), "2h")
        self.assertEqual(fmt_duration(30), "30s")


class TestPrecedence(unittest.TestCase):
    def test_priority_orders(self):
        p0 = {"id": "a", "prio": "P0", "seq": 9}
        p3 = {"id": "b", "prio": "P3", "seq": 1}
        self.assertLess(precedence_key(p0), precedence_key(p3))

    def test_seq_breaks_priority_tie_not_wallclock(self):
        early = {"id": "z", "prio": "P2", "seq": 1}
        late = {"id": "a", "prio": "P2", "seq": 2}
        # seq (delivery order) wins over lexical id: 'z' seq1 beats 'a' seq2.
        self.assertLess(precedence_key(early), precedence_key(late))

    def test_lexical_id_is_final_tiebreak(self):
        a = {"id": "a", "prio": "P2", "seq": 5}
        b = {"id": "b", "prio": "P2", "seq": 5}
        self.assertLess(precedence_key(a), precedence_key(b))

    def test_p0_beats_reserved(self):
        # "P0 preempts everything" — even a reserved lower waiter.
        p0 = {"id": "x", "prio": "P0", "seq": 99, "reserved": False}
        reserved_p3 = {"id": "y", "prio": "P3", "seq": 1, "reserved": True}
        self.assertEqual(resolve_next([reserved_p3, p0])["id"], "x")


class TestShouldPreempt(unittest.TestCase):
    def test_strictly_higher_preempts(self):
        self.assertTrue(should_preempt("P2", "P1"))
        self.assertTrue(should_preempt("P3", "P0"))

    def test_equal_or_lower_waits(self):
        self.assertFalse(should_preempt("P2", "P2"))  # equal waits
        self.assertFalse(should_preempt("P0", "P0"))  # even P0 vs P0
        self.assertFalse(should_preempt("P1", "P2"))  # lower waits


# --- Ledger behaviour tests -----------------------------------------------
class TestUncontended(unittest.TestCase):
    def test_first_claim_just_holds(self):
        led, d = fresh_ledger()
        with led:
            r = led.acquire("git-tree", "g1", "P2", 300, "s1", "commit", None, None, now=1000.0)
        self.assertEqual(r["status"], "granted")
        self.assertFalse(r["renewed"])
        self.assertEqual(r["holder"]["id"], "g1")

    def test_query_free_resource(self):
        led, d = fresh_ledger()
        with led:
            v = led.query("never-touched", now=1000.0)
        self.assertIsNone(v["holder"])
        self.assertEqual(v["queue"], [])


class TestLease(unittest.TestCase):
    def test_expired_hold_auto_frees_on_read(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("dev-db", "m1", "P2", 600, "s1", "backfill", None, None, now=1000.0)
        # 11 minutes later, past the 10-min lease -> free, and a new claim wins.
        with led:
            r = led.acquire("dev-db", "m2", "P2", 600, "s2", "reset", None, None, now=1000.0 + 660)
        self.assertEqual(r["status"], "granted")
        self.assertEqual(r["holder"]["id"], "m2")
        self.assertIsNotNone(r["lapsed"])
        self.assertEqual(r["lapsed"]["id"], "m1")

    def test_renew_extends_before_expiry(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("dev-db", "m1", "P2", 600, "s1", "backfill", None, None, now=1000.0)
        with led:
            r = led.acquire("dev-db", "m1", "P2", 1200, "s1", "renew", None, None, now=1300.0)
        self.assertTrue(r["renewed"])
        self.assertEqual(r["holder"]["expires_at"], 1300.0 + 1200)

    def test_sweep_frees_and_promotes(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("apply-lane", "a1", "P2", 60, "s1", "apply", None, None, now=1000.0)
            led.acquire("apply-lane", "a2", "P2", 300, "s2", "apply2", None, None, now=1001.0)
        with led:
            swept = led.sweep(now=1000.0 + 120)  # a1 lapsed
        self.assertEqual(len(swept), 1)
        with led:
            v = led.query("apply-lane", now=1000.0 + 121)
        self.assertEqual(v["holder"]["id"], "a2")  # promoted


class TestContention(unittest.TestCase):
    def test_two_claims_same_prio_first_seq_wins(self):
        led, d = fresh_ledger()
        with led:
            r1 = led.acquire("git-tree", "g1", "P2", 300, "s1", "c1", None, None, now=1000.0)
            r2 = led.acquire("git-tree", "g2", "P2", 300, "s2", "c2", None, None, now=1000.5)
        self.assertEqual(r1["status"], "granted")
        self.assertEqual(r2["status"], "queued")
        with led:
            v = led.query("git-tree", now=1001.0)
        self.assertEqual(v["holder"]["id"], "g1")
        self.assertEqual(v["queue"][0]["id"], "g2")

    def test_determinism_is_state_not_wallclock(self):
        # Same ledger state -> same winner, regardless of real time.
        led, d = fresh_ledger()
        with led:
            led.acquire("r", "b", "P2", 300, "s2", None, None, None, now=5.0)
            led.acquire("r", "a", "P2", 300, "s1", None, None, None, now=5.0)  # tie on time
        with led:
            v = led.query("r", now=6.0)
        # b claimed first (seq 1) so b holds; a waits — order by seq, not id.
        self.assertEqual(v["holder"]["id"], "b")


class TestPreemption(unittest.TestCase):
    def test_higher_prio_owed_yield(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("apply-lane", "p2", "P2", 600, "s1", "dev apply", None, None, now=1000.0)
            r = led.acquire("apply-lane", "x0", "P0", 480, "s2", "incident", None, None, now=1001.0)
        self.assertEqual(r["status"], "queued")
        self.assertTrue(r.get("preempt"))  # strictly higher -> yield owed

    def test_equal_prio_waits_no_preempt(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("apply-lane", "p2a", "P2", 600, "s1", None, None, None, now=1000.0)
            r = led.acquire("apply-lane", "p2b", "P2", 600, "s2", None, None, None, now=1001.0)
        self.assertEqual(r["status"], "queued")
        self.assertFalse(r.get("preempt"))

    def test_renewal_cannot_override_queued_higher_prio(self):
        # A holder must not extend its lease indefinitely over a strictly-higher
        # waiter — that would defeat preemption ("each renewal re-enters queue").
        led, d = fresh_ledger()
        with led:
            led.acquire("apply-lane", "p2", "P2", 300, "s1", "dev apply", None, None, now=1000.0)
            led.acquire("apply-lane", "x0", "P0", 480, "s2", "incident", None, None, now=1001.0)
        with led:
            r = led.acquire("apply-lane", "p2", "P2", 1800, "s1", "renew", None, None, now=1002.0)
        self.assertEqual(r["status"], "queued")
        self.assertTrue(r.get("renew_refused"))
        with led:
            v = led.query("apply-lane", now=1003.0)
        # Current lease stands (not extended past its original expiry) so the
        # holder can finish its atomic op, but it did not grow to 1800s.
        self.assertEqual(v["holder"]["id"], "p2")
        self.assertEqual(v["holder"]["expires_at"], 1000.0 + 300)  # unchanged

    def test_renewal_allowed_over_lower_waiter(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("apply-lane", "p2", "P2", 300, "s1", None, None, None, now=1000.0)
            led.acquire("apply-lane", "p3", "P3", 480, "s2", None, None, None, now=1001.0)
        with led:
            r = led.acquire("apply-lane", "p2", "P2", 1800, "s1", "renew", None, None, now=1002.0)
        self.assertTrue(r["renewed"])  # equal/lower waiter does not block renewal

    def test_release_promotes_preemptor(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("apply-lane", "p2", "P2", 600, "s1", None, None, None, now=1000.0)
            led.acquire("apply-lane", "x0", "P0", 480, "s2", "incident", None, None, now=1001.0)
        with led:
            r = led.release("apply-lane", "p2", "s1", now=1002.0)
        self.assertTrue(r["freed"])
        self.assertEqual(r["new_holder"]["id"], "x0")  # P0 promoted


class TestAntiStarvation(unittest.TestCase):
    def test_reserved_p3_not_jumped_by_fresh_p2(self):
        led, d = fresh_ledger()
        # P3 waits behind a holder, gets passed over one free window -> reserved.
        with led:
            led.acquire("r", "hold", "P1", 60, "s0", None, None, None, now=1000.0)
            led.acquire("r", "p3", "P3", 3600, "s3", "long-suffering", None, None, now=1001.0)
        # Holder releases -> p3 is sole waiter, gets promoted.
        with led:
            led.release("r", "hold", "s0", now=1002.0)
        with led:
            v = led.query("r", now=1002.0)
        self.assertEqual(v["holder"]["id"], "p3")

    def test_reserved_waiter_beats_fresh_higher_next_window(self):
        led, d = fresh_ledger()
        with led:
            # hold, then p3 queues behind it and a p2 also queues.
            led.acquire("r", "hold", "P1", 60, "s0", None, None, None, now=1000.0)
            led.acquire("r", "p3", "P3", 3600, "s3", None, None, None, now=1001.0)
            led.acquire("r", "p2first", "P2", 3600, "s2", None, None, None, now=1002.0)
        # First window: hold releases. p2first outranks p3 (both non-reserved),
        # so p2first is promoted; p3 is passed over -> becomes reserved.
        with led:
            led.release("r", "hold", "s0", now=1003.0)
            v = led.query("r", now=1003.0)
        self.assertEqual(v["holder"]["id"], "p2first")
        # Now a FRESH p2 arrives while p2first holds.
        with led:
            led.acquire("r", "p2fresh", "P2", 3600, "s9", None, None, None, now=1004.0)
        # Second window: p2first releases. reserved p3 must beat fresh p2.
        with led:
            led.release("r", "p2first", "s2", now=1005.0)
            v = led.query("r", now=1005.0)
        self.assertEqual(v["holder"]["id"], "p3")  # not starved by fresh P2s

    def test_p0_still_overrides_reservation(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("r", "hold", "P1", 60, "s0", None, None, None, now=1000.0)
            led.acquire("r", "p3", "P3", 3600, "s3", None, None, None, now=1001.0)
            led.acquire("r", "p2", "P2", 3600, "s2", None, None, None, now=1002.0)
            led.release("r", "hold", "s0", now=1003.0)  # p2 promoted, p3 reserved
            led.acquire("r", "x0", "P0", 480, "s9", "incident", None, None, now=1004.0)
            led.release("r", "p2", "s2", now=1005.0)  # window opens
            v = led.query("r", now=1005.0)
        self.assertEqual(v["holder"]["id"], "x0")  # P0 preempts everything


class TestGraceful(unittest.TestCase):
    def test_release_unheld_is_noop(self):
        led, d = fresh_ledger()
        with led:
            r = led.release("r", "nobody", "s1", now=1000.0)
        self.assertFalse(r["freed"])
        self.assertEqual(r["dequeued"], 0)

    def test_deny_missing_waiter_is_noop(self):
        led, d = fresh_ledger()
        with led:
            r = led.deny("r", "ghost", now=1000.0)
        self.assertFalse(r["found"])

    def test_corrupt_ledger_resets_not_crashes(self):
        led, d = fresh_ledger()
        Path(led.path).write_text("{ this is not json ")
        with led:
            r = led.acquire("r", "g1", "P2", 300, "s1", None, None, None, now=1000.0)
        self.assertEqual(r["status"], "granted")

    def test_standing_release_removes_from_queue(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("r", "hold", "P2", 600, "s0", None, None, None, now=1000.0)
            led.acquire("r", "w", "P2", 600, "swaive", None, None, None, now=1001.0)
            led.standing_release("r", "swaive", now=1002.0)
            v = led.query("r", now=1002.0)
        self.assertNotIn("swaive", [w.get("session") for w in v["queue"]])
        self.assertIn("swaive", v["standing"])


class TestSessionRelease(unittest.TestCase):
    def test_release_all_for_session(self):
        led, d = fresh_ledger()
        with led:
            led.acquire("r1", "a", "P2", 600, "sX", None, None, None, now=1000.0)
            led.acquire("r2", "b", "P2", 600, "sX", None, None, None, now=1000.0)
            led.acquire("r2", "c", "P2", 600, "sOther", None, None, None, now=1001.0)
        with led:
            names = led.release_session("sX", now=1002.0)
        self.assertEqual(set(names), {"r1", "r2"})
        with led:
            v1 = led.query("r1", now=1003.0)
            v2 = led.query("r2", now=1003.0)
        self.assertIsNone(v1["holder"])
        self.assertEqual(v2["holder"]["id"], "c")  # sOther promoted


class TestCoordLine(unittest.TestCase):
    def test_quotes_spaces(self):
        line = coord_line("CLAIM", "warp", id="k1", prio="P3", hold="5m",
                          **{"for": "kubectl get ns"})
        self.assertIn('for="kubectl get ns"', line)
        self.assertIn("resource=warp", line)
        self.assertTrue(line.startswith("COORD CLAIM resource=warp"))

    def test_no_quotes_when_no_spaces(self):
        line = coord_line("RELEASE", "git-tree", id="g1")
        self.assertIn("id=g1", line)
        self.assertNotIn('"', line)


# --- Concurrency: real subprocesses hammering one ledger ------------------
class TestConcurrentFlock(unittest.TestCase):
    def test_no_corruption_and_single_holder(self):
        d = tempfile.mkdtemp(prefix="coordconc-")
        env = dict(os.environ, COORD_DIR=d)
        env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)  # keep these holds anonymous
        coord_py = str(SCRIPTS / "coord.py")
        # 24 sessions race to claim the same resource at once.
        procs = []
        for i in range(24):
            procs.append(subprocess.Popen(
                [sys.executable, coord_py, "claim", "hot",
                 "--prio", "P2", "--hold", "10m", "--id", f"c{i:02d}",
                 "--session", f"s{i:02d}"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        rcs = [p.wait() for p in procs]
        # Exactly one GRANTED (exit 0); the rest QUEUED (exit 10).
        self.assertEqual(rcs.count(coord.EXIT_OK), 1, f"expected 1 grant, got {rcs}")
        self.assertEqual(rcs.count(coord.EXIT_QUEUED), 23)
        # Ledger is valid JSON with exactly one holder and 23 queued.
        state = json.loads((Path(d) / "ledger.json").read_text())
        res = state["resources"]["hot"]
        self.assertIsNotNone(res["holder"])
        self.assertEqual(len(res["queue"]), 23)
        # seq counter is dense & unique across all enqueues + the holder.
        seqs = [w["seq"] for w in res["queue"]]
        self.assertEqual(len(set(seqs)), len(seqs))  # no duplicate seq

    def test_cli_end_to_end_flow(self):
        d = tempfile.mkdtemp(prefix="coorde2e-")
        env = dict(os.environ, COORD_DIR=d)
        env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)  # keep these holds anonymous
        coord_py = str(SCRIPTS / "coord.py")

        def run(*args):
            return subprocess.run([sys.executable, coord_py, *args],
                                  env=env, capture_output=True, text=True)

        r = run("claim", "warp", "--prio", "P3", "--hold", "5m",
                "--id", "k1", "--session", "s1", "--profile", "Dev",
                "--for", "kubectl get ns")
        self.assertEqual(r.returncode, coord.EXIT_OK)
        self.assertIn("COORD CLAIM resource=warp", r.stdout)
        self.assertIn('profile=Dev', r.stdout)

        r = run("claim", "warp", "--prio", "P3", "--id", "k2", "--session", "s2")
        self.assertEqual(r.returncode, coord.EXIT_QUEUED)

        r = run("release", "warp", "--id", "k1", "--session", "s1")
        self.assertEqual(r.returncode, coord.EXIT_OK)
        self.assertIn("COORD RELEASE resource=warp", r.stdout)

        r = run("query", "warp", "--json")
        view = json.loads(r.stdout.split("\n", 1)[1])
        self.assertEqual(view["holder"]["id"], "k2")  # k2 promoted


# --- Liveness: reap a DEAD holder early, via connect-probe (not file stat) ---
#
# The headline property: a `kill -9`'d holder leaves its .sock FILE behind
# (bind creates the inode, only unlink removes it), so a file-existence check
# would falsely report LIVE forever. connect() is the authority; the probe
# fails toward ALIVE on every ambiguity (a false DEAD is catastrophic).
class _Sock:
    """A real AF_UNIX listener used to model a session's messaging socket."""
    def __init__(self, path):
        self.path = str(path)
        self.sock = None

    def listen(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(64)
        return self

    def kill9(self):
        # Simulate SIGKILL: the process is gone but the bound file SURVIVES
        # (no unlink). Closing the fd does not remove the path.
        if self.sock:
            self.sock.close()
            self.sock = None

    def recreate(self):
        # Simulate pid reuse: a NEW session unlinks the stale path and binds a
        # fresh socket -> a new inode at the same path.
        if self.sock:
            self.sock.close()
        os.unlink(self.path)
        self.sock = None
        return self.listen()

    def close(self):
        if self.sock:
            self.sock.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class TestLiveness(unittest.TestCase):
    def setUp(self):
        # Short dir under /tmp to stay within the AF_UNIX path length limit.
        self.d = tempfile.mkdtemp(prefix="ct-", dir="/tmp")
        self.socks = []

    def tearDown(self):
        for s in self.socks:
            s.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def _sock(self, name):
        s = _Sock(os.path.join(self.d, name)).listen()
        self.socks.append(s)
        return s

    def test_live_socket_holder_stands(self):
        s = self._sock(f"{os.getpid()}.sock")  # pid alive, listener up
        tok = enrich_token(parse_token(s.path))
        self.assertIsNotNone(tok.get("ino"))
        self.assertIsNone(dead_reason(tok))  # connect succeeds -> alive
        led, _ = fresh_ledger()
        with led:
            led.acquire("r", "h", "P2", 600, "sess", None, None, None, now=1000.0, token=tok)
            v = led.query("r", now=1001.0)  # well within TTL
        self.assertEqual(v["holder"]["id"], "h")  # stands

    def test_kill9_stale_socket_reaped_before_ttl(self):
        # THE headline test: a file-existence check would FAIL this.
        s = self._sock(f"{os.getpid()}.sock")
        tok = enrich_token(parse_token(s.path))
        led, _ = fresh_ledger()
        with led:
            led.acquire("r", "dead", "P2", 3600, "sess", None, None, None,
                        now=1000.0, token=tok)
            led.acquire("r", "waiter", "P2", 3600, "s2", None, None, None, now=1001.0)
        s.kill9()  # process gone, .sock file remains -> connect ECONNREFUSED
        self.assertTrue(os.path.exists(s.path), "kill -9 leaves the socket file")
        self.assertIsNotNone(dead_reason(tok))  # positively dead via connect
        with led:
            v = led.query("r", now=1002.0)  # 3598s before TTL
        self.assertEqual(v["holder"]["id"], "waiter")  # reaped EARLY + promoted

    def test_unclassifiable_probe_fails_toward_alive(self):
        # A probe error we cannot classify as definitively dead must NOT reap.
        s = self._sock(f"{os.getpid()}.sock")
        tok = enrich_token(parse_token(s.path))
        orig = coord._connect_dead
        coord._connect_dead = lambda *a, **k: None  # simulate EACCES/timeout/unknown
        try:
            self.assertIsNone(dead_reason(tok))  # unknown -> alive
            led, _ = fresh_ledger()
            with led:
                led.acquire("r", "h", "P2", 600, "sess", None, None, None,
                            now=1000.0, token=tok)
                v = led.query("r", now=1001.0)
            self.assertEqual(v["holder"]["id"], "h")  # not reaped
        finally:
            coord._connect_dead = orig

    def test_kill0_alive_but_connect_refused_reaped(self):
        # pid looks alive (our own pid), inode matches, but no listener answers
        # -> connect is authoritative -> reap. (pid-reuse-safe: never trust
        # kill -0 success as life.)
        s = self._sock(f"{os.getpid()}.sock")
        tok = enrich_token(parse_token(s.path))
        s.kill9()
        self.assertEqual(coord._kill0_dead(os.getpid()), False)  # our pid is alive
        self.assertIsNotNone(dead_reason(tok))  # yet reaped via connect refusal

    def test_pid_reuse_inode_nonce_prevents_false_live(self):
        # Holder dies; pid recycled to a NEW session that binds the SAME path.
        # connect() would succeed (new listener) -> the inode nonce is what
        # prevents a confident false LIVE.
        s = self._sock(f"{os.getpid()}.sock")
        tok = enrich_token(parse_token(s.path))
        old_ino = tok["ino"]
        s.recreate()  # new inode at same path, new listener answering
        new_ino = os.stat(s.path).st_ino
        self.assertNotEqual(old_ino, new_ino)
        reason = dead_reason(tok)
        self.assertIsNotNone(reason)
        self.assertIn("inode mismatch", reason)  # reaped, not false-LIVE

    def test_esrch_pid_token_is_dead(self):
        # A raw pid token for a pid that cannot exist -> ESRCH -> dead.
        self.assertIsNotNone(dead_reason({"type": "pid", "value": "2147480000"}))
        self.assertIsNone(dead_reason({"type": "pid", "value": str(os.getpid())}))

    def test_no_token_is_ttl_only_and_surfaced(self):
        # An anonymous holder is TTL-only, and query MUST surface that it is
        # un-probeable rather than silently un-reapable.
        led, _ = fresh_ledger()
        with led:
            led.acquire("r", "anon", "P2", 600, "anon", None, None, None,
                        now=1000.0, token=None)
            v = led.query("r", now=1001.0)
        self.assertEqual(v["holder"]["id"], "anon")  # not liveness-reaped
        self.assertIn("ttl-only", v["reaper"])
        self.assertIn("no liveness token", v["reaper"])

    def test_reap_promotes_p0_over_p2(self):
        # Dead holder + a P0 and a P2 waiting -> reap and promote the P0.
        s = self._sock(f"{os.getpid()}.sock")
        tok = enrich_token(parse_token(s.path))
        led, _ = fresh_ledger()
        with led:
            led.acquire("r", "dead", "P2", 3600, "sess", None, None, None,
                        now=1000.0, token=tok)
            led.acquire("r", "p2", "P2", 3600, "s2", None, None, None, now=1001.0)
            led.acquire("r", "x0", "P0", 3600, "s3", "incident", None, None, now=1002.0)
        s.kill9()
        with led:
            v = led.query("r", now=1003.0)
        self.assertEqual(v["holder"]["id"], "x0")  # P0 promoted after reap

    def test_reaper_note_weak_vs_verified(self):
        s = self._sock(f"{os.getpid()}.sock")
        verified = enrich_token(parse_token(s.path))
        self.assertIn("verified", reaper_note({"token": verified}))
        weak = {"type": "socket", "value": "/tmp/cc-socks/1.sock"}  # no inode stamped
        self.assertIn("weak", reaper_note({"token": weak}))


class TestLivenessCLI(unittest.TestCase):
    def _run(self, env, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "coord.py"), *args],
                              env=env, capture_output=True, text=True)

    def test_env_socket_autopicked_and_sweep_reaps(self):
        d = tempfile.mkdtemp(prefix="ct-", dir="/tmp")
        try:
            sockpath = os.path.join(d, f"{os.getpid()}.sock")
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sockpath)
            srv.listen(64)
            env = dict(os.environ, COORD_DIR=d, CLAUDE_CODE_MESSAGING_SOCKET=sockpath)
            # Claim with no token flag -> auto-picks the env socket.
            r = self._run(env, "claim", "res", "--prio", "P2", "--hold", "1h",
                          "--id", "h", "--session", "sess")
            self.assertEqual(r.returncode, coord.EXIT_OK)
            self.assertIn("inode-nonce verified", r.stderr)
            # A DIFFERENT session queues, with its OWN (live) socket.
            wpath = os.path.join(d, f"{os.getpid()}-w.sock")
            wsrv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            wsrv.bind(wpath)
            wsrv.listen(64)
            self._run(env, "claim", "res", "--prio", "P2", "--id", "w",
                      "--session", "s2", "--socket", wpath)
            # Kill the holder's session: close its socket, the file remains.
            srv.close()
            r = self._run(env, "sweep")
            self.assertIn("holder dead", r.stdout)  # RELEASE line carries the reason
            r = self._run(env, "query", "res", "--json")
            view = json.loads(r.stdout.split("\n", 1)[1])
            self.assertEqual(view["holder"]["id"], "w")  # reaped + promoted (its socket lives)
            wsrv.close()
        finally:
            try:
                srv.close()
            except Exception:
                pass
            shutil.rmtree(d, ignore_errors=True)

    def test_anon_claim_surfaced_in_query(self):
        d = tempfile.mkdtemp(prefix="ct-", dir="/tmp")
        try:
            env = dict(os.environ, COORD_DIR=d)
            env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
            r = self._run(env, "claim", "res", "--id", "a", "--session", "anon")
            self.assertEqual(r.returncode, coord.EXIT_OK)
            r = self._run(env, "query", "res")
            self.assertIn("ttl-only", r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# --- Probe context binding: verify the CLAIMED profile, not the default ------
WARP_META = {
    "probe_template": "kubectl --context {context} get ns --request-timeout=8s",
    "contexts": {"Dev": "surge-dev", "Prod": "prod"},
}


class TestProbeResolve(unittest.TestCase):
    def test_dev_maps_to_surge_dev(self):
        cmd, ctx = resolve_probe(WARP_META, "Dev")
        self.assertEqual(ctx, "surge-dev")
        self.assertIn("--context surge-dev", cmd)
        self.assertNotIn("prod", cmd)

    def test_prod_maps_to_prod(self):
        cmd, ctx = resolve_probe(WARP_META, "Prod")
        self.assertEqual(ctx, "prod")
        self.assertIn("--context prod", cmd)

    def test_case_insensitive_profile(self):
        self.assertEqual(_ctx_lookup(WARP_META["contexts"], "dev"), "surge-dev")

    def test_no_profile_refuses(self):
        with self.assertRaises(ProbeUnresolved):
            resolve_probe(WARP_META, None)

    def test_unknown_profile_refuses(self):
        with self.assertRaises(ProbeUnresolved):
            resolve_probe(WARP_META, "Staging")

    def test_plain_probe_needs_no_profile(self):
        cmd, ctx = resolve_probe({"probe": "git status --porcelain"}, None)
        self.assertIsNone(ctx)
        self.assertEqual(cmd, "git status --porcelain")

    def test_explicit_cmd_is_verbatim(self):
        cmd, ctx = resolve_probe(WARP_META, None, explicit_cmd="echo hi")
        self.assertEqual((cmd, ctx), ("echo hi", None))

    def test_no_probe_configured(self):
        self.assertIsNone(resolve_probe({"description": "x"}, None))

    def test_shipped_registry_maps_correctly(self):
        # Guards the ACTUAL config/resources.toml, not just a fixture.
        meta = coord.resource_meta("warp")
        self.assertEqual(resolve_probe(meta, "Dev")[1], "surge-dev")
        self.assertEqual(resolve_probe(meta, "Prod")[1], "prod")
        with self.assertRaises(ProbeUnresolved):
            resolve_probe(meta, None)


class TestProbeContextCLI(unittest.TestCase):
    """A mock `kubectl` on PATH records the --context it was invoked with, proving
    the probe targets the profile's context — never the default (prod)."""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ct-", dir="/tmp")
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        self.record = os.path.join(self.d, "kubectl-args")
        shim = os.path.join(self.bin, "kubectl")
        with open(shim, "w") as f:
            f.write("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > "
                    + repr(self.record) + "\nexit 0\n")
        os.chmod(shim, 0o755)
        self.env = dict(os.environ, COORD_DIR=self.d,
                        PATH=self.bin + os.pathsep + os.environ["PATH"])
        self.env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "coord.py"), *args],
                              env=self.env, capture_output=True, text=True)

    def _recorded_context(self):
        with open(self.record) as f:
            args = f.read().splitlines()
        return args[args.index("--context") + 1]

    def test_dev_profile_targets_surge_dev(self):
        r = self._run("probe", "warp", "--profile", "Dev")
        self.assertEqual(r.returncode, coord.EXIT_OK)
        self.assertEqual(self._recorded_context(), "surge-dev")
        self.assertIn("context=surge-dev", r.stderr)

    def test_prod_profile_targets_prod(self):
        r = self._run("probe", "warp", "--profile", "Prod")
        self.assertEqual(r.returncode, coord.EXIT_OK)
        self.assertEqual(self._recorded_context(), "prod")

    def test_no_profile_refuses_and_never_calls_kubectl(self):
        r = self._run("probe", "warp")
        self.assertEqual(r.returncode, coord.EXIT_PROBE_UNRESOLVED)
        self.assertIn("PROBE REFUSED", r.stderr)
        self.assertFalse(os.path.exists(self.record),
                         "kubectl must NOT run when the target is unresolvable")

    def test_held_lease_profile_used_when_flag_omitted(self):
        # A Dev lease is held; probe with no --profile inherits its profile.
        self._run("claim", "warp", "--id", "k1", "--profile", "Dev",
                  "--prio", "P3", "--session", "s1")
        r = self._run("probe", "warp")
        self.assertEqual(r.returncode, coord.EXIT_OK)
        self.assertEqual(self._recorded_context(), "surge-dev")

    def test_unknown_profile_refuses(self):
        r = self._run("probe", "warp", "--profile", "Staging")
        self.assertEqual(r.returncode, coord.EXIT_PROBE_UNRESOLVED)
        self.assertFalse(os.path.exists(self.record))


if __name__ == "__main__":
    unittest.main(verbosity=2)
