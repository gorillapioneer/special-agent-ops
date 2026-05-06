"""
cli.py — Special Agent Ops command-line interface.

Run a shell command and record everything about it:
  git state, stdout, stderr, changed files, and a compressed archive.

Usage:
    python -m sao.cli run --name "my mission" --command "pytest"

    # or, after `pip install -e .`:
    sao run --name "my mission" --command "pytest"
"""

import argparse
import sys
from pathlib import Path

from sao.blackbox.recorder import record_mission


# ── Sub-commands ──────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    """Record one mission and print the result summary."""
    print(f"\n  Mission:  {args.name!r}")
    print(f"  Command:  {args.command}")
    print(f"  Working directory: {Path.cwd()}\n")

    result = record_mission(
        name=args.name,
        command=args.command,
        repo_path=Path.cwd(),
    )

    _print_banner(result)
    return result["exit_code"]


def _print_banner(result: dict) -> None:
    width = 64
    bar = "=" * width
    archive_sha256 = result.get("archive_sha256", "")
    # Show first 16 + last 8 chars so it fits the terminal without wrapping.
    sha_display = (
        f"{archive_sha256[:16]}...{archive_sha256[-8:]}"
        if len(archive_sha256) == 64
        else archive_sha256
    )
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — MISSION COMPLETE")
    print(bar)
    print(f"  Mission ID:      {result['mission_id']}")
    print(f"  Command:         {result['command']}")
    print(f"  Exit Code:       {result['exit_code']}")
    print(f"  Changed Files:   {result['changed_files_count']}")
    print(f"  Session Folder:  {result['session_dir']}")
    print(f"  Archive:         {result['zip_path']}")
    print(f"  Archive SHA256:  {sha_display}")
    print(f"  Seal:            {result.get('seal_path', 'n/a')}")
    print(bar)
    print()


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sao",
        description=(
            "Special Agent Ops — black box recorder for AI coding agent sessions.\n"
            "\n"
            "Records git state, command output, and changed files, then compresses\n"
            "everything into a timestamped archive under blackbox/sessions/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── `run` sub-command ─────────────────────────────────────────────────────
    run_p = sub.add_parser(
        "run",
        help="Run a command and record a mission session.",
        description="Run a shell command inside the current repo and record everything.",
    )
    run_p.add_argument(
        "--name",
        required=True,
        help='Human-readable label for this mission, e.g. "pytest baseline".',
    )
    run_p.add_argument(
        "--command",
        required=True,
        help='Shell command to execute and record, e.g. "python -m pytest".',
    )
    run_p.set_defaults(func=cmd_run)

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
