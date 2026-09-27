"""Strategy identity (spec §4).

strategy_hash = SHA-256 over (engine git commit, strategy code hash,
canonical params, universe, protocol_id). Two runs with the same hash ran
the same code on the same rules; the vault recomputes it from the current
checkout before a holdout evaluation and refuses when it differs, so the
holdout always tests the code and parameters that passed dev.

Code is hashed with CRLF normalised to LF: a Windows checkout with
core.autocrlf and a Linux one must agree on the hash of the same commit.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_CODE_DIR = REPO_ROOT / "app" / "strategy"


class DirtyTree(Exception):
    pass


def engine_commit(repo: Path = REPO_ROOT) -> str:
    """HEAD commit. Refuses when app/ has uncommitted or untracked changes:
    the commit would not describe the engine that actually ran."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", "app")
    if dirty:
        raise DirtyTree(f"uncommitted changes under app/; commit before a protocol run:\n{dirty}")
    return git("rev-parse", "HEAD")


def code_hash(paths: list[Path] | None = None, root: Path = REPO_ROOT) -> str:
    """Hash of the strategy source: relative path + LF-normalised bytes of each
    file. Default is every .py under app/strategy/."""
    if paths is None:
        paths = sorted(STRATEGY_CODE_DIR.rglob("*.py"))
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.resolve().relative_to(root.resolve()).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()


def canonical_params(params: dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def strategy_hash(engine_commit: str, code_hash: str, params: dict,
                  universe: list[str], protocol_id: str) -> str:
    payload = {"engine_commit": engine_commit, "code_hash": code_hash,
               "params": json.loads(canonical_params(params)),
               "universe": sorted(universe), "protocol_id": protocol_id}
    return hashlib.sha256(canonical_params(payload).encode()).hexdigest()
