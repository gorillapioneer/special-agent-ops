"""
verify_pr.py — the enforcement gate: verify provenance across a PR range.

``sao verify-pr --base <ref> --head <ref>`` walks every commit in
``base..head`` and checks, per commit:

  * does it carry a sao attestation note (refs/notes/sao)?
  * hash chain      — parent_attestation_sha256 links to the previous
                      attestation (located via the ledger's previous leaf's
                      session copy) where discoverable,
  * ledger          — the recorded leaf verifies by inclusion proof against
                      the CURRENT ledger root, and the recorded (size, root)
                      is append-only-consistent with the current log,
  * diff            — diff_sha256 matches the recorded session's
                      git_diff.patch when the session folder still exists,
  * session copy    — the note matches the session's provenance.json,
  * signature       — provenance.json.sig verifies when present,
  * scope           — files changed in the commit all match the mission's
                      flight-plan globs (WARN on drift, FAIL with
                      --strict-scope).

Unattested commits are WARN by default, FAIL with --require-attestation.
Exit code 0 when the gate passes, 1 when it fails.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import attest, flightplan, ledger as ledger_mod

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def list_range_commits(repo_path: Path, base: str, head: str) -> list:
    """Commits in base..head, oldest first."""
    proc = _git(["rev-list", "--reverse", f"{base}..{head}"], cwd=repo_path)
    if proc.returncode != 0:
        raise ValueError(
            f"git rev-list failed for {base}..{head}: {proc.stderr.strip()}"
        )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def commit_changed_files(repo_path: Path, commit: str) -> list:
    proc = _git(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", commit],
        cwd=repo_path,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# ── Per-commit checks ─────────────────────────────────────────────────────────

def _check(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


def _session_dir_for(repo_path: Path, mission_id: str):
    d = Path(repo_path) / "blackbox" / "sessions" / mission_id
    return d if d.is_dir() else None


def _check_hash_chain(repo_path, statement, ledger) -> dict:
    leaf_index = statement.get("ledger", {}).get("leaf_index")
    parent_sha = statement.get("parent_attestation_sha256")

    if leaf_index == 0:
        if parent_sha is None:
            return _check("hash-chain", OK, "first ledger entry, no parent")
        return _check("hash-chain", FAIL, "leaf 0 must not declare a parent")

    if leaf_index is None:
        return _check("hash-chain", FAIL, "attestation has no ledger position")

    entries = ledger.entries()
    if leaf_index - 1 >= len(entries):
        return _check("hash-chain", FAIL, "previous ledger entry missing")
    parent_mission = entries[leaf_index - 1].get("mission_id")
    parent_dir = _session_dir_for(repo_path, parent_mission)
    if parent_dir is None:
        return _check(
            "hash-chain", WARN,
            f"parent session {parent_mission} not on disk; link unverifiable",
        )
    _, parent_text = attest.load_attestation(parent_dir)
    if parent_text is None:
        return _check(
            "hash-chain", WARN,
            f"parent session {parent_mission} has no provenance.json",
        )
    import hashlib

    actual = hashlib.sha256(parent_text.encode("utf-8")).hexdigest()
    if actual == parent_sha:
        return _check("hash-chain", OK, f"links to {parent_mission}")
    return _check(
        "hash-chain", FAIL,
        f"parent_attestation_sha256 does not match {parent_mission}",
    )


def _check_ledger(statement, ledger) -> list:
    checks = []
    pos = statement.get("ledger") or {}
    leaf_index = pos.get("leaf_index")
    leaf_hash = pos.get("leaf_hash")
    if leaf_index is None or not leaf_hash:
        return [_check("ledger-inclusion", FAIL, "no ledger position recorded")]

    current = ledger.root()
    if leaf_index >= current["tree_size"]:
        return [_check(
            "ledger-inclusion", FAIL,
            f"leaf index {leaf_index} beyond current log size {current['tree_size']}",
        )]

    entry = ledger.entries()[leaf_index]
    if entry.get("leaf_hash") != leaf_hash:
        checks.append(_check(
            "ledger-inclusion", FAIL,
            "ledger entry leaf hash differs from attestation",
        ))
        return checks

    proof = ledger.inclusion_proof(leaf_index)
    inc_ok = ledger_mod.verify_inclusion(
        leaf_hash, leaf_index, proof, current["root_hash"], current["tree_size"]
    )
    checks.append(_check(
        "ledger-inclusion",
        OK if inc_ok else FAIL,
        f"leaf {leaf_index} vs current root (size {current['tree_size']})",
    ))

    rec_size = pos.get("tree_size")
    rec_root = pos.get("root")
    if rec_size and rec_root:
        try:
            cons = ledger.consistency_proof(rec_size)
            cons_ok = ledger_mod.verify_consistency(
                rec_size, current["tree_size"], rec_root,
                current["root_hash"], cons,
            )
        except ValueError:
            cons_ok = False
        checks.append(_check(
            "ledger-consistency",
            OK if cons_ok else FAIL,
            f"recorded size {rec_size} -> current size {current['tree_size']}",
        ))
    return checks


def _check_diff(repo_path, statement) -> dict:
    mission_id = statement.get("mission_id", "")
    session_dir = _session_dir_for(repo_path, mission_id)
    if session_dir is None:
        return _check("diff", SKIP, "session folder not on disk")
    diff_path = session_dir / "git_diff.patch"
    if not diff_path.exists():
        return _check("diff", WARN, "session has no git_diff.patch")
    from sao.blackbox.seal import sha256_file

    actual = sha256_file(diff_path)
    if actual == statement.get("diff_sha256"):
        return _check("diff", OK, "diff_sha256 matches recorded session diff")
    return _check("diff", FAIL, "diff_sha256 does not match session diff")


def _check_session_copy(repo_path, statement, note_text) -> dict:
    mission_id = statement.get("mission_id", "")
    session_dir = _session_dir_for(repo_path, mission_id)
    if session_dir is None:
        return _check("session-copy", SKIP, "session folder not on disk")
    _, session_text = attest.load_attestation(session_dir)
    if session_text is None:
        return _check("session-copy", WARN, "session has no provenance.json")
    if session_text == note_text:
        return _check("session-copy", OK, "git note matches provenance.json")
    return _check("session-copy", FAIL, "git note differs from provenance.json")


def _check_signature(repo_path, statement) -> dict:
    mission_id = statement.get("mission_id", "")
    session_dir = _session_dir_for(repo_path, mission_id)
    if session_dir is None:
        return _check("signature", SKIP, "session folder not on disk")
    result = attest.verify_attestation_signature(session_dir)
    if result is None:
        return _check("signature", SKIP, "no signature present")
    if result:
        return _check("signature", OK, "ssh signature verifies")
    return _check("signature", FAIL, "ssh signature does not verify")


def _check_scope(repo_path, statement, commit, strict_scope) -> dict:
    mission_id = statement.get("mission_id", "")
    session_dir = _session_dir_for(repo_path, mission_id)
    if session_dir is None:
        return _check("scope", SKIP, "session folder not on disk")
    plan = flightplan.load_session_plan(session_dir)
    if plan is None:
        return _check("scope", SKIP, "no flight plan filed for this mission")
    changed = commit_changed_files(repo_path, commit)
    result = flightplan.check_scope(changed, plan.get("scope", []))
    if result["ok"]:
        return _check(
            "scope", OK,
            f"{len(result['in_scope'])} file(s) within declared scope",
        )
    drift = ", ".join(result["out_of_scope"][:5])
    level = FAIL if strict_scope else WARN
    return _check("scope", level, f"scope drift: {drift}")


# ── Range verification ────────────────────────────────────────────────────────

def verify_pr(
    repo_path: Path,
    base: str,
    head: str,
    require_attestation: bool = False,
    strict_scope: bool = False,
) -> dict:
    """Verify provenance for every commit in base..head.

    Returns {"ok": bool, "base": ..., "head": ..., "commits": [...],
             "counts": {"attested": n, "unattested": n, "failed": n}}.
    """
    repo_path = Path(repo_path)
    commits = list_range_commits(repo_path, base, head)
    ledger = ledger_mod.Ledger(repo_path)

    results = []
    attested = unattested = failed = 0

    for commit in commits:
        note = attest.read_git_note(repo_path, commit)
        entry = {
            "commit": commit,
            "short": commit[:10],
            "attested": note is not None,
            "mission_id": None,
            "checks": [],
        }
        if note is None:
            unattested += 1
            level = FAIL if require_attestation else WARN
            entry["checks"].append(_check(
                "attestation", level, "commit carries no sao attestation note"
            ))
        else:
            attested += 1
            entry["mission_id"] = note.get("mission_id")
            note_text = attest.canonical_json(note)
            entry["checks"].append(_check(
                "attestation", OK, f"mission {note.get('mission_id')}"
            ))
            entry["checks"].append(_check_hash_chain(repo_path, note, ledger))
            entry["checks"].extend(_check_ledger(note, ledger))
            entry["checks"].append(_check_diff(repo_path, note))
            entry["checks"].append(_check_session_copy(repo_path, note, note_text))
            entry["checks"].append(_check_signature(repo_path, note))
            entry["checks"].append(
                _check_scope(repo_path, note, commit, strict_scope)
            )

        if any(c["level"] == FAIL for c in entry["checks"]):
            failed += 1
        results.append(entry)

    ok = failed == 0
    return {
        "ok": ok,
        "base": base,
        "head": head,
        "commit_count": len(commits),
        "commits": results,
        "counts": {
            "attested": attested,
            "unattested": unattested,
            "failed": failed,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_text(report: dict) -> str:
    bar = "=" * 64
    lines = [
        "",
        bar,
        "  SPECIAL AGENT OPS — VERIFY PR",
        bar,
        f"  Range:      {report['base']}..{report['head']}",
        f"  Commits:    {report['commit_count']}",
        f"  Attested:   {report['counts']['attested']}",
        f"  Unattested: {report['counts']['unattested']}",
        f"  Failed:     {report['counts']['failed']}",
        bar,
    ]
    for c in report["commits"]:
        title = c["mission_id"] or "(unattested)"
        lines.append(f"  {c['short']}  {title}")
        for check in c["checks"]:
            lines.append(f"      [{check['level']:<4}] {check['name']}: {check['detail']}")
    lines.append(bar)
    lines.append(f"  Result: {'PASS' if report['ok'] else 'FAIL'}")
    lines.append(bar)
    lines.append("")
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    status = "PASS" if report["ok"] else "FAIL"
    lines = [
        f"# sao verify-pr — {status}",
        "",
        f"- **Range:** `{report['base']}..{report['head']}`",
        f"- **Commits:** {report['commit_count']} "
        f"({report['counts']['attested']} attested, "
        f"{report['counts']['unattested']} unattested, "
        f"{report['counts']['failed']} failed)",
        f"- **Generated:** {report['generated_at']}",
        "",
        "| Commit | Mission | Check | Level | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in report["commits"]:
        mission = c["mission_id"] or "—"
        for check in c["checks"]:
            lines.append(
                f"| `{c['short']}` | {mission} | {check['name']} "
                f"| {check['level']} | {check['detail']} |"
            )
    lines.append("")
    return "\n".join(lines)
