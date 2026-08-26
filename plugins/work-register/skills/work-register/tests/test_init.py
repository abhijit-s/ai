"""`--init`: the verb every other fixture in this suite already leans on.

It writes exactly three things and refuses to overwrite any of them, because a
half-initialised register is worse than none — the binding and the contract only mean
something together.
"""

from __future__ import annotations

import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from harness import Register, RegisterCase


class Init(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="work-register-test-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.reg = Register(self._tmp)

    @property
    def base_config(self) -> Path:
        return self.reg.home / ".config" / "work-register" / "config.toml"

    def test_init_writes_the_contract_the_binding_and_the_day_file_directory(self):
        out = self.reg.init()
        self.assertEqual(0, out.returncode)
        self.assertTrue(self.reg.contract.is_file())
        self.assertTrue(self.reg.register_dir.is_dir())
        self.assertTrue((self.reg.register_dir / "README.md").is_file())

        base = tomllib.loads(self.base_config.read_text())
        # --init resolves the path it is handed, so compare resolved to resolved.
        self.assertEqual(
            str(self.reg.root.resolve()), base["register"]["test"]["data_root"]
        )
        self.assertEqual("test", base["default_register"])

    def test_init_writes_no_board(self):
        """The board is derived; an empty one scaffolded here would be a second source of
        truth for exactly one run."""
        self.reg.init()
        self.assertFalse(self.reg.board.exists())
        self.reg.day("2026-08-01", "## S\n\n- [ ] ▶ first item\n")
        self.reg.run()
        self.assertTrue(self.reg.board.is_file())

    def test_init_refuses_to_overwrite_an_existing_register(self):
        self.reg.init()
        again = self.reg.run("--init", str(self.reg.root), "--name", "test", check=False)
        self.assertNotEqual(0, again.returncode)
        self.assertIn("already exists", again.stderr)
        self.assertIn("will not overwrite", again.stderr)

    def test_init_dry_run_writes_nothing(self):
        out = self.reg.run("--init", str(self.reg.root), "--name", "test", "--dry-run")
        self.assertEqual(0, out.returncode)
        self.assertIn("nothing written", out.stdout)
        self.assertFalse(self.reg.contract.exists())
        self.assertFalse(self.base_config.exists())

    def test_init_refuses_a_path_that_is_not_a_directory(self):
        out = self.reg.run("--init", str(self.reg.root / "nope"), check=False)
        self.assertNotEqual(0, out.returncode)
        self.assertIn("existing vault directory", out.stderr)

    def test_a_second_register_keeps_the_first_default(self):
        self.reg.init()
        other = self.reg.tmp / "second"
        other.mkdir()
        out = self.reg.run("--init", str(other), "--name", "second")
        self.assertEqual(0, out.returncode)
        base = tomllib.loads(self.base_config.read_text())
        self.assertEqual({"test", "second"}, set(base["register"]))
        self.assertEqual("test", base["default_register"], "init stole the default")


class InitProvesItself(RegisterCase):
    def test_the_engine_resolves_the_register_init_just_wrote(self):
        out = self.reg.run("--show-config")
        self.assertIn("registers: test", out.stdout)
        self.assertIn("resolved: test", out.stdout)
