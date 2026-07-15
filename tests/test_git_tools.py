"""Tests for sao.blackbox.git_tools against a real git repository."""

import subprocess
from pathlib import Path

from sao.blackbox import git_tools


def _commit_all(repo: Path, message: str) -> None:
    args = ["git", "-c", "user.name=SAO Tests", "-c", "user.email=sao-tests@example.com",
            "-C", str(repo)]
    subprocess.run([*args, "add", "-A"], check=True)
    subprocess.run([*args, "commit", "-q", "-m", message], check=True)


def test_get_branch(git_repo: Path):
    assert git_tools.get_branch(cwd=git_repo) == "main"


def test_get_commit_is_full_sha(git_repo: Path):
    commit = git_tools.get_commit(cwd=git_repo)
    assert len(commit) == 40
    assert all(c in "0123456789abcdef" for c in commit)


def test_outside_repo_returns_unknown(tmp_path: Path):
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    assert git_tools.get_branch(cwd=plain) == "unknown"
    assert git_tools.get_commit(cwd=plain) == "unknown"


def test_status_clean_repo_is_empty(git_repo: Path):
    assert git_tools.get_status_short(cwd=git_repo).strip() == ""


def test_status_reports_untracked(git_repo: Path):
    (git_repo / "new.txt").write_text("x", encoding="utf-8")
    status = git_tools.get_status_short(cwd=git_repo)
    assert "?? new.txt" in status


def test_get_diff_reports_modification(git_repo: Path):
    (git_repo / "README.md").write_text("changed contents\n", encoding="utf-8")
    diff = git_tools.get_diff(cwd=git_repo)
    assert "README.md" in diff
    assert "+changed contents" in diff


def test_changed_files_modified_and_untracked(git_repo: Path):
    (git_repo / "README.md").write_text("modified\n", encoding="utf-8")
    (git_repo / "brand_new.txt").write_text("new\n", encoding="utf-8")
    files = git_tools.get_changed_files(cwd=git_repo)
    assert "README.md" in files
    assert "brand_new.txt" in files


def test_changed_files_includes_staged(git_repo: Path):
    (git_repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "staged.txt"], check=True)
    files = git_tools.get_changed_files(cwd=git_repo)
    assert "staged.txt" in files


def test_changed_files_no_duplicates(git_repo: Path):
    (git_repo / "README.md").write_text("modified\n", encoding="utf-8")
    files = git_tools.get_changed_files(cwd=git_repo)
    assert len(files) == len(set(files))


def test_changed_files_exclude_prefix(git_repo: Path):
    inside = git_repo / "blackbox" / "sessions"
    inside.mkdir(parents=True)
    (inside / "session_file.txt").write_text("x", encoding="utf-8")
    (git_repo / "kept.txt").write_text("x", encoding="utf-8")
    # Commit so paths appear individually rather than as a collapsed dir,
    # then modify both.
    _commit_all(git_repo, "add files")
    (inside / "session_file.txt").write_text("y", encoding="utf-8")
    (git_repo / "kept.txt").write_text("y", encoding="utf-8")

    files = git_tools.get_changed_files(cwd=git_repo, exclude_prefix="blackbox/sessions/")
    assert "kept.txt" in files
    assert all(not f.startswith("blackbox/sessions/") for f in files)


def test_changed_files_clean_repo_empty(git_repo: Path):
    assert git_tools.get_changed_files(cwd=git_repo) == []
