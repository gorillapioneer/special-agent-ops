"""
recorder.py — orchestrates one mission recording session end-to-end.

Flow:
    1.  Generate a unique mission_id from timestamp + sanitised name.
    2.  Create the session folder under blackbox/sessions/.
    3.  Capture git state *before* running the command.
    4.  Run the command with subprocess (in its own process group on POSIX;
        surviving background children are killed after the main process
        exits, so the after-state and seal bind to a quiesced snapshot).
    5.  Capture git state *after*.
    6.  Write raw artefacts (manifest.json, stdout.txt, git_diff.patch, …).
    7.  Compress the session folder into a .zip archive.
    8.  Write the SHA256 seal (seal.json + seal.txt).
    9.  Write the seal card (seal_payload.json + seal_card.md).
   10.  Write mission_summary.md (includes seal hashes + card paths).
   11.  Return a result dict so the CLI can print the summary.

QR image generation uses the qrcode[pil] runtime dependency.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import compressor, git_tools, html_card as html_mod, qr_image as qr_image_mod, qr_payload as qr_mod, seal as seal_mod, seal_card as card_mod, summary


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise_name(name: str) -> str:
    """Turn a free-text mission name into a safe path component."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)   # non-alphanumeric → underscore
    slug = slug.strip("_")
    return slug[:40]                            # cap at 40 chars


def _make_mission_id(name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{_sanitise_name(name)}"


def format_command_argv(command_argv: list[str]) -> str:
    """Return a readable command string for an argv list."""
    if os.name == "nt":
        return subprocess.list2cmdline(command_argv)

    import shlex

    return shlex.join(command_argv)


# Grace period between SIGTERM and SIGKILL when reaping surviving
# process-group members after the wrapped command exits (POSIX only).
_KILL_GRACE_SECONDS = 2.0


def _terminate_process_group(pgid: int) -> None:
    """Kill any surviving members of process group *pgid* (POSIX only).

    SIGTERM first, a brief grace period, then SIGKILL.  Never raises: the
    group usually no longer exists (the common case — no background
    stragglers), and permission errors are ignored.
    """
    import signal
    import time

    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + _KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)          # any members left?
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _execute_command(command, *, shell: bool, cwd=None):
    """Run the wrapped command and return (stdout, stderr, exit_code).

    On POSIX the command runs in its own session/process group
    (``start_new_session=True``) and, after the main process exits, any
    surviving group members (background children the command left behind)
    are killed BEFORE the caller captures after-state and seals — so the
    seal binds to a quiesced snapshot, not one a straggler can mutate
    mid-hash.  Output goes to temp files rather than pipes so a lingering
    child holding the pipe open cannot stall collection.

    On Windows there is no process group/session equivalent here; behaviour
    falls back to a plain ``subprocess.run``.
    """
    if os.name != "posix":
        proc = subprocess.run(
            command,
            shell=shell,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout, proc.stderr, proc.returncode

    import tempfile

    # Popen (not run): we need the child's pid to address its process
    # group after exit.  Running the user's own command is this module's
    # documented purpose (see _run_shell_command).
    from subprocess import Popen

    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = Popen(
            command,
            shell=shell,
            cwd=cwd,
            stdout=out_f,
            stderr=err_f,
            start_new_session=True,
        )
        exit_code = proc.wait()
        # The session leader is proc.pid; reap any group stragglers now,
        # before after-state capture and sealing.
        _terminate_process_group(proc.pid)
        out_f.seek(0)
        err_f.seek(0)
        stdout_text = out_f.read().decode("utf-8", errors="replace")
        stderr_text = err_f.read().decode("utf-8", errors="replace")
        return stdout_text, stderr_text, exit_code


def _run_shell_command(command: str, cwd=None):
    """Execute *command* in a shell. Returns (stdout, stderr, exit_code).

    shell=True is intentional here: the caller provides the full shell command
    string (e.g. "python -m pytest -x"), and we want the shell to resolve it
    exactly as the user typed it — on Windows this uses cmd.exe, on Unix /bin/sh.
    """
    try:
        return _execute_command(command, shell=True, cwd=cwd)
    except Exception as exc:
        return "", f"Failed to start command: {exc}", 1


def _run_argv_command(command_argv: list[str], cwd=None):
    """Execute *command_argv* without a shell. Returns stdout, stderr, exit_code."""
    try:
        return _execute_command(command_argv, shell=False, cwd=cwd)
    except Exception as exc:
        return "", f"Failed to start command: {exc}", 1


# ── Public API ────────────────────────────────────────────────────────────────

def _record_mission(
    name: str,
    command: str,
    repo_path: Path | None = None,
    command_mode: str = "shell",
    command_argv: list[str] | None = None,
    attest: bool = False,
) -> dict:
    """Run *command*, record everything, and return a result dict.

    Parameters
    ----------
    name:       Human-readable label for this mission (used in mission_id).
    command:       Readable command string for display and manifests.
    repo_path:     Root directory of the project; defaults to the current directory.
    command_mode:  ``"shell"`` for shell-string commands, ``"argv"`` for list commands.
    command_argv:  Argument list used when command_mode is ``"argv"``.

    Returns
    -------
    dict with keys: mission_id, name, command, exit_code, status,
    changed_files_count, session_dir (Path), zip_path (Path),
    seal_path (Path), archive_sha256 (str),
    seal_card_path (Path), seal_payload_path (Path).
    """
    if repo_path is None:
        repo_path = Path.cwd()
    if command_mode not in {"shell", "argv"}:
        raise ValueError(f"Unsupported command_mode: {command_mode}")
    if command_mode == "argv" and not command_argv:
        raise ValueError("command_argv is required when command_mode is 'argv'")

    mission_id = _make_mission_id(name)
    sessions_dir = repo_path / "blackbox" / "sessions"
    session_dir = sessions_dir / mission_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Prefix used to exclude the recorder's own output files from the
    # "changed files" list (they aren't part of what the command changed).
    sessions_prefix = "blackbox/sessions/"

    # ── Git state before ──────────────────────────────────────────────────────
    branch = git_tools.get_branch(cwd=repo_path)
    commit_before = git_tools.get_commit(cwd=repo_path)
    status_before = git_tools.get_status_short(cwd=repo_path)

    # ── Run the command ───────────────────────────────────────────────────────
    started_at = datetime.now(timezone.utc)
    if command_mode == "argv":
        stdout_text, stderr_text, exit_code = _run_argv_command(
            command_argv,
            cwd=repo_path,
        )
    else:
        stdout_text, stderr_text, exit_code = _run_shell_command(command, cwd=repo_path)
    ended_at = datetime.now(timezone.utc)
    duration_seconds = (ended_at - started_at).total_seconds()

    # ── Git state after ───────────────────────────────────────────────────────
    commit_after = git_tools.get_commit(cwd=repo_path)
    status_after = git_tools.get_status_short(cwd=repo_path)
    diff_text = git_tools.get_diff(cwd=repo_path)
    changed_files = git_tools.get_changed_files(
        cwd=repo_path, exclude_prefix=sessions_prefix
    )

    # ── Build manifest ────────────────────────────────────────────────────────
    manifest = {
        "mission_id": mission_id,
        "name": name,
        "repo_path": str(repo_path),
        "command": command,
        "command_mode": command_mode,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "exit_code": exit_code,
        "git_branch": branch,
        "git_commit_before": commit_before,
        "git_commit_after": commit_after,
        "changed_files_count": len(changed_files),
        "changed_files": changed_files,
    }
    if command_mode == "argv":
        manifest["command_argv"] = command_argv

    # ── Write raw artefacts (no summary yet — seal comes first) ──────────────
    manifest_path = session_dir / "manifest.json"

    def _write(filename: str, content: str) -> None:
        (session_dir / filename).write_text(content, encoding="utf-8")

    _write("manifest.json",         json.dumps(manifest, indent=2))
    _write("stdout.txt",            stdout_text)
    _write("stderr.txt",            stderr_text)
    _write("git_status_before.txt", status_before)
    _write("git_status_after.txt",  status_after)
    _write("git_diff.patch",        diff_text)

    # ── Flight plan (provenance) ──────────────────────────────────────────────
    # A pending flight plan (blackbox/flightplan.pending.json) is consumed
    # into the session BEFORE compressing/sealing so the seal covers it.
    from sao.provenance import flightplan as flightplan_mod

    consumed_flightplan = flightplan_mod.consume_pending(repo_path, session_dir)

    # ── Compress ──────────────────────────────────────────────────────────────
    # Compress before sealing so the archive SHA256 goes into the seal.
    zip_path = compressor.compress_session(session_dir)

    # ── Seal ──────────────────────────────────────────────────────────────────
    # Hashes raw data files and the archive.  Card and summary files are
    # written after this point and are excluded from the directory hash.
    seal_data = seal_mod.write_seal(
        session_dir=session_dir,
        archive_path=zip_path,
        manifest_path=manifest_path,
    )
    seal_path = session_dir / "seal.json"

    # ── Seal card (compact payload + shareable Markdown card) ─────────────────
    payload = card_mod.build_seal_payload(
        manifest=manifest,
        seal=seal_data,
        exit_code=exit_code,
        changed_files=changed_files,
    )
    card_paths = card_mod.write_seal_card(session_dir=session_dir, payload=payload)

    # ── QR payload (compact JSON for QR encoding) ─────────────────────────────
    qr_paths = qr_mod.write_qr_payload(session_dir=session_dir, seal_payload=payload)

    # QR image is derived from the compact payload and remains outside the seal.
    qr_image_path = qr_image_mod.write_qr_image(
        session_dir=session_dir,
        qr_payload_txt_path=qr_paths["qr_payload_txt_path"],
    )
    qr_paths["qr_image_path"] = qr_image_path

    # ── HTML card (standalone, no external assets) ────────────────────────────
    qr_text = (session_dir / "seal_qr_payload.txt").read_text(encoding="utf-8")
    html_card_path = html_mod.write_html_card(
        session_dir=session_dir,
        payload=payload,
        qr_payload_text=qr_text,
        qr_image_path=qr_image_path,
    )

    # ── Summary (written last — references seal hashes and card paths) ────────
    _write(
        "mission_summary.md",
        summary.generate_summary(
            manifest,
            stdout_text,
            stderr_text,
            seal=seal_data,
            card_paths=card_paths,
            qr_paths=qr_paths,
            html_card_path=html_card_path,
        ),
    )

    status = "PASS" if exit_code == 0 else "FAIL"

    # ── Attestation (provenance, opt-in) ──────────────────────────────────────
    # Appends the mission to the transparency ledger, writes provenance.json
    # (excluded from the seal's directory hash), and attaches a git note to
    # the new HEAD commit when the mission ended on one.
    attest_result = None
    if attest:
        from sao.provenance import attest as attest_mod

        attest_result = attest_mod.attest_session(repo_path, session_dir)

    return {
        "flightplan_consumed":  consumed_flightplan is not None,
        "attestation":          attest_result,
        "mission_id":           mission_id,
        "name":                 name,
        "command":              command,
        "command_mode":         command_mode,
        "command_argv":         command_argv,
        "exit_code":            exit_code,
        "status":               status,
        "changed_files_count":  len(changed_files),
        "session_dir":          session_dir,
        "zip_path":             zip_path,
        "seal_path":            seal_path,
        "archive_sha256":       seal_data["archive_sha256"],
        "seal_card_path":       card_paths["seal_card_path"],
        "seal_payload_path":    card_paths["seal_payload_path"],
        "qr_payload_json_path": qr_paths["qr_payload_json_path"],
        "qr_payload_txt_path":  qr_paths["qr_payload_txt_path"],
        "qr_image_path":        qr_image_path,
        "html_card_path":       html_card_path,
    }


def record_mission(
    name: str,
    command: str,
    repo_path: Path = None,
    attest: bool = False,
) -> dict:
    """Run a shell command string, record everything, and return a result dict."""
    return _record_mission(
        name=name,
        command=command,
        repo_path=repo_path,
        command_mode="shell",
        attest=attest,
    )


def record_mission_argv(
    name: str,
    command_argv: list[str],
    repo_path: Path = None,
    attest: bool = False,
) -> dict:
    """Run an argv command without a shell, record everything, and return a result dict."""
    command = format_command_argv(command_argv)
    return _record_mission(
        name=name,
        command=command,
        repo_path=repo_path,
        command_mode="argv",
        command_argv=command_argv,
        attest=attest,
    )
