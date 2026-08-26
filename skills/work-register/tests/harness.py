"""Throwaway registers for the work-register test suite.

Every fixture is a temp directory holding its own vault AND its own HOME, and the engine
is driven as a subprocess with that HOME in its environment. That matters more than it
looks: `--init` merges a `[register.<name>]` binding into the per-machine base config at
`~/.config/work-register/config.toml`, so a fixture that did not redirect HOME would
write into the machine's real one. Redirecting it makes the whole suite hermetic by
construction rather than by everyone remembering to be careful.

Driving the CLI rather than calling `main()` in-process buys three things the suite needs:
the real exit codes (`--show` on a miss, and the `--rebuild` refusal), the real stdout, and
a fresh interpreter per run — so no module-level constant resolved under one HOME can leak
into a test running under another.

Pure grammar functions are imported directly instead; see `engine()`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ENGINE = SKILL_ROOT / "scripts" / "sync_board.py"


_ENGINE_MODULE = None


def engine():
    """Import the engine as a module, for testing pure functions directly.

    Loaded by path rather than by name: the file is a script in `scripts/`, not an
    installed package, and adding its directory to `sys.path` would be a side effect the
    next test inherits. It IS registered in `sys.modules` first, because `@dataclass`
    resolves its own module to decide what is a `KW_ONLY` sentinel and cannot find one
    that was never registered.
    """
    global _ENGINE_MODULE
    if _ENGINE_MODULE is None:
        spec = importlib.util.spec_from_file_location("wr_engine", ENGINE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _ENGINE_MODULE = module
    return _ENGINE_MODULE


class Register:
    """One throwaway register: a vault, its own HOME, and a way to run the engine on it."""

    def __init__(self, tmp: Path, name: str = "test") -> None:
        self.tmp = tmp
        self.root = tmp / "vault"
        self.home = tmp / "home"
        self.name = name
        self.root.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)

        env = {k: v for k, v in os.environ.items() if not k.startswith("WORK_REGISTER")}
        env["HOME"] = str(self.home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # The engine stamps `since` from the local date; pin it so a fixture built at
        # 23:59 in one zone and asserted on in another cannot straddle midnight.
        env["TZ"] = "UTC"
        self.env = env

    # --- paths ---------------------------------------------------------------
    @property
    def contract(self) -> Path:
        return self.root / ".work-register.toml"

    @property
    def register_dir(self) -> Path:
        return self.root / "Register"

    @property
    def board(self) -> Path:
        return self.root / "WORK-REGISTER.md"

    @property
    def ledger(self) -> Path:
        return self.register_dir / ".sync-state.json"

    def board_named(self, relative: str) -> Path:
        return self.root / relative

    # --- driving the engine --------------------------------------------------
    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(ENGINE), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"engine exited {result.returncode} for {args}\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            )
        return result

    def init(self, *extra: str) -> subprocess.CompletedProcess:
        return self.run("--init", str(self.root), "--name", self.name, *extra)

    # --- authoring the fixture ----------------------------------------------
    def day(self, date: str, body: str) -> Path:
        path = self.register_dir / f"{date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
        return path

    def append_contract(self, toml: str) -> None:
        with self.contract.open("a", encoding="utf-8") as handle:
            handle.write("\n" + toml.strip("\n") + "\n")

    def write_contract(self, toml: str) -> None:
        self.contract.write_text(toml.strip("\n") + "\n", encoding="utf-8")

    # --- reading the fixture back -------------------------------------------
    def board_text(self, relative: str | None = None) -> str:
        path = self.board if relative is None else self.board_named(relative)
        return path.read_text(encoding="utf-8")

    def ids_on(self, relative: str | None = None) -> list[str]:
        import re

        return re.findall(r"<!-- wr:([A-Za-z0-9_.-]+) -->", self.board_text(relative))

    def card_for(self, item_id: str, relative: str | None = None) -> str:
        """The one card block carrying `item_id`, as it sits on the board."""
        import re

        text = self.board_text(relative)
        body = re.split(r"\n(?=## )", text)
        for chunk in body:
            for card in re.split(r"\n(?=- \[)", chunk):
                if f"wr:{item_id} " in card or f"wr:{item_id}-->" in card:
                    return card.rstrip()
        raise AssertionError(f"no card {item_id} on {relative or 'the default board'}")

    def column_of(self, item_id: str, relative: str | None = None) -> str:
        import re

        column = ""
        for line in self.board_text(relative).splitlines():
            if line.startswith("## "):
                column = line[3:].strip()
            if f"wr:{item_id} " in line or f"wr:{item_id}-->" in line:
                return column
        raise AssertionError(f"no card {item_id} on {relative or 'the default board'}")

    def settings(self, relative: str | None = None) -> dict:
        import json
        import re

        found = re.search(
            r"%%\s*kanban:settings\s*\n```\n(.*?)\n```\n%%", self.board_text(relative), re.S
        )
        if not found:
            raise AssertionError("board carries no kanban:settings block")
        return json.loads(found.group(1))

    def frontmatter(self, relative: str | None = None) -> dict:
        text = self.board_text(relative)
        if not text.startswith("---\n"):
            raise AssertionError("board carries no frontmatter")
        block = text.split("---\n", 2)[1]
        out = {}
        for line in block.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                out[key.strip()] = value.strip()
        return out

    def checksum(self) -> str:
        """One digest over the whole register — day files, boards, ledger, contract.

        A read-only verb is proved by this rather than by an absence of output: silence on
        stdout says nothing about whether an id was stamped into a day file.
        """
        digest = hashlib.sha256()
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(self.root)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


class RegisterCase(unittest.TestCase):
    """A test case with a fresh, initialised register per test."""

    #: appended to the contract `--init` writes, before any sync
    CONTRACT_EXTRA = ""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="work-register-test-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.reg = Register(self._tmp)
        self.reg.init()
        #: exactly what `--init` wrote, before this case's own vocabulary was layered on.
        #: A test that has to RE-declare a table (TOML refuses a second `[scope]`) rebuilds
        #: the contract from this rather than appending a header that cannot legally repeat.
        self.init_contract = self.reg.contract.read_text(encoding="utf-8")
        if self.CONTRACT_EXTRA:
            self.reg.append_contract(self.CONTRACT_EXTRA)

    def assertUnchanged(self, before: str, what: str) -> None:
        self.assertEqual(before, self.reg.checksum(), f"{what} wrote to the register")
