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
  * git objects     — the recorded result tree OID matches the commit's
                      actual tree, and each recorded changed-path blob OID
                      and mode matches ``git ls-tree`` reality (v2
                      attestations; v1 statements predate this and SKIP),
  * session copy    — the note matches the session's provenance.json and,
                      for v2 notes, the note's payload_sha256 matches the
                      SHA256 of the session copy.  When the session folder
                      is gone this is a WARN: a git note alone is
                      unverifiable discovery metadata (notes can be
                      force-replaced without changing the commit SHA),
  * signature       — provenance.json.sig verifies when present,
  * scope           — files changed in the commit all match the mission's
                      flight-plan globs (WARN on drift, FAIL with
                      --strict-scope),
  * tier            — the highest verifiable assurance tier for the commit
                      (self-recorded / locally-signed / ci-verified /
                      independently-witnessed — see docs/THREAT_MODEL.md).
                      A commit reaches ci-verified only when a CI-issued
                      DSSE attestation (discovered via refs/notes/sao-ci
                      or --ci-attestations-dir) passes `sao ci-verify`
                      checks; it reaches independently-witnessed when it
                      is ci-verified AND its ledger leaf is covered by a
                      checkpoint carrying >= --require-witnesses valid
                      cosignatures from the pinned --witness-keys set
                      (checkpoint from --checkpoint or the newest anchor
                      on --anchors-remote). Commits below --min-tier FAIL.

Both "sao-attestation/1" and "sao-attestation/2" statements are accepted;
unknown versions FAIL.

Unattested commits are WARN by default, FAIL with --require-attestation
(and always FAIL when --min-tier is above self-recorded).
Exit code 0 when the gate passes, 1 when it fails.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import (
    anchor as anchor_mod,
    attest,
    checkpoint as checkpoint_mod,
    envelope as envelope_mod,
    flightplan,
    ledger as ledger_mod,
)

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


def _check_git_objects(repo_path, statement, commit) -> dict:
    """Verify recorded git object IDs against the actual commit.

    v2 attestations record the result commit's tree OID and, per changed
    path, the blob OID and file mode.  Those must match what the commit
    actually contains — a diff hash can only be checked against the
    recorded diff text, but object IDs are checked against git itself.
    """
    gobj = statement.get("git_objects")
    if gobj is None:
        if statement.get("version") == "sao-attestation/1":
            return _check(
                "git-objects", SKIP,
                "attestation v1 predates git object binding",
            )
        return _check(
            "git-objects", SKIP,
            "no git_objects recorded (mission ended without a new commit)",
        )

    recorded_commit = gobj.get("commit")
    if recorded_commit != commit:
        return _check(
            "git-objects", FAIL,
            f"recorded result commit {str(recorded_commit)[:10]} is not "
            f"the noted commit {commit[:10]}",
        )

    proc = _git(["rev-parse", "--verify", f"{commit}^{{tree}}"], cwd=repo_path)
    actual_tree = proc.stdout.strip()
    if proc.returncode != 0 or not actual_tree:
        return _check("git-objects", FAIL, "could not resolve commit tree")
    if actual_tree != gobj.get("tree"):
        return _check(
            "git-objects", FAIL,
            f"recorded tree OID does not match actual tree {actual_tree[:10]}",
        )

    # One recursive ls-tree, then compare each recorded changed path.
    proc = _git(["ls-tree", "-r", commit], cwd=repo_path)
    if proc.returncode != 0:
        return _check("git-objects", FAIL, "git ls-tree failed")
    tree_map = {}
    for line in proc.stdout.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and path:
            tree_map[path] = (parts[0], parts[2])   # (mode, oid)

    mismatches = []
    for entry in gobj.get("changed", []):
        path = entry.get("path")
        if entry.get("status") == "D":
            if path in tree_map:
                mismatches.append(f"{path} (recorded deleted, present)")
            continue
        actual = tree_map.get(path)
        if actual is None:
            mismatches.append(f"{path} (missing from tree)")
        elif actual != (entry.get("mode"), entry.get("blob")):
            mismatches.append(f"{path} (blob/mode mismatch)")
    if mismatches:
        return _check(
            "git-objects", FAIL,
            "recorded blob OIDs do not match tree: " + ", ".join(mismatches[:5]),
        )
    return _check(
        "git-objects", OK,
        f"tree {actual_tree[:10]} and {len(gobj.get('changed', []))} "
        f"changed path(s) match git objects",
    )


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


def _check_session_copy(repo_path, statement, note_text, note_payload_sha) -> dict:
    mission_id = statement.get("mission_id", "")
    session_dir = _session_dir_for(repo_path, mission_id)
    if session_dir is None:
        return _check(
            "session-copy", WARN,
            "session folder not on disk — a git note alone is unverifiable "
            "discovery metadata (notes can be replaced without changing the "
            "commit SHA)",
        )
    _, session_text = attest.load_attestation(session_dir)
    if session_text is None:
        return _check("session-copy", WARN, "session has no provenance.json")
    if session_text != note_text:
        return _check(
            "session-copy", FAIL, "git note differs from provenance.json"
        )
    if note_payload_sha is not None:
        import hashlib

        session_sha = hashlib.sha256(session_text.encode("utf-8")).hexdigest()
        if note_payload_sha != session_sha:
            return _check(
                "session-copy", FAIL,
                "note payload_sha256 does not match session provenance.json",
            )
        return _check(
            "session-copy", OK,
            "git note matches provenance.json (payload_sha256 cross-checked)",
        )
    return _check("session-copy", OK, "git note matches provenance.json")


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


# ── Assurance tier determination ─────────────────────────────────────────────

def _determine_ci_tier(
    repo_path, commit, ci_hmac_key_file, ci_attestations_dir
):
    """Look for a CI-issued attestation and verify it.

    Returns (check_dict, ci_verified: bool). The check is SKIP when no CI
    attestation is discoverable, OK when one verifies to ci-verified, and
    FAIL when one is found but does not verify (a bad CI attestation is
    loud, never silently ignored).
    """
    from . import ci_issue  # imported lazily: ci_issue imports this module

    dsse, source = ci_issue.find_ci_attestation(
        repo_path, commit, ci_attestations_dir
    )
    if dsse is None:
        return _check(
            "ci-attestation", SKIP,
            "no CI-issued attestation found (refs/notes/sao-ci or "
            "--ci-attestations-dir)",
        ), False
    report = ci_issue.ci_verify(
        repo_path,
        commit,
        dsse=dsse,
        hmac_key_file=ci_hmac_key_file,
        allowed_signers=os.environ.get("SAO_ALLOWED_SIGNERS"),
        signer_identity=os.environ.get("SAO_SIGNER_IDENTITY", "sao"),
    )
    if report["ok"] and report["tier"] == envelope_mod.TIER_CI_VERIFIED:
        return _check(
            "ci-attestation", OK,
            f"CI-issued attestation verifies (ci-verified): {source}",
        ), True
    if report["ok"]:
        return _check(
            "ci-attestation", WARN,
            f"CI attestation verifies but claims tier {report['tier']} "
            f"(issued outside CI?): {source}",
        ), False
    failures = "; ".join(
        f"{c['name']}: {c['detail']}" for c in report["checks"]
        if c["level"] == FAIL
    )
    return _check(
        "ci-attestation", FAIL,
        f"CI attestation found but does not verify ({source}): {failures}",
    ), False


def _load_witness_context(
    repo_path,
    witness_keys,
    require_witnesses,
    checkpoint_path,
    anchors_remote,
    anchors_ref,
):
    """Load and verify the witnessed checkpoint ONCE per verify-pr run.

    Returns None when no checkpoint source was requested, else
    {"cp": dict|None, "report": dict|None, "source": str, "error": str}.
    Per-commit leaf coverage is checked separately in _check_witnessed.
    """
    if checkpoint_path is None and anchors_remote is None:
        return None
    ctx = {"cp": None, "report": None, "source": None, "error": None}
    if checkpoint_path is not None:
        try:
            ctx["cp"] = checkpoint_mod.load_checkpoint(checkpoint_path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            ctx["error"] = f"cannot load checkpoint {checkpoint_path}: {e}"
            return ctx
        ctx["source"] = str(checkpoint_path)
    else:
        cp, source = anchor_mod.latest_anchored_checkpoint(
            repo_path, anchors_remote, ref=anchors_ref
        )
        if cp is None:
            ctx["error"] = f"no anchored checkpoint: {source}"
            return ctx
        ctx["cp"], ctx["source"] = cp, source
    if not witness_keys:
        ctx["error"] = (
            "a witnessed checkpoint requires a pinned --witness-keys file"
        )
        return ctx
    ctx["report"] = checkpoint_mod.verify_checkpoint(
        repo_path,
        ctx["cp"],
        require_witnesses=require_witnesses,
        witness_keys_path=witness_keys,
    )
    return ctx


def _check_witnessed(statement, witness_ctx):
    """Per-commit witnessed-coverage check.

    Returns (check_dict, witnessed: bool). SKIP when no checkpoint source
    was given; FAIL loudly when one was given but does not verify or does
    not cover the commit's ledger leaf.
    """
    if witness_ctx is None:
        return _check(
            "witnessed-checkpoint", SKIP,
            "no witnessed checkpoint provided (--checkpoint / "
            "--anchors-remote)",
        ), False
    if witness_ctx["error"]:
        return _check(
            "witnessed-checkpoint", FAIL, witness_ctx["error"]
        ), False
    cp_report = witness_ctx["report"]
    if not cp_report["ok"]:
        failures = "; ".join(
            f"{c['name']}: {c['detail']}" for c in cp_report["checks"]
            if c["level"] == FAIL
        )
        return _check(
            "witnessed-checkpoint", FAIL,
            f"checkpoint does not verify ({witness_ctx['source']}): "
            f"{failures}",
        ), False
    leaf_index = (statement.get("ledger") or {}).get("leaf_index")
    tree_size = witness_ctx["cp"].get("tree_size")
    if leaf_index is None:
        return _check(
            "witnessed-checkpoint", FAIL,
            "attestation records no ledger leaf to cover",
        ), False
    if leaf_index >= tree_size:
        return _check(
            "witnessed-checkpoint", FAIL,
            f"ledger leaf {leaf_index} is not covered by the witnessed "
            f"checkpoint (size {tree_size}) — emit and witness a fresh "
            "checkpoint",
        ), False
    witnesses = ", ".join(cp_report["valid_witnesses"]) or "none"
    return _check(
        "witnessed-checkpoint", OK,
        f"leaf {leaf_index} covered by checkpoint size {tree_size} with "
        f"{len(cp_report['valid_witnesses'])} pinned cosignature(s) "
        f"({witnesses}) via {witness_ctx['source']}",
    ), True


def _check_tier(tier, min_tier) -> dict:
    have, need = envelope_mod.tier_rank(tier), envelope_mod.tier_rank(min_tier)
    if have >= need:
        return _check("tier", OK, f"assurance tier {tier} >= required {min_tier}")
    return _check(
        "tier", FAIL,
        f"assurance tier {tier or 'none'} is below required {min_tier}",
    )


# ── Range verification ────────────────────────────────────────────────────────

def verify_pr(
    repo_path: Path,
    base: str,
    head: str,
    require_attestation: bool = False,
    strict_scope: bool = False,
    min_tier: str = envelope_mod.TIER_SELF_RECORDED,
    ci_hmac_key_file=None,
    ci_attestations_dir=None,
    witness_keys=None,
    require_witnesses: int = 1,
    checkpoint_path=None,
    anchors_remote=None,
    anchors_ref=None,
) -> dict:
    """Verify provenance for every commit in base..head.

    Returns {"ok": bool, "base": ..., "head": ..., "commits": [...],
             "counts": {"attested": n, "unattested": n, "failed": n}}.
    Each commit entry carries its highest verifiable assurance tier;
    commits below *min_tier* FAIL. The independently-witnessed tier
    additionally needs a witnessed checkpoint source (*checkpoint_path*
    or *anchors_remote*) plus the pinned *witness_keys* file.
    """
    repo_path = Path(repo_path)
    if envelope_mod.tier_rank(min_tier) < 0:
        raise ValueError(
            f"unknown --min-tier {min_tier!r}; expected one of "
            f"{', '.join(envelope_mod.TIER_ORDER)}"
        )
    min_rank = envelope_mod.tier_rank(min_tier)
    commits = list_range_commits(repo_path, base, head)
    ledger = ledger_mod.Ledger(repo_path)
    witness_ctx = _load_witness_context(
        repo_path, witness_keys, require_witnesses,
        checkpoint_path, anchors_remote, anchors_ref,
    )

    results = []
    attested = unattested = failed = 0

    for commit in commits:
        note = attest.read_git_note(repo_path, commit)
        entry = {
            "commit": commit,
            "short": commit[:10],
            "attested": note is not None,
            "mission_id": None,
            "tier": None,
            "checks": [],
        }
        if note is None:
            unattested += 1
            level = FAIL if require_attestation else WARN
            entry["checks"].append(_check(
                "attestation", level, "commit carries no sao attestation note"
            ))
            if min_rank > 0:
                entry["checks"].append(_check_tier(None, min_tier))
        else:
            attested += 1
            # A v2 note is the statement plus payload_sha256; strip the
            # payload field before treating the note as a statement.
            statement, note_payload_sha = attest.note_statement_and_payload(note)
            entry["mission_id"] = statement.get("mission_id")
            note_text = attest.canonical_json(statement)
            version = statement.get("version")
            if version in attest.SUPPORTED_VERSIONS:
                entry["checks"].append(_check(
                    "attestation", OK,
                    f"mission {statement.get('mission_id')} ({version})",
                ))
            else:
                entry["checks"].append(_check(
                    "attestation", FAIL,
                    f"unsupported attestation version: {version!r}",
                ))
            entry["checks"].append(_check_hash_chain(repo_path, statement, ledger))
            entry["checks"].extend(_check_ledger(statement, ledger))
            entry["checks"].append(_check_diff(repo_path, statement))
            entry["checks"].append(
                _check_git_objects(repo_path, statement, commit)
            )
            entry["checks"].append(
                _check_session_copy(
                    repo_path, statement, note_text, note_payload_sha
                )
            )
            signature_check = _check_signature(repo_path, statement)
            entry["checks"].append(signature_check)
            entry["checks"].append(
                _check_scope(repo_path, statement, commit, strict_scope)
            )

            # Highest verifiable assurance tier for this commit.
            ci_check, ci_verified = _determine_ci_tier(
                repo_path, commit, ci_hmac_key_file, ci_attestations_dir
            )
            entry["checks"].append(ci_check)
            witness_check, witnessed = _check_witnessed(statement, witness_ctx)
            entry["checks"].append(witness_check)
            if ci_verified and witnessed:
                entry["tier"] = envelope_mod.TIER_INDEPENDENTLY_WITNESSED
            elif ci_verified:
                entry["tier"] = envelope_mod.TIER_CI_VERIFIED
            elif signature_check["level"] == OK:
                entry["tier"] = envelope_mod.TIER_LOCALLY_SIGNED
            else:
                entry["tier"] = envelope_mod.TIER_SELF_RECORDED
            entry["checks"].append(_check_tier(entry["tier"], min_tier))

        if any(c["level"] == FAIL for c in entry["checks"]):
            failed += 1
        results.append(entry)

    ok = failed == 0
    return {
        "ok": ok,
        "base": base,
        "head": head,
        "min_tier": min_tier,
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
        f"  Min Tier:   {report.get('min_tier') or 'self-recorded'}",
        f"  Commits:    {report['commit_count']}",
        f"  Attested:   {report['counts']['attested']}",
        f"  Unattested: {report['counts']['unattested']}",
        f"  Failed:     {report['counts']['failed']}",
        bar,
    ]
    for c in report["commits"]:
        title = c["mission_id"] or "(unattested)"
        tier = c.get("tier") or "no tier"
        lines.append(f"  {c['short']}  {title}  [{tier}]")
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
        f"- **Minimum tier:** {report.get('min_tier') or 'self-recorded'}",
        f"- **Commits:** {report['commit_count']} "
        f"({report['counts']['attested']} attested, "
        f"{report['counts']['unattested']} unattested, "
        f"{report['counts']['failed']} failed)",
        f"- **Generated:** {report['generated_at']}",
        "",
        "| Commit | Mission | Tier | Check | Level | Detail |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in report["commits"]:
        mission = c["mission_id"] or "—"
        tier = c.get("tier") or "—"
        for check in c["checks"]:
            lines.append(
                f"| `{c['short']}` | {mission} | {tier} | {check['name']} "
                f"| {check['level']} | {check['detail']} |"
            )
    lines.append("")
    return "\n".join(lines)
