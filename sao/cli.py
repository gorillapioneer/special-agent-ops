"""
cli.py — Special Agent Ops command-line interface.

Commands:
    sao run --name "my mission" --command "pytest"
    sao wrap --name "my mission" -- pytest
    sao map
    sao pr-report <mission_id>
    sao list
    sao show <mission_id>
    sao verify <mission_id>
    sao flight-plan --name "..." --intent "..." --scope "sao/**"
    sao attest <mission_id>
    sao ledger root | sao ledger verify
    sao verify-pr --base main --head HEAD [--min-tier ci-verified]
    sao ci-issue --commit <oid> --signer hmac
    sao ci-verify --commit <oid> --attestation <path>
    sao checkpoint emit | sao checkpoint verify --checkpoint <path>
    sao witness cosign --checkpoint <path> --state-dir <dir> --name <name>
    sao anchor push --remote <url> | sao anchor verify --remote <url>
    sao blame <file>
    sao mcp
"""

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from sao.blackbox.recorder import format_command_argv, record_mission, record_mission_argv
from sao.blackbox import (
    browser,
    dashboard as dashboard_mod,
    maproom as maproom_mod,
    pr_report as pr_report_mod,
)
from sao.provenance import (
    anchor as anchor_mod,
    attest as attest_mod,
    blame as blame_mod,
    checkpoint as checkpoint_mod,
    ci_issue as ci_issue_mod,
    envelope as envelope_mod,
    flightplan as flightplan_mod,
    ledger as ledger_mod,
    mcp_server as mcp_mod,
    verify_pr as verify_pr_mod,
    witness as witness_mod,
)


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
        attest=args.attest,
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
        attest=args.attest,
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
    if result.get("flightplan_consumed"):
        print(f"  Flight Plan:     consumed into session (flightplan.json)")
    attestation = result.get("attestation")
    if attestation:
        ledger_pos = attestation["statement"]["ledger"]
        print(f"  Attestation:     {attestation['provenance_path']}")
        print(f"  Ledger Leaf:     #{ledger_pos['leaf_index']} (tree size {ledger_pos['tree_size']})")
        if attestation.get("note_attached"):
            print(f"  Git Note:        refs/notes/sao -> {attestation['note_commit'][:10]}")
        else:
            print(f"  Git Note:        not attached (no new commit)")
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


# -- pr-report ---------------------------------------------------------------

def cmd_pr_report(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    try:
        session_dir = browser.find_mission(sessions_root, args.mission_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        if args.output:
            report_path = pr_report_mod.write_pr_report(
                session_dir=session_dir,
                output_path=Path(args.output),
            )
            print(report_path)
        else:
            payload = pr_report_mod.build_pr_report_payload(session_dir)
            markdown = pr_report_mod.render_pr_report_markdown(payload)
            print(markdown, end="" if markdown.endswith("\n") else "\n")
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


# -- open --------------------------------------------------------------------

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


# ── flight-plan ──────────────────────────────────────────────────────────────

def cmd_flight_plan(args) -> int:
    try:
        path = flightplan_mod.file_flight_plan(
            repo_path=Path.cwd(),
            name=args.name,
            intent=args.intent,
            scope=args.scope,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — FLIGHT PLAN FILED")
    print(bar)
    print(f"  Name:    {args.name}")
    print(f"  Intent:  {args.intent}")
    for g in args.scope:
        print(f"  Scope:   {g}")
    print(f"  Pending: {path}")
    print(bar)
    print("  The next recorded mission will consume this plan.")
    print(bar)
    print()
    return 0


# ── attest ───────────────────────────────────────────────────────────────────

def cmd_attest(args) -> int:
    sessions_root = browser.get_sessions_root(Path.cwd())
    try:
        session_dir = browser.find_mission(sessions_root, args.mission_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        result = attest_mod.attest_session(Path.cwd(), session_dir)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    statement = result["statement"]
    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — ATTESTATION")
    print(bar)
    print(f"  Mission ID:      {statement['mission_id']}")
    print(f"  Attestation:     {result['provenance_path']}")
    print(f"  Statement SHA:   {result['attestation_sha256'][:16]}...")
    print(f"  Ledger Leaf:     #{statement['ledger']['leaf_index']} "
          f"(tree size {statement['ledger']['tree_size']})")
    print(f"  Ledger Root:     {statement['ledger']['root'][:16]}...")
    parent = statement.get("parent_attestation_sha256")
    print(f"  Parent:          {parent[:16] + '...' if parent else '(chain start)'}")
    if result["note_attached"]:
        print(f"  Git Note:        refs/notes/sao -> {result['note_commit'][:10]}")
    else:
        print(f"  Git Note:        not attached (mission did not end on a new commit)")
    if result["signature_path"]:
        print(f"  Signature:       {result['signature_path']}")
    print(bar)
    print()
    return 0


# ── ledger ───────────────────────────────────────────────────────────────────

def cmd_ledger_root(args) -> int:
    ledger = ledger_mod.Ledger(Path.cwd())
    root_info = ledger.root()
    print(json.dumps(root_info, indent=2))

    if args.qr:
        payload = ledger_mod.build_root_qr_payload(root_info)
        try:
            from sao.blackbox import qr_image as qr_image_mod

            qr_path = qr_image_mod.generate_qr_png(payload, Path(args.qr))
            print(f"QR image written: {qr_path}", file=sys.stderr)
        except RuntimeError as e:
            print(f"QR image skipped: {e}", file=sys.stderr)
    return 0


def cmd_ledger_verify(args) -> int:
    ledger = ledger_mod.Ledger(Path.cwd())
    result = ledger.verify_log()

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — LEDGER VERIFY")
    print(bar)
    print(f"  Ledger:     {ledger.path}")
    print(f"  Tree Size:  {result['tree_size']}")
    print(f"  Root Hash:  {result['root_hash']}")
    print(bar)
    for entry in result["entries"]:
        leaf_state = (
            "leaf recomputed OK" if entry["leaf_ok"]
            else "LEAF MISMATCH" if entry["leaf_recomputed"]
            else "session gone (leaf kept)"
        )
        inc_state = "inclusion OK" if entry["inclusion_ok"] else "INCLUSION FAILED"
        print(f"  #{entry['index']}  {entry['mission_id']}")
        print(f"      {leaf_state}; {inc_state}")
    print(bar)
    if result["ok"]:
        print("  Result: VERIFIED")
    else:
        print("  Result: FAILED")
        for problem in result["problems"]:
            print(f"  - {problem}")
    print(bar)
    print()
    return 0 if result["ok"] else 1


# ── ci-issue / ci-verify ─────────────────────────────────────────────────────

def cmd_ci_issue(args) -> int:
    report = ci_issue_mod.issue(
        repo_path=Path.cwd(),
        commit=args.commit,
        mission_id=args.session,
        signer_kind=args.signer,
        key_file=args.key_file,
        out_path=Path(args.out) if args.out else None,
        allow_failed_checks=args.allow_failed_checks,
        strict_scope=args.strict_scope,
    )
    print(ci_issue_mod.render_report(report, "CI ISSUE"))
    return 0 if report["ok"] else 1


def cmd_ci_verify(args) -> int:
    report = ci_issue_mod.ci_verify(
        repo_path=Path.cwd(),
        commit=args.commit,
        attestation_path=Path(args.attestation),
        hmac_key_file=args.hmac_key_file,
        allowed_signers=args.allowed_signers,
        signer_identity=args.signer_identity,
    )
    print(ci_issue_mod.render_report(report, "CI VERIFY"))
    return 0 if report["ok"] else 1


# ── checkpoint ───────────────────────────────────────────────────────────────

def cmd_checkpoint_emit(args) -> int:
    try:
        signer = checkpoint_mod.make_operator_signer(args.signer, args.key_file)
        cp = checkpoint_mod.build_checkpoint(
            Path.cwd(),
            origin=args.origin,
            bundle_proof_from=args.bundle_proof_from,
        )
        checkpoint_mod.sign_checkpoint(cp, signer)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    out_path = (
        Path(args.out) if args.out
        else Path.cwd() / checkpoint_mod.DEFAULT_CHECKPOINT_PATH
    )
    checkpoint_mod.write_checkpoint(cp, out_path)

    width = 64
    bar = "=" * width
    print()
    print(bar)
    print("  SPECIAL AGENT OPS — CHECKPOINT")
    print(bar)
    print(f"  Origin:     {cp['origin']}")
    print(f"  Tree Size:  {cp['tree_size']}")
    print(f"  Root Hash:  {cp['root_hash']}")
    print(f"  Signed:     {'yes (' + args.signer + ')' if cp['signature'] else 'NO — UNSIGNED'}")
    if cp.get("bundled_proofs"):
        print(f"  Bundled:    consistency proof from size "
              f"{cp['bundled_proofs'][0]['old_size']}")
    print(f"  Checkpoint: {out_path}")
    print(bar)
    print("  Hand this file to witnesses (sao witness cosign) — they run")
    print("  OUTSIDE this repo and need ledger access via their own clone")
    print("  or the bundled proof (--bundle-proof-from).")
    print(bar)
    print()
    return 0


def cmd_checkpoint_verify(args) -> int:
    try:
        cp = checkpoint_mod.load_checkpoint(args.checkpoint)
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    report = checkpoint_mod.verify_checkpoint(
        Path.cwd(),
        cp,
        require_witnesses=args.require_witnesses,
        witness_keys_path=args.witness_keys,
        hmac_key_file=args.hmac_key_file,
        allowed_signers=args.allowed_signers,
        identity=args.signer_identity,
    )
    print(checkpoint_mod.render_report(report, "CHECKPOINT VERIFY"))
    return 0 if report["ok"] else 1


# ── witness ──────────────────────────────────────────────────────────────────

def cmd_witness_cosign(args) -> int:
    try:
        signer = checkpoint_mod.make_cosign_signer(args.signer, args.key_file)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    report = witness_mod.cosign(
        checkpoint_path=args.checkpoint,
        state_dir=args.state_dir,
        name=args.name,
        signer=signer,
        ledger_repo=args.ledger_repo,
        operator_hmac_key_file=args.operator_hmac_key_file,
        operator_allowed_signers=args.operator_allowed_signers,
    )
    print(witness_mod.render_report(report))
    return 0 if report["ok"] else 1


def cmd_witness_state(args) -> int:
    states = witness_mod.list_states(args.state_dir)
    print(witness_mod.render_states(states, args.state_dir))
    return 0


# ── anchor ───────────────────────────────────────────────────────────────────

def cmd_anchor_push(args) -> int:
    report = anchor_mod.push(
        repo_path=Path.cwd(),
        remote=args.remote,
        ref=args.ref,
        checkpoint_path=args.checkpoint,
        origin=args.origin,
    )
    print(anchor_mod.render_report(report, "ANCHOR PUSH"))
    return 0 if report["ok"] else 1


def cmd_anchor_verify(args) -> int:
    report = anchor_mod.verify(
        repo_path=Path.cwd(),
        remote=args.remote,
        ref=args.ref,
        origin=args.origin,
        max_age_days=args.max_age_days,
    )
    print(anchor_mod.render_report(report, "ANCHOR VERIFY"))
    return 0 if report["ok"] else 1


# ── verify-pr ────────────────────────────────────────────────────────────────

def cmd_verify_pr(args) -> int:
    try:
        report = verify_pr_mod.verify_pr(
            repo_path=Path.cwd(),
            base=args.base,
            head=args.head,
            require_attestation=args.require_attestation,
            strict_scope=args.strict_scope,
            min_tier=args.min_tier,
            ci_hmac_key_file=args.ci_hmac_key_file,
            ci_attestations_dir=args.ci_attestations_dir,
            witness_keys=args.witness_keys,
            require_witnesses=args.require_witnesses,
            checkpoint_path=args.checkpoint,
            anchors_remote=args.anchors_remote,
            anchors_ref=args.anchors_ref,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(verify_pr_mod.render_text(report))
    if args.markdown:
        md_path = Path(args.markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(verify_pr_mod.render_markdown(report), encoding="utf-8")
        print(f"Markdown report: {md_path}")
    return 0 if report["ok"] else 1


# ── blame ────────────────────────────────────────────────────────────────────

def cmd_blame(args) -> int:
    try:
        result = blame_mod.blame_file(Path.cwd(), args.file)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(blame_mod.render_text(result))
    return 0


# ── mcp ──────────────────────────────────────────────────────────────────────

def cmd_mcp(args) -> int:
    return mcp_mod.serve(repo_path=Path.cwd())


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
            "  pr-report       Print a GitHub PR-ready mission report.\n"
            "  verify          Verify SHA256 seals for a mission session.\n"
            "  verify-archive  Verify a mission .zip archive directly.\n"
            "  flight-plan     Pre-declare the scope of the next mission.\n"
            "  attest          Build a provenance attestation for a mission.\n"
            "  ledger          Merkle transparency log (root / verify).\n"
            "  verify-pr       Verify provenance for all commits in a PR range.\n"
            "  ci-issue        Issue a CI attestation (DSSE) after verifying evidence.\n"
            "  ci-verify       Verify a CI-issued DSSE attestation for a commit.\n"
            "  checkpoint      Emit / verify signed ledger checkpoints (witnessable).\n"
            "  witness         Independent witness: cosign checkpoints, refuse forks.\n"
            "  anchor          Anchor checkpoints on an external git remote.\n"
            "  blame           Line-level attribution for a file (best-effort).\n"
            "  mcp             Run the provenance MCP server over stdio.\n"
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
    run_p.add_argument(
        "--attest",
        action="store_true",
        help="Append the mission to the transparency ledger and attach a "
             "git attestation note after recording (off by default).",
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
        "--attest",
        action="store_true",
        help="Append the mission to the transparency ledger and attach a "
             "git attestation note after recording (off by default).",
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

    # -- pr-report -----------------------------------------------------------
    pr_report_p = sub.add_parser(
        "pr-report",
        help="Print a GitHub PR-ready mission report.",
        description=(
            "Generate Markdown from a recorded mission that can be pasted "
            "directly into a GitHub pull request."
        ),
    )
    pr_report_p.add_argument("mission_id", help="Mission ID to report.")
    pr_report_p.add_argument(
        "--output",
        help="Write the report to a Markdown file instead of stdout.",
    )
    pr_report_p.set_defaults(func=cmd_pr_report)

    # -- open ----------------------------------------------------------------
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

    # ── flight-plan ───────────────────────────────────────────────────────────
    fp_p = sub.add_parser(
        "flight-plan",
        help="Pre-declare the scope of the next recorded mission.",
        description=(
            "Write blackbox/flightplan.pending.json.  The next recorded\n"
            "mission consumes it into the sealed session (flightplan.json)\n"
            "and references it from the mission's attestation."
        ),
    )
    fp_p.add_argument("--name", required=True, help="Mission name the plan is for.")
    fp_p.add_argument("--intent", required=True, help="What the mission intends to do.")
    fp_p.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="GLOB",
        help="fnmatch glob of repo-relative paths the mission may change "
             "(repeatable).",
    )
    fp_p.set_defaults(func=cmd_flight_plan)

    # ── attest ────────────────────────────────────────────────────────────────
    attest_p = sub.add_parser(
        "attest",
        help="Build a provenance attestation for a recorded mission.",
        description=(
            "Append the mission to the transparency ledger (if not present),\n"
            "write provenance.json into the session folder, and attach the\n"
            "canonical statement as a git note (refs/notes/sao) when the\n"
            "mission ended on a new commit."
        ),
    )
    attest_p.add_argument("mission_id", help="Mission ID to attest.")
    attest_p.set_defaults(func=cmd_attest)

    # ── ledger ────────────────────────────────────────────────────────────────
    ledger_p = sub.add_parser(
        "ledger",
        help="Merkle transparency log over mission seals.",
        description="Inspect and verify blackbox/ledger.jsonl (RFC 6962-style Merkle log).",
    )
    ledger_sub = ledger_p.add_subparsers(dest="ledger_command", required=True)

    ledger_root_p = ledger_sub.add_parser(
        "root",
        help="Print the current tree size and Merkle root hash.",
        description="Print {tree_size, root_hash} for the current ledger.",
    )
    ledger_root_p.add_argument(
        "--qr",
        metavar="PATH",
        help="Also write a QR image of the root payload to PATH.",
    )
    ledger_root_p.set_defaults(func=cmd_ledger_root)

    ledger_verify_p = ledger_sub.add_parser(
        "verify",
        help="Re-verify the whole ledger (leaves + inclusion proofs).",
        description=(
            "Recompute leaf hashes from session seals where sessions still\n"
            "exist and verify every entry's inclusion proof against the root."
        ),
    )
    ledger_verify_p.set_defaults(func=cmd_ledger_verify)

    # ── verify-pr ─────────────────────────────────────────────────────────────
    vpr_p = sub.add_parser(
        "verify-pr",
        help="Verify provenance for all commits in a PR range.",
        description=(
            "Walk base..head and verify each commit's sao attestation:\n"
            "hash chain, ledger inclusion/consistency, session diff hash,\n"
            "signature (when present), and flight-plan scope."
        ),
    )
    vpr_p.add_argument("--base", required=True, help="Base ref (e.g. origin/main).")
    vpr_p.add_argument("--head", required=True, help="Head ref (e.g. HEAD).")
    vpr_p.add_argument(
        "--require-attestation",
        action="store_true",
        help="Fail (instead of warn) on commits without an attestation note.",
    )
    vpr_p.add_argument(
        "--strict-scope",
        action="store_true",
        help="Fail (instead of warn) when a commit changes files outside "
             "the mission's declared flight-plan scope.",
    )
    vpr_p.add_argument(
        "--markdown",
        metavar="PATH",
        help="Also write a Markdown report suitable for a GitHub check summary.",
    )
    vpr_p.add_argument(
        "--min-tier",
        choices=list(envelope_mod.TIER_ORDER),
        default=envelope_mod.TIER_SELF_RECORDED,
        help="Minimum assurance tier every commit must reach "
             "(default: self-recorded — current behaviour). ci-verified "
             "requires a valid CI-issued DSSE attestation per commit; "
             "independently-witnessed additionally requires the commit's "
             "ledger leaf to be covered by a witnessed checkpoint "
             "(--witness-keys plus --checkpoint or --anchors-remote).",
    )
    vpr_p.add_argument(
        "--ci-hmac-key-file",
        metavar="PATH",
        help="HMAC key file to verify CI-issued attestation signatures "
             "(ssh envelopes use $SAO_ALLOWED_SIGNERS instead).",
    )
    vpr_p.add_argument(
        "--ci-attestations-dir",
        metavar="DIR",
        help="Directory of CI-issued DSSE envelopes to search when a "
             "commit has no refs/notes/sao-ci discovery note.",
    )
    vpr_p.add_argument(
        "--witness-keys",
        metavar="FILE",
        help="Pinned witness keys file (one witness per line) for "
             "verifying checkpoint cosignatures.",
    )
    vpr_p.add_argument(
        "--require-witnesses",
        type=int,
        default=1,
        metavar="N",
        help="Cosignatures required from the pinned witness set for the "
             "independently-witnessed tier (default: 1).",
    )
    vpr_p.add_argument(
        "--checkpoint",
        metavar="PATH",
        help="Witnessed checkpoint file covering the commits' ledger "
             "leaves.",
    )
    vpr_p.add_argument(
        "--anchors-remote",
        metavar="URL",
        help="External anchor remote: use its newest anchored checkpoint "
             "instead of --checkpoint.",
    )
    vpr_p.add_argument(
        "--anchors-ref",
        metavar="REF",
        help="Anchor ref on --anchors-remote (default: "
             "refs/sao/anchors/<origin-slug>).",
    )
    vpr_p.set_defaults(func=cmd_verify_pr)

    # ── ci-issue ──────────────────────────────────────────────────────────────
    ci_issue_p = sub.add_parser(
        "ci-issue",
        help="Issue a CI attestation after independently verifying evidence.",
        description=(
            "Verify a mission's evidence bundle (seal, ledger, note payload\n"
            "hash), independently recompute the commit's git objects, apply\n"
            "policy (flight-plan scope, recorded checks), and emit a DSSE-\n"
            "wrapped in-toto Statement. Tier is ci-verified only when run\n"
            "under CI (GITHUB_ACTIONS=true) with a real signer; a local run\n"
            "stays self-recorded/locally-signed. Attaches a discovery note\n"
            "under refs/notes/sao-ci."
        ),
    )
    ci_issue_p.add_argument("--commit", required=True, help="Result commit OID to attest.")
    ci_issue_p.add_argument(
        "--session",
        metavar="MISSION_ID",
        help="Mission id of the evidence bundle (default: discovered via "
             "the commit's refs/notes/sao note).",
    )
    ci_issue_p.add_argument(
        "--signer",
        choices=["none", "ssh", "hmac"],
        default="none",
        help="Envelope signer. 'none' emits an unsigned envelope and can "
             "never claim ci-verified.",
    )
    ci_issue_p.add_argument(
        "--key-file",
        metavar="PATH",
        help="Signing key file (hmac: default $SAO_CI_HMAC_KEY_FILE; "
             "ssh: default $SAO_SIGNING_KEY_FILE).",
    )
    ci_issue_p.add_argument(
        "--out",
        metavar="PATH",
        help="Envelope output path (default: "
             "blackbox/ci-attestations/<commit>.dsse.json).",
    )
    ci_issue_p.add_argument(
        "--allow-failed-checks",
        action="store_true",
        help="Issue even when the recorded mission exit code is non-zero.",
    )
    ci_issue_p.add_argument(
        "--strict-scope",
        action="store_true",
        help="Refuse issuance when the commit changes files outside the "
             "flight-plan scope (default: advisory warning).",
    )
    ci_issue_p.set_defaults(func=cmd_ci_issue)

    # ── ci-verify ─────────────────────────────────────────────────────────────
    ci_verify_p = sub.add_parser(
        "ci-verify",
        help="Verify a CI-issued DSSE attestation against a commit.",
        description=(
            "Verify the DSSE signature, check the in-toto statement subject\n"
            "against the actual commit and tree, and re-run the git reality\n"
            "checks against the predicate. Prints the assurance tier."
        ),
    )
    ci_verify_p.add_argument("--commit", required=True, help="Commit the statement must match.")
    ci_verify_p.add_argument(
        "--attestation", required=True, metavar="PATH",
        help="Path to the DSSE envelope JSON.",
    )
    ci_verify_p.add_argument(
        "--hmac-key-file",
        metavar="PATH",
        help="HMAC key file for hmac-signed envelopes.",
    )
    ci_verify_p.add_argument(
        "--allowed-signers",
        metavar="PATH",
        help="ssh allowed-signers file for ssh-signed envelopes.",
    )
    ci_verify_p.add_argument(
        "--signer-identity",
        default="sao",
        help="Identity to match in the allowed-signers file (default: sao).",
    )
    ci_verify_p.set_defaults(func=cmd_ci_verify)

    # ── checkpoint ────────────────────────────────────────────────────────────
    ckpt_p = sub.add_parser(
        "checkpoint",
        help="Signed ledger checkpoints for independent witnessing.",
        description=(
            "Emit and verify signed checkpoints of the transparency ledger\n"
            "(origin + tree size + Merkle root). Witnesses cosign\n"
            "checkpoints (sao witness); clients requiring N pinned\n"
            "cosignatures make ledger equivocation need colluding witnesses."
        ),
    )
    ckpt_sub = ckpt_p.add_subparsers(dest="checkpoint_command", required=True)

    ckpt_emit_p = ckpt_sub.add_parser(
        "emit",
        help="Emit a signed checkpoint of the current ledger.",
        description=(
            "Write a sao-checkpoint/1 document for the current ledger,\n"
            "signed by the repo operator. Verifiers and witnesses need\n"
            "ledger access (this repo or a clone); for witnesses without a\n"
            "clone, embed a consistency proof with --bundle-proof-from\n"
            "set to the tree size the witness last saw."
        ),
    )
    ckpt_emit_p.add_argument(
        "--out",
        metavar="PATH",
        help="Checkpoint output path (default: blackbox/checkpoint.json).",
    )
    ckpt_emit_p.add_argument(
        "--signer",
        choices=["none", "ssh", "hmac"],
        default="none",
        help="Operator signer. 'none' emits an UNSIGNED checkpoint "
             "(loudly marked as such).",
    )
    ckpt_emit_p.add_argument(
        "--key-file",
        metavar="PATH",
        help="Signing key file (hmac: default $SAO_CI_HMAC_KEY_FILE; "
             "ssh: default $SAO_SIGNING_KEY_FILE).",
    )
    ckpt_emit_p.add_argument(
        "--origin",
        help="Stable ledger identity string (default: origin remote URL, "
             "falling back to the repo directory name).",
    )
    ckpt_emit_p.add_argument(
        "--bundle-proof-from",
        type=int,
        metavar="SIZE",
        help="Embed a consistency proof from this older tree size, for "
             "witnesses without their own ledger clone.",
    )
    ckpt_emit_p.set_defaults(func=cmd_checkpoint_emit)

    ckpt_verify_p = ckpt_sub.add_parser(
        "verify",
        help="Verify a checkpoint (signature, ledger root, cosignatures).",
        description=(
            "Verify the operator signature, that the checkpoint root\n"
            "matches this repo's ledger at that size (and is append-only\n"
            "consistent with the current ledger), and that at least\n"
            "--require-witnesses cosignatures verify against the pinned\n"
            "--witness-keys file (one witness per line:\n"
            "'<name> ssh-ed25519 <key>' allowed-signers style, or\n"
            "'<name> hmac-sha256 <key>' for a shared-secret witness)."
        ),
    )
    ckpt_verify_p.add_argument(
        "--checkpoint", required=True, metavar="PATH",
        help="Checkpoint file to verify.",
    )
    ckpt_verify_p.add_argument(
        "--require-witnesses",
        type=int,
        default=0,
        metavar="N",
        help="Minimum valid cosignatures from the pinned witness set "
             "(default: 0 — no quorum enforced).",
    )
    ckpt_verify_p.add_argument(
        "--witness-keys",
        metavar="FILE",
        help="Pinned witness keys file (one witness per line).",
    )
    ckpt_verify_p.add_argument(
        "--hmac-key-file",
        metavar="PATH",
        help="HMAC key file to verify an hmac operator signature.",
    )
    ckpt_verify_p.add_argument(
        "--allowed-signers",
        metavar="PATH",
        help="ssh allowed-signers file to verify an ssh operator signature.",
    )
    ckpt_verify_p.add_argument(
        "--signer-identity",
        default="sao",
        help="Identity to match in the operator allowed-signers file "
             "(default: sao).",
    )
    ckpt_verify_p.set_defaults(func=cmd_checkpoint_verify)

    # ── witness ───────────────────────────────────────────────────────────────
    witness_p = sub.add_parser(
        "witness",
        help="Independent checkpoint witness (cosign / state).",
        description=(
            "A stateful, independent cosigner for ledger checkpoints.\n"
            "RUN IT OUTSIDE THE ATTESTED REPO — a different machine, repo,\n"
            "and CI, with its own key and state the repo operator cannot\n"
            "write to (see templates/sao-witness.yml). The witness\n"
            "remembers the last checkpoint per origin, verifies\n"
            "append-only growth, and REFUSES forks, rollbacks, and\n"
            "same-size root swaps without updating its state."
        ),
    )
    witness_sub = witness_p.add_subparsers(dest="witness_command", required=True)

    witness_cosign_p = witness_sub.add_parser(
        "cosign",
        help="Verify a checkpoint against remembered state and cosign it.",
        description=(
            "First encounter with an origin is trust-on-first-use (logged\n"
            "loudly). Afterwards the checkpoint must extend the remembered\n"
            "one: same origin, tree size not shrinking, and a verifying\n"
            "consistency proof — from the witness's own ledger clone\n"
            "(--ledger-repo) or the checkpoint's bundled proof. Any\n"
            "failure is a REFUSAL (exit 1, state untouched)."
        ),
    )
    witness_cosign_p.add_argument(
        "--checkpoint", required=True, metavar="PATH",
        help="Checkpoint file to verify and cosign (updated in place).",
    )
    witness_cosign_p.add_argument(
        "--state-dir", required=True, metavar="DIR",
        help="Directory holding this witness's per-origin state files.",
    )
    witness_cosign_p.add_argument(
        "--name", required=True,
        help="Witness name — must match the name pinned in verifiers' "
             "witness-keys files.",
    )
    witness_cosign_p.add_argument(
        "--signer",
        choices=["ssh", "hmac"],
        required=True,
        help="Cosignature signer ('none' is not allowed for witnesses).",
    )
    witness_cosign_p.add_argument(
        "--key-file",
        metavar="PATH",
        help="Witness signing key file (hmac: default $SAO_CI_HMAC_KEY_FILE; "
             "ssh: default $SAO_SIGNING_KEY_FILE). Keep it OUT of the "
             "attested repo's trust domain.",
    )
    witness_cosign_p.add_argument(
        "--ledger-repo",
        metavar="PATH",
        help="Path to the witness's own clone of the attested repo, used "
             "to verify consistency proofs (otherwise the checkpoint must "
             "bundle a proof from the remembered size).",
    )
    witness_cosign_p.add_argument(
        "--operator-hmac-key-file",
        metavar="PATH",
        help="Pinned operator HMAC key to verify the checkpoint's "
             "operator signature before cosigning.",
    )
    witness_cosign_p.add_argument(
        "--operator-allowed-signers",
        metavar="PATH",
        help="Pinned operator allowed-signers file to verify an ssh "
             "operator signature before cosigning.",
    )
    witness_cosign_p.set_defaults(func=cmd_witness_cosign)

    witness_state_p = witness_sub.add_parser(
        "state",
        help="Print the witness's remembered origins (sizes + roots).",
        description="List every origin in --state-dir with its last "
                    "cosigned tree size and root.",
    )
    witness_state_p.add_argument(
        "--state-dir", required=True, metavar="DIR",
        help="Directory holding this witness's per-origin state files.",
    )
    witness_state_p.set_defaults(func=cmd_witness_state)

    # ── anchor ────────────────────────────────────────────────────────────────
    anchor_p = sub.add_parser(
        "anchor",
        help="Anchor checkpoints on an external git remote.",
        description=(
            "Publish checkpoints as an append-only commit chain on a ref\n"
            "in an EXTERNAL repository (refs/sao/anchors/<origin-slug>),\n"
            "so ledger history cannot be rewritten without the anchor\n"
            "chain telling on it. Use a remote the attested repo's\n"
            "operator and agents cannot force-push."
        ),
    )
    anchor_sub = anchor_p.add_subparsers(dest="anchor_command", required=True)

    anchor_push_p = anchor_sub.add_parser(
        "push",
        help="Append the current (optionally witnessed) checkpoint.",
        description=(
            "Append a checkpoint as a new anchor commit whose parent is\n"
            "the previous anchor. Refuses to anchor a checkpoint that does\n"
            "not strictly grow past the anchored tip, and pushes plain\n"
            "fast-forward — a rewritten anchor ref makes the push fail."
        ),
    )
    anchor_push_p.add_argument(
        "--remote", required=True,
        help="External anchor repository (URL or path).",
    )
    anchor_push_p.add_argument(
        "--ref",
        metavar="REF",
        help="Anchor ref (default: refs/sao/anchors/<origin-slug>).",
    )
    anchor_push_p.add_argument(
        "--checkpoint",
        metavar="PATH",
        help="Checkpoint file to anchor (default: build an unsigned "
             "checkpoint of the current ledger).",
    )
    anchor_push_p.add_argument(
        "--origin",
        help="Ledger identity when building the default checkpoint "
             "(default: origin remote URL / repo directory name).",
    )
    anchor_push_p.set_defaults(func=cmd_anchor_push)

    anchor_verify_p = anchor_sub.add_parser(
        "verify",
        help="Verify the anchor chain against the local ledger.",
        description=(
            "Fetch the anchor chain and check linearity (strictly\n"
            "increasing tree sizes, one origin), consistency of every\n"
            "anchored root with the local ledger, and (with\n"
            "--max-age-days) freshness of the newest anchor. Checkpoint\n"
            "timestamps are operator claims unless witnessed."
        ),
    )
    anchor_verify_p.add_argument(
        "--remote", required=True,
        help="External anchor repository (URL or path).",
    )
    anchor_verify_p.add_argument(
        "--ref",
        metavar="REF",
        help="Anchor ref (default: refs/sao/anchors/<origin-slug>).",
    )
    anchor_verify_p.add_argument(
        "--origin",
        help="Ledger identity used to derive the default ref.",
    )
    anchor_verify_p.add_argument(
        "--max-age-days",
        type=float,
        metavar="N",
        help="Fail when the newest anchor's checkpoint timestamp is older "
             "than N days.",
    )
    anchor_verify_p.set_defaults(func=cmd_anchor_verify)

    # ── blame ─────────────────────────────────────────────────────────────────
    blame_p = sub.add_parser(
        "blame",
        help="Line-level attribution for a file (derived, best-effort).",
        description=(
            "Annotate each line of a file with the agent mission whose commit\n"
            "last touched it, resolved through git blame + refs/notes/sao\n"
            "attestations. Attribution is derived and best-effort: moves,\n"
            "reformats, and conflict resolution distort it; commit-level\n"
            "provenance is canonical."
        ),
    )
    blame_p.add_argument("file", help="Repo-relative path of the file to annotate.")
    blame_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the annotated listing.",
    )
    blame_p.set_defaults(func=cmd_blame)

    # ── mcp ───────────────────────────────────────────────────────────────────
    mcp_p = sub.add_parser(
        "mcp",
        help="Run the provenance MCP server over stdio.",
        description=(
            "Serve newline-delimited JSON-RPC 2.0 (Model Context Protocol)\n"
            "on stdin/stdout so live agents can file flight plans and query\n"
            "mission provenance.  Stdlib only; runs until stdin closes."
        ),
    )
    mcp_p.set_defaults(func=cmd_mcp)

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
