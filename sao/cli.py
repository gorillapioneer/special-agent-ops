"""
cli.py — Special Agent Ops command-line interface.

Commands:
    sao run --name "my mission" --command "pytest"
    sao wrap --name "my mission" -- pytest
    sao map
    sao list
    sao show <mission_id>
    sao verify <mission_id>
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from sao.blackbox.recorder import format_command_argv, record_mission, record_mission_argv
from sao.blackbox import browser, dashboard as dashboard_mod, maproom as maproom_mod


# ── run ───────────────────────────────────────────────────────────────────────

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


def cmd_wrap(args) -> int:
    """Record one argv command without invoking a shell."""
    command_argv = list(args.command_argv)
    if command_argv and command_argv[0] == "--":
        command_argv = command_argv[1:]
    if not command_argv:
        print(
            'Error: wrap requires a command after "--", e.g. '
            'sao wrap --name "python version" -- python --version',
            file=sys.stderr,
        )
        return 2

    command_display = format_command_argv(command_argv)
    print(f"\n  Mission:  {args.name!r}")
    print(f"  Command:  {command_display}")
    print(f"  Mode:     argv")
    print(f"  Working directory: {Path.cwd()}\n")

    result = record_mission_argv(
        name=args.name,
        command_argv=command_argv,
        repo_path=Path.cwd(),
    )

    _print_banner(result)
    return result["exit_code"]


def _print_banner(result: dict) -> None:
    width = 64
    bar = "=" * width
    archive_sha256 = result.get("archive_sha256", "")
    sha_display = (
        f"{archive_sha256[:16]}...{archive_sha256[-8:]}"
        if len(archive_sha256) == 64
        else archive_sha256
    )
    status = result.get("status", "PASS" if result.get("exit_code", 0) == 0 else "FAIL")
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — MISSION COMPLETE")
    print(bar)
    print(f"  Mission ID:      {result['mission_id']}")
    print(f"  Status:          {status}")
    print(f"  Command:         {result['command']}")
    if result.get("command_mode"):
        print(f"  Command Mode:    {result['command_mode']}")
    print(f"  Exit Code:       {result['exit_code']}")
    print(f"  Changed Files:   {result['changed_files_count']}")
    print(f"  Session Folder:  {result['session_dir']}")
    print(f"  Archive:         {result['zip_path']}")
    print(f"  Archive SHA256:  {sha_display}")
    print(f"  Seal:            {result.get('seal_path', 'n/a')}")
    print(f"  Seal Card:       {result.get('seal_card_path', 'n/a')}")
    print(f"  HTML Card:       {result.get('html_card_path', 'n/a')}")
    print(f"  QR Payload:      {result.get('qr_payload_json_path', 'n/a')}")
    print(f"  QR Image:        {result.get('qr_image_path', 'n/a')}")
    print(bar)
    print()


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    missions = browser.list_missions(sessions_root)

    if not missions:
        print("No missions recorded yet.")
        print(f"Sessions directory: {sessions_root}")
        return 0

    # Column widths
    id_w  = max(len("Mission ID"),  max(len(m["mission_id"]) for m in missions))
    st_w  = max(len("Status"),      max(len(m["status"])     for m in missions))
    ch_w  = max(len("Changed"),     max(len(str(m["changed_files_count"])) for m in missions))
    cmd_w = max(len("Command"),     min(40, max(len(m["command"]) for m in missions)))

    header = (
        f"{'Mission ID':<{id_w}}  {'Status':<{st_w}}  {'Changed':>{ch_w}}  "
        f"{'Command':<{cmd_w}}"
    )
    sep = "-" * len(header)
    print()
    print(header)
    print(sep)
    for m in missions:
        cmd_display = m["command"] if len(m["command"]) <= cmd_w else m["command"][:cmd_w - 3] + "..."
        print(
            f"{m['mission_id']:<{id_w}}  "
            f"{m['status']:<{st_w}}  "
            f"{str(m['changed_files_count']):>{ch_w}}  "
            f"{cmd_display:<{cmd_w}}"
        )
    print()
    print(f"  {len(missions)} mission(s) in {sessions_root}")
    print()
    return 0


# ── show ──────────────────────────────────────────────────────────────────────

def cmd_show(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    try:
        session_dir = browser.find_mission(sessions_root, args.mission_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        m = browser.load_manifest(session_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    exit_code = m.get("exit_code", -1)
    status = "PASS" if exit_code == 0 else "FAIL"

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — MISSION DETAIL")
    print(bar)
    print(f"  Mission ID:       {m.get('mission_id', '?')}")
    print(f"  Name:             {m.get('name', '?')}")
    print(f"  Status:           {status}")
    print(f"  Started:          {m.get('started_at', '?')}")
    print(f"  Ended:            {m.get('ended_at', '?')}")
    print(f"  Duration:         {m.get('duration_seconds', '?')}s")
    print(f"  Command:          {m.get('command', '?')}")
    if m.get("command_mode"):
        print(f"  Command Mode:     {m.get('command_mode')}")
    print(f"  Exit Code:        {exit_code}")
    print(f"  Changed Files:    {m.get('changed_files_count', '?')}")

    # Seal for archive SHA256
    try:
        seal = browser.load_seal(session_dir)
        sha = seal.get("archive_sha256", "?")
        sha_display = f"{sha[:16]}...{sha[-8:]}" if len(sha) == 64 else sha
        print(f"  Archive SHA256:   {sha_display}")
    except FileNotFoundError:
        print(f"  Archive SHA256:   (seal.json not found)")

    print(f"  Seal Card:        {session_dir / 'seal_card.md'}")
    print(f"  Mission Summary:  {session_dir / 'mission_summary.md'}")
    print(f"  QR Payload:       {session_dir / 'seal_qr_payload.txt'}")
    print(f"  QR Image:         {browser.get_qr_image_path(session_dir)}")
    print(bar)
    print()
    return 0


# ── verify ────────────────────────────────────────────────────────────────────

def cmd_verify(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    try:
        session_dir = browser.find_mission(sessions_root, args.mission_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        result = browser.verify_mission(session_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    def _ok(flag: bool) -> str:
        return "OK" if flag else "FAILED"

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — VERIFY")
    print(bar)
    print(f"  Mission ID:        {result['mission_id']}")
    print(f"  Manifest:          {_ok(result['manifest_ok'])}")
    print(f"  Archive:           {_ok(result['archive_ok'])}")
    print(f"  Session Directory: {_ok(result['session_directory_ok'])}")
    print(bar)
    if result["verified"]:
        print("  Result: VERIFIED")
    else:
        print("  Result: FAILED")
        if not result["archive_found"]:
            print("  (archive .zip not found alongside session directory)")
    print(bar)
    print()

    return 0 if result["verified"] else 1


# ── dashboard ────────────────────────────────────────────────────────────────

def cmd_dashboard(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    dashboard_mod.run_dashboard(
        sessions_root=sessions_root,
        host="127.0.0.1",
        port=args.port,
    )
    return 0


# ── map ───────────────────────────────────────────────────────────────────────

def cmd_map(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    output_path = Path(args.output) if args.output else None
    map_path = maproom_mod.write_maproom(
        sessions_root=sessions_root,
        output_path=output_path,
    )

    missions = maproom_mod.collect_maproom_missions(sessions_root)
    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — MAPROOM")
    print(bar)
    print(f"  Output:    {map_path}")
    print(f"  Missions:  {len(missions)}")
    print(bar)
    if args.open:
        webbrowser.open(map_path.resolve().as_uri())
        print("  Result: OPENED")
        print(bar)
    print()
    return 0


# ── open ─────────────────────────────────────────────────────────────────────

def cmd_open(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    try:
        session_dir = browser.find_mission(sessions_root, args.mission_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        html_path = browser.open_html_card(session_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — OPEN")
    print(bar)
    print(f"  Mission ID:  {args.mission_id}")
    print(f"  HTML Card:   {html_path}")
    print(bar)
    print("  Result: OPENED")
    print(bar)
    print()
    return 0


# ── verify-archive ───────────────────────────────────────────────────────────

def cmd_verify_archive(args) -> int:
    archive_path = Path(args.archive_path).resolve()

    try:
        result = browser.verify_archive_file(archive_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    def _ok(flag: bool) -> str:
        return "OK" if flag else "FAILED"

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — VERIFY ARCHIVE")
    print(bar)
    print(f"  Archive:           {result['archive_path']}")
    print(f"  Mission ID:        {result['mission_id']}")
    print(f"  Archive SHA256:    {_ok(result['archive_ok'])}")
    print(f"  Manifest:          {_ok(result['manifest_ok'])}")
    print(f"  Session Directory: {_ok(result['session_directory_ok'])}")
    print(bar)
    if result["verified"]:
        print("  Result: VERIFIED")
    else:
        print("  Result: FAILED")
    print(bar)
    print()

    return 0 if result["verified"] else 1


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sao",
        description=(
            "Special Agent Ops — black box recorder and mission browser.\n"
            "\n"
            "Commands:\n"
            "  run             Record a command as a mission session.\n"
            "  wrap            Record an argv command without a shell.\n"
            "  list            List all recorded missions.\n"
            "  show            Inspect a mission session.\n"
            "  open            Open a mission HTML card in the default browser.\n"
            "  dashboard       Start a local dashboard server.\n"
            "  map             Generate a standalone MapRoom mission timeline.\n"
            "  verify          Verify SHA256 seals for a mission session.\n"
            "  verify-archive  Verify a mission .zip archive directly.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── run ───────────────────────────────────────────────────────────────────
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

    # â”€â”€ wrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    wrap_p = sub.add_parser(
        "wrap",
        help="Run a command argv list and record a mission session.",
        description=(
            "Run a command without shell=True and record everything. Use --\n"
            "before the wrapped command so its flags are passed through."
        ),
    )
    wrap_p.add_argument(
        "--name",
        required=True,
        help='Human-readable label for this mission, e.g. "codex session".',
    )
    wrap_p.add_argument(
        "command_argv",
        nargs=argparse.REMAINDER,
        metavar="COMMAND",
        help="Command and arguments to execute after --.",
    )
    wrap_p.set_defaults(func=cmd_wrap)

    # ── list ──────────────────────────────────────────────────────────────────
    list_p = sub.add_parser(
        "list",
        help="List all recorded mission sessions.",
        description="Print a compact table of all missions in blackbox/sessions/.",
    )
    list_p.set_defaults(func=cmd_list)

    # ── show ──────────────────────────────────────────────────────────────────
    show_p = sub.add_parser(
        "show",
        help="Show details for a recorded mission.",
        description="Print full metadata for a specific mission session.",
    )
    show_p.add_argument("mission_id", help="Mission ID, e.g. 20260506_091500_pytest_baseline")
    show_p.set_defaults(func=cmd_show)

    # ── dashboard ─────────────────────────────────────────────────────────────
    dash_p = sub.add_parser(
        "dashboard",
        help="Start a local mission dashboard server.",
        description=(
            "Serve a local dashboard at http://127.0.0.1:<port> listing all\n"
            "recorded missions with links to their HTML cards, summaries,\n"
            "and QR payloads.  Only serves known files from known session\n"
            "folders — no arbitrary file access."
        ),
    )
    dash_p.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port to listen on (default: 8765).",
    )
    dash_p.set_defaults(func=cmd_dashboard)

    # ── map ───────────────────────────────────────────────────────────────────
    map_p = sub.add_parser(
        "map",
        help="Generate a standalone MapRoom mission timeline.",
        description="Generate blackbox/maproom.html from recorded mission manifests.",
    )
    map_p.add_argument(
        "--output",
        help="Output HTML path (default: blackbox/maproom.html).",
    )
    map_p.add_argument(
        "--open",
        action="store_true",
        help="Open the generated MapRoom HTML in the default browser.",
    )
    map_p.set_defaults(func=cmd_map)

    # ── open ──────────────────────────────────────────────────────────────────
    open_p = sub.add_parser(
        "open",
        help="Open a mission HTML card in the default browser.",
        description="Open seal_card.html for a recorded mission in the system default browser.",
    )
    open_p.add_argument("mission_id", help="Mission ID to open, e.g. 20260506_091500_pytest_baseline")
    open_p.set_defaults(func=cmd_open)

    # ── verify ────────────────────────────────────────────────────────────────
    verify_p = sub.add_parser(
        "verify",
        help="Verify SHA256 seals for a mission session.",
        description="Recompute and confirm manifest, archive, and session directory hashes.",
    )
    verify_p.add_argument("mission_id", help="Mission ID to verify.")
    verify_p.set_defaults(func=cmd_verify)

    # ── verify-archive ────────────────────────────────────────────────────────
    va_p = sub.add_parser(
        "verify-archive",
        help="Verify a mission .zip archive directly.",
        description=(
            "Extract a mission archive to a temp folder and verify all SHA256 seals.\n"
            "The original session folder is not required."
        ),
    )
    va_p.add_argument(
        "archive_path",
        help="Path to the mission .zip archive, e.g. blackbox/sessions/20260506_091500_pytest_baseline.zip",
    )
    va_p.set_defaults(func=cmd_verify_archive)

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
