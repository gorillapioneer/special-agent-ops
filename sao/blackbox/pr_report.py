"""
pr_report.py - Markdown pull request reports for recorded missions.

The report is intentionally compact and safe to paste into a GitHub pull
request. It summarizes mission identity, command metadata, changed files,
verification data, and local artifact paths without embedding stdout, stderr,
diffs, or archive contents.
"""

from __future__ import annotations

import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _display_path(path: Path, base: Path | None) -> str:
    if base is not None:
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            pass
    return str(path)


def _inline_code(value) -> str:
    return str(value).replace("`", r"\`")


def _status_from(exit_code, seal_payload: dict) -> str:
    if exit_code == 0:
        return "PASS"
    if isinstance(exit_code, int):
        return "FAIL"
    status = seal_payload.get("status")
    return status if status in {"PASS", "FAIL"} else "UNKNOWN"


def _repo_base(session_dir: Path, manifest: dict) -> Path | None:
    repo_path = manifest.get("repo_path")
    if repo_path:
        candidate = Path(repo_path)
        if candidate.exists():
            return candidate

    try:
        return session_dir.parents[2]
    except IndexError:
        return None


def build_pr_report_payload(session_dir: Path) -> dict:
    """Build a PR report payload from a recorded mission directory."""
    session_dir = Path(session_dir)
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seal = _load_json(session_dir / "seal.json")
    seal_payload = _load_json(session_dir / "seal_payload.json")

    mission_id = manifest.get("mission_id", session_dir.name)
    repo_base = _repo_base(session_dir, manifest)
    archive_path = session_dir.parent / f"{mission_id}.zip"
    changed_files = manifest.get("changed_files", [])
    if not isinstance(changed_files, list):
        changed_files = []

    exit_code = manifest.get("exit_code")
    command_mode = manifest.get("command_mode") or "shell"

    artifact_paths = {
        "archive": archive_path,
        "seal_card": session_dir / "seal_card.md",
        "html_card": session_dir / "seal_card.html",
        "mission_summary": session_dir / "mission_summary.md",
        "qr_image": session_dir / "seal_qr.png",
    }

    return {
        "mission": manifest.get("name", mission_id),
        "mission_id": mission_id,
        "status": _status_from(exit_code, seal_payload),
        "command_mode": command_mode,
        "command": manifest.get("command", ""),
        "command_argv": manifest.get("command_argv", []),
        "exit_code": exit_code if exit_code is not None else "unknown",
        "changed_files_count": manifest.get("changed_files_count", len(changed_files)),
        "changed_files": changed_files,
        "branch": manifest.get("git_branch", "unknown") or "unknown",
        "started_at": manifest.get("started_at", ""),
        "ended_at": manifest.get("ended_at", ""),
        "archive_sha256": (
            seal.get("archive_sha256")
            or seal_payload.get("archive_sha256")
            or ""
        ),
        "seal_version": (
            seal.get("seal_version")
            or seal_payload.get("seal_version")
            or ""
        ),
        "artifact_paths": {
            name: _display_path(path, repo_base)
            for name, path in artifact_paths.items()
            if path.exists()
        },
    }


def render_pr_report_markdown(payload: dict) -> str:
    """Render a paste-ready GitHub Markdown mission report."""
    mission_id = payload.get("mission_id", "")
    changed_files = payload.get("changed_files", [])
    artifacts = payload.get("artifact_paths", {})

    lines = [
        "# Special Agent Ops Mission Report",
        "",
        "## Summary",
        "",
        f"- Mission: {payload.get('mission', '')}",
        f"- Mission ID: {payload.get('mission_id', '')}",
        f"- Status: {payload.get('status', 'UNKNOWN')}",
        f"- Command mode: {payload.get('command_mode', 'shell')}",
        f"- Command: `{_inline_code(payload.get('command', ''))}`",
        f"- Exit code: {payload.get('exit_code', 'unknown')}",
        f"- Changed files: {payload.get('changed_files_count', 0)}",
        f"- Started: {payload.get('started_at', '')}",
        f"- Ended: {payload.get('ended_at', '')}",
        "",
        "## Verification",
        "",
        f"- Archive SHA256: `{_inline_code(payload.get('archive_sha256', ''))}`",
        f"- Seal version: {payload.get('seal_version', '')}",
        "- Local verification command:",
        "",
        "```bash",
        f"sao verify {mission_id}",
        "```",
        "",
    ]

    archive_path = artifacts.get("archive")
    if archive_path:
        lines.extend([
            "- Archive verification command:",
            "",
            "```bash",
            f"sao verify-archive {archive_path}",
            "```",
            "",
        ])

    lines.extend([
        "## Changed Files",
        "",
    ])
    if changed_files:
        lines.extend(f"- `{_inline_code(path)}`" for path in changed_files)
    else:
        lines.append("- No changed files recorded.")

    lines.extend([
        "",
        "## Local Artifacts",
        "",
    ])
    artifact_labels = [
        ("seal_card", "Markdown card"),
        ("html_card", "HTML card"),
        ("mission_summary", "Mission summary"),
        ("qr_image", "QR image"),
        ("archive", "Archive"),
    ]
    any_artifact = False
    for key, label in artifact_labels:
        path = artifacts.get(key)
        if path:
            any_artifact = True
            lines.append(f"- {label}: `{_inline_code(path)}`")
    if not any_artifact:
        lines.append("- No local artifact paths found.")

    lines.extend([
        "",
        "_Generated locally by Special Agent Ops. This report does not embed stdout, stderr, diffs, secrets, or archive contents._",
        "",
    ])
    return "\n".join(lines)


def write_pr_report(session_dir: Path, output_path: Path | None = None) -> Path:
    """Write a PR report Markdown file and return its path."""
    session_dir = Path(session_dir)
    if output_path is None:
        output_path = session_dir / "pr_report.md"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_pr_report_payload(session_dir)
    output_path.write_text(render_pr_report_markdown(payload), encoding="utf-8")
    return output_path
