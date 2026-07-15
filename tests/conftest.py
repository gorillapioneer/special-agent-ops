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


# ── Provenance fixtures ───────────────────────────────────────────────────────

def commit_mission_script(filename: str, text: str, message: str) -> str:
    """Python -c script for a mission that writes *filename* and commits it."""
    return (
        "import pathlib, subprocess; "
        f"p = pathlib.Path({filename!r}); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        f"p.write_text({text!r}, encoding='utf-8'); "
        f"subprocess.run(['git', 'add', {filename!r}], check=True); "
        "subprocess.run(['git', '-c', 'user.name=Agent', "
        "'-c', 'user.email=agent@example.com', 'commit', '-q', '-m', "
        f"{message!r}], check=True)"
    )


def record_committing_mission(
    repo: Path,
    name: str,
    filename: str,
    text: str,
    attest: bool = True,
) -> dict:
    """Record a mission (argv mode) that writes one file and commits it."""
    from sao.blackbox import recorder

    script = commit_mission_script(filename, text, f"feat: {name}")
    return recorder.record_mission_argv(
        name=name,
        command_argv=[sys.executable, "-c", script],
        repo_path=repo,
        attest=attest,
    )


def human_commit(repo: Path, filename: str, text: str, message: str) -> str:
    """Make a plain (unattested) commit; return its sha."""
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    env_args = ["-c", "user.name=Human", "-c", "user.email=human@example.com"]
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(
        ["git", *env_args, "-C", str(repo), "commit", "-q", "-m", message],
        check=True,
    )
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


@pytest.fixture(scope="session")
def provenance_repo(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """A repo with two attested agent missions and one human commit.

    Mission A runs under a flight plan scoped to src/*; mission B has no
    flight plan.  Session-scoped: treat as READ-ONLY (tamper tests must use
    copy_provenance_repo).
    """
    pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")
    from sao.provenance import flightplan

    repo = tmp_path_factory.mktemp("provenance_repo")
    init_git_repo(repo)
    base_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    flightplan.file_flight_plan(
        repo, name="mission a", intent="add alpha module", scope=["src/*"]
    )
    mission_a = record_committing_mission(
        repo, "mission a", "src/alpha.py", "ALPHA = 1\nBETA = 2\n"
    )
    mission_b = record_committing_mission(
        repo, "mission b", "src/beta.py", "def beta():\n    return 'b'\n"
    )
    human_sha = human_commit(
        repo, "docs/notes.txt", "written by a human\n", "docs: add notes"
    )

    return {
        "repo": repo,
        "base_commit": base_commit,
        "mission_a": mission_a,
        "mission_b": mission_b,
        "human_commit": human_sha,
    }


@pytest.fixture
def copy_provenance_repo(provenance_repo: dict, tmp_path: Path):
    """Copy the shared provenance repo (including .git) for tamper tests."""

    def _copy() -> dict:
        dest = tmp_path / "repo"
        shutil.copytree(provenance_repo["repo"], dest)
        copied = dict(provenance_repo)
        copied["repo"] = dest
        return copied

    return _copy


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
