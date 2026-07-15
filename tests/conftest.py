"""Shared fixtures for the Special Agent Ops test suite.

Recording a mission is a real end-to-end operation (subprocess + git +
zip + hashing + QR image), so the expensive "recorded mission" fixture is
session-scoped and read-only tests share it.  Tests that mutate session
files copy it into their own tmp directory first (copy_mission).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def init_git_repo(path: Path) -> None:
    """Initialise a git repo with one commit so HEAD exists."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    env_args = [
        "-c", "user.name=SAO Tests",
        "-c", "user.email=sao-tests@example.com",
    ]
    (path / "README.md").write_text("test repo\n", encoding="utf-8")
    subprocess.run(["git", *env_args, "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", *env_args, "-C", str(path), "commit", "-q", "-m", "initial commit"],
        check=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A fresh git repository with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    return repo


# Command used for the shared recorded mission: prints to stdout and stderr
# and creates one untracked file, so changed-file tracking is exercised.
MISSION_SCRIPT = (
    "import sys, pathlib; "
    "print('mission stdout line'); "
    "print('mission stderr line', file=sys.stderr); "
    "pathlib.Path('artifact.txt').write_text('artifact contents')"
)


@pytest.fixture(scope="session")
def recorded_mission(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Record one real mission (argv mode) in a fresh git repo.

    Returns the recorder result dict with an extra key ``repo_path``.
    Session-scoped: treat the returned session directory as READ-ONLY.
    """
    pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")
    from sao.blackbox import recorder

    repo = tmp_path_factory.mktemp("mission_repo")
    init_git_repo(repo)

    result = recorder.record_mission_argv(
        name="Fixture Mission",
        command_argv=[sys.executable, "-c", MISSION_SCRIPT],
        repo_path=repo,
    )
    result["repo_path"] = repo
    return result


@pytest.fixture
def copy_mission(recorded_mission: dict, tmp_path: Path):
    """Copy the shared recorded mission into tmp_path for mutation tests.

    Returns a function that produces a dict with session_dir / zip_path
    pointing at the private copy.
    """

    def _copy() -> dict:
        src_dir: Path = recorded_mission["session_dir"]
        src_zip: Path = recorded_mission["zip_path"]
        dest_root = tmp_path / "sessions"
        dest_root.mkdir(exist_ok=True)
        dest_dir = dest_root / src_dir.name
        shutil.copytree(src_dir, dest_dir)
        dest_zip = dest_root / src_zip.name
        shutil.copy2(src_zip, dest_zip)
        return {
            "session_dir": dest_dir,
            "zip_path": dest_zip,
            "sessions_root": dest_root,
            "mission_id": recorded_mission["mission_id"],
        }

    return _copy
