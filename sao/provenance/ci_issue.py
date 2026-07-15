"""
ci_issue.py — CI-side attestation issuance: the ``CI-verified`` tier.

Design principle (docs/THREAT_MODEL.md): the workstation recorder submits
EVIDENCE; it must not issue the final authoritative claim. A trusted CI
job runs ``sao ci-issue`` to:

  1. locate the session/evidence bundle for the commit (via the
     refs/notes/sao note's payload_sha256 -> session provenance.json,
     or an explicit --session mission id),
  2. verify the evidence: seal verification of the session directory,
     ledger inclusion + consistency, attestation payload hash vs note,
  3. independently recompute git reality: the commit's tree OID and the
     changed paths + blob OIDs/modes vs its parent — and compare them
     against the evidence's git_objects claims. Any mismatch refuses
     issuance,
  4. apply policy: changed files within the flight-plan scope
     (strict/advisory) and recorded exit_code == 0 unless
     --allow-failed-checks,
  5. collect the CI identity (GITHUB_ACTIONS env) into issuer claims —
     mode is "ci" only when GITHUB_ACTIONS=true; a local ci-issue run
     NEVER claims ci-verified,
  6. emit a DSSE-wrapped in-toto Statement and attach a discovery
     pointer as a git note under refs/notes/sao-ci (statement sha256 +
     location), mirroring the notes-as-discovery-index convention.

The assurance tier in the emitted statement is:

    ci-verified     when issuer mode == "ci" AND the envelope is signed
    locally-signed  when signed but issued outside CI
    self-recorded   when unsigned (signer "none")

``sao ci-verify`` checks an emitted envelope: DSSE signature, statement
subject vs the actual commit/tree, and a re-run of the git reality
checks. It is what a downstream consumer (verify-pr --min-tier, an
auditor) uses to accept or reject the CI claim.

What this tier adds — and does not add: issuing the final claim outside
the workstation closes workstation-side forgery of the *attestation*;
it does NOT make the locally recorded evidence truthful. The CI job
independently recomputes git reality and applies policy, so the claim
"this commit matches its declared evidence and policy" is now made by
an identity the coding agent cannot access.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sao.blackbox import browser
from . import attest, envelope as envelope_mod, flightplan, ledger as ledger_mod
from . import verify_pr as verify_pr_mod

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

CI_NOTES_REF = "refs/notes/sao-ci"
CI_NOTE_VERSION = "sao-ci-note/1"

#: Default output location for issued envelopes, repo-relative.
DEFAULT_OUT_DIR = Path("blackbox") / "ci-attestations"


def _check(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _resolve_commit(repo_path: Path, ref: str):
    proc = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo_path)
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


# ── Issuer identity ───────────────────────────────────────────────────────────

_GITHUB_CLAIM_VARS = (
    "GITHUB_REPOSITORY",
    "GITHUB_REPOSITORY_OWNER",
    "GITHUB_WORKFLOW",
    "GITHUB_WORKFLOW_REF",
    "GITHUB_WORKFLOW_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_ACTOR",
    "GITHUB_SHA",
    "GITHUB_REF",
    "GITHUB_EVENT_NAME",
    "RUNNER_ENVIRONMENT",
)


def collect_ci_identity() -> dict:
    """Collect the issuing environment's identity claims.

    Returns {"mode": "ci"|"local", "provider": ..., "claims": {...}}.
    mode is "ci" ONLY when GITHUB_ACTIONS=true — a workstation running
    ci-issue stays "local" and cannot mint a ci-verified claim.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        claims = {}
        for var in _GITHUB_CLAIM_VARS:
            value = os.environ.get(var)
            if value:
                claims[var.lower()] = value
        return {"mode": "ci", "provider": "github-actions", "claims": claims}
    return {"mode": "local", "provider": None, "claims": {}}


# ── Evidence location ─────────────────────────────────────────────────────────

def locate_session(repo_path: Path, commit: str, mission_id=None):
    """Locate the evidence bundle (session dir) for *commit*.

    Returns (session_dir, statement, note, error_detail). With
    *mission_id* the session is addressed directly; otherwise the
    refs/notes/sao discovery note on the commit supplies the mission id.
    The durable statement is always read from the session's
    provenance.json — never trusted from the note alone.
    """
    repo_path = Path(repo_path)
    note = attest.read_git_note(repo_path, commit)
    if mission_id is None:
        if note is None:
            return None, None, None, (
                "no refs/notes/sao note on commit and no --session given: "
                "evidence bundle not discoverable"
            )
        note_statement, _ = attest.note_statement_and_payload(note)
        mission_id = note_statement.get("mission_id")
        if not mission_id:
            return None, None, note, "attestation note carries no mission_id"

    session_dir = repo_path / "blackbox" / "sessions" / str(mission_id)
    if not session_dir.is_dir():
        return None, None, note, (
            f"session folder not found for mission {mission_id}: "
            "the evidence bundle must be present where ci-issue runs"
        )
    statement, _ = attest.load_attestation(session_dir)
    if statement is None:
        return session_dir, None, note, (
            f"session {mission_id} has no readable provenance.json"
        )
    return session_dir, statement, note, None


# ── Evidence verification ─────────────────────────────────────────────────────

def _check_seal(session_dir: Path) -> dict:
    try:
        result = browser.verify_mission(session_dir)
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        return _check("evidence-seal", FAIL, f"seal verification failed: {e}")
    if not result["manifest_ok"] or not result["session_directory_ok"]:
        return _check(
            "evidence-seal", FAIL,
            "session seal does not verify (manifest or directory hash mismatch)",
        )
    if result["archive_found"] and not result["archive_ok"]:
        return _check("evidence-seal", FAIL, "archive hash mismatch vs seal")
    if not result["archive_found"]:
        return _check(
            "evidence-seal", WARN,
            "manifest + directory hashes verify; archive not present",
        )
    return _check("evidence-seal", OK, "seal verifies (manifest, directory, archive)")


def _check_note_payload(session_dir: Path, note) -> dict:
    """Cross-check the discovery note against the durable session copy."""
    if note is None:
        return _check(
            "evidence-note", WARN,
            "no refs/notes/sao note on commit (session addressed directly)",
        )
    _, session_text = attest.load_attestation(session_dir)
    note_statement, note_payload_sha = attest.note_statement_and_payload(note)
    note_text = attest.canonical_json(note_statement)
    if session_text != note_text:
        return _check(
            "evidence-note", FAIL,
            "git note statement differs from session provenance.json",
        )
    if note_payload_sha is not None:
        session_sha = hashlib.sha256(session_text.encode("utf-8")).hexdigest()
        if note_payload_sha != session_sha:
            return _check(
                "evidence-note", FAIL,
                "note payload_sha256 does not match session provenance.json",
            )
    return _check("evidence-note", OK, "note matches session copy (payload hash checked)")


# ── Independent git recomputation ─────────────────────────────────────────────

def recompute_git_objects(repo_path: Path, commit: str):
    """Recompute the commit's git objects from git itself.

    Same shape as attest.collect_git_objects, but derived at issuance
    time from the repository the CI job fetched — not from the evidence.
    """
    parent_proc = _git(["rev-parse", "--verify", f"{commit}^"], cwd=repo_path)
    parent = parent_proc.stdout.strip() if parent_proc.returncode == 0 else None
    return attest.collect_git_objects(repo_path, parent, commit)


def compare_git_objects(claimed, actual) -> list:
    """Compare claimed git_objects against independently recomputed ones.

    Returns a list of human-readable mismatch strings (empty = match).
    """
    problems = []
    if claimed is None:
        return ["evidence carries no git_objects claims for this commit"]
    if actual is None:
        return ["could not recompute git objects for the commit"]
    for field in ("commit", "tree"):
        if claimed.get(field) != actual.get(field):
            problems.append(
                f"{field}: claimed {str(claimed.get(field))[:12]} != "
                f"actual {str(actual.get(field))[:12]}"
            )
    if claimed.get("parent_commit") != actual.get("parent_commit"):
        problems.append(
            f"parent_commit: claimed {str(claimed.get('parent_commit'))[:12]} "
            f"!= actual {str(actual.get('parent_commit'))[:12]}"
        )

    def _by_path(gobj):
        return {
            e.get("path"): (e.get("blob"), e.get("mode"), e.get("status"))
            for e in gobj.get("changed", [])
        }

    claimed_map, actual_map = _by_path(claimed), _by_path(actual)
    for path in sorted(set(claimed_map) | set(actual_map)):
        if path not in claimed_map:
            problems.append(f"{path}: changed in git but not claimed")
        elif path not in actual_map:
            problems.append(f"{path}: claimed but not changed in git")
        elif claimed_map[path] != actual_map[path]:
            problems.append(f"{path}: blob/mode/status mismatch")
    return problems


# ── Policy ────────────────────────────────────────────────────────────────────

def _check_scope_policy(session_dir, changed_paths, strict_scope) -> dict:
    plan = flightplan.load_session_plan(session_dir)
    if plan is None:
        return _check("policy-scope", SKIP, "no flight plan filed for this mission")
    strict = strict_scope or plan.get("enforcement") == "strict"
    result = flightplan.check_scope(changed_paths, plan.get("scope", []))
    if result["ok"]:
        return _check(
            "policy-scope", OK,
            f"{len(result['in_scope'])} changed file(s) within declared scope",
        )
    drift = ", ".join(result["out_of_scope"][:5])
    return _check(
        "policy-scope",
        FAIL if strict else WARN,
        f"out of declared scope ({'strict' if strict else 'advisory'}): {drift}",
    )


def _check_exit_code(statement, allow_failed_checks) -> dict:
    exit_code = statement.get("exit_code")
    if exit_code == 0:
        return _check("policy-checks", OK, "recorded mission exit code 0")
    if allow_failed_checks:
        return _check(
            "policy-checks", WARN,
            f"recorded exit code {exit_code} allowed by --allow-failed-checks",
        )
    return _check(
        "policy-checks", FAIL,
        f"recorded mission exit code {exit_code} != 0 "
        "(use --allow-failed-checks to override)",
    )


# ── Issuance ──────────────────────────────────────────────────────────────────

def _relative_location(repo_path: Path, out_path: Path) -> str:
    try:
        return out_path.resolve().relative_to(Path(repo_path).resolve()).as_posix()
    except ValueError:
        return str(out_path)


def issue(
    repo_path: Path,
    commit: str,
    mission_id=None,
    signer_kind: str = "none",
    key_file=None,
    out_path=None,
    allow_failed_checks: bool = False,
    strict_scope: bool = False,
) -> dict:
    """Verify evidence + git reality + policy, then issue a CI attestation.

    Returns a report dict:
        {"ok", "tier", "checks", "commit", "mission_id",
         "out_path", "statement_sha256", "note_attached"}.
    Nothing is written when any check FAILs.
    """
    repo_path = Path(repo_path)
    checks = []
    report = {
        "ok": False,
        "tier": None,
        "checks": checks,
        "commit": None,
        "mission_id": None,
        "out_path": None,
        "statement_sha256": None,
        "note_attached": False,
    }

    resolved = _resolve_commit(repo_path, commit)
    if resolved is None:
        checks.append(_check("commit", FAIL, f"cannot resolve commit {commit!r}"))
        return report
    commit = resolved
    report["commit"] = commit

    # 1. Locate the evidence bundle.
    session_dir, statement, note, err = locate_session(repo_path, commit, mission_id)
    if err is not None:
        checks.append(_check("evidence-bundle", FAIL, err))
        return report
    report["mission_id"] = statement.get("mission_id")
    checks.append(_check(
        "evidence-bundle", OK,
        f"session {statement.get('mission_id')} ({statement.get('version')})",
    ))

    # 2. Verify the evidence: seal, ledger, note payload hash.
    checks.append(_check_seal(session_dir))
    ledger = ledger_mod.Ledger(repo_path)
    checks.extend(verify_pr_mod._check_ledger(statement, ledger))
    checks.append(_check_note_payload(session_dir, note))

    # 3. Independently recompute git reality and compare with claims.
    actual_objects = recompute_git_objects(repo_path, commit)
    claimed_objects = statement.get("git_objects")
    if claimed_objects is not None and claimed_objects.get("commit") != commit:
        checks.append(_check(
            "git-reality", FAIL,
            f"evidence claims commit {str(claimed_objects.get('commit'))[:12]}, "
            f"asked to attest {commit[:12]}",
        ))
    else:
        mismatches = compare_git_objects(claimed_objects, actual_objects)
        if mismatches:
            checks.append(_check(
                "git-reality", FAIL,
                "evidence does not match git: " + "; ".join(mismatches[:5]),
            ))
        else:
            checks.append(_check(
                "git-reality", OK,
                f"tree {actual_objects['tree'][:10]} and "
                f"{len(actual_objects['changed'])} changed path(s) "
                "recomputed from git match the evidence claims",
            ))

    # 4. Policy: flight-plan scope over recomputed paths, recorded checks.
    changed_paths = (
        [e["path"] for e in actual_objects.get("changed", [])]
        if actual_objects else []
    )
    checks.append(_check_scope_policy(session_dir, changed_paths, strict_scope))
    checks.append(_check_exit_code(statement, allow_failed_checks))

    # 5. Issuer identity and signer.
    issuer = collect_ci_identity()
    try:
        signer = envelope_mod.make_signer(signer_kind, key_file)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        checks.append(_check("signer", FAIL, str(e)))
        return report

    signed = signer.kind != "none"
    if issuer["mode"] == "ci" and signed:
        tier = envelope_mod.TIER_CI_VERIFIED
    elif signed:
        tier = envelope_mod.TIER_LOCALLY_SIGNED
    else:
        tier = envelope_mod.TIER_SELF_RECORDED
    report["tier"] = tier
    if issuer["mode"] != "ci":
        checks.append(_check(
            "issuer", WARN,
            f"not running under CI (GITHUB_ACTIONS!=true): tier stays {tier}",
        ))
    else:
        checks.append(_check(
            "issuer", OK,
            f"CI identity: {issuer['claims'].get('github_repository', '?')} "
            f"run {issuer['claims'].get('github_run_id', '?')} -> tier {tier}",
        ))

    if any(c["level"] == FAIL for c in checks):
        return report

    # 6. Build the statement, wrap, write, and attach the discovery note.
    _, session_text = attest.load_attestation(session_dir)
    seal = json.loads((session_dir / "seal.json").read_text(encoding="utf-8"))
    plan = flightplan.load_session_plan(session_dir)
    predicate = {
        "predicate_version": "sao-ci-attestation/1",
        "assurance_tier": tier,
        "mission": {
            "id": statement.get("mission_id"),
            "name": statement.get("mission_name"),
        },
        "agent": statement.get("agent"),
        "repo": statement.get("repo"),
        "branch": statement.get("branch"),
        "git_objects": actual_objects,
        "checks": {"exit_code": statement.get("exit_code")},
        "flightplan": (
            {
                "sha256": statement.get("flightplan_sha256"),
                "name": plan.get("name"),
                "intent": plan.get("intent"),
                "scope": plan.get("scope"),
            }
            if plan is not None else None
        ),
        "seal_manifest_sha256": statement.get("seal_manifest_sha256"),
        "ledger": statement.get("ledger"),
        "evidence_bundle": {
            "attestation_version": statement.get("version"),
            "attestation_sha256": hashlib.sha256(
                session_text.encode("utf-8")
            ).hexdigest(),
            "session_directory_sha256": seal.get("session_directory_sha256"),
            "archive_sha256": seal.get("archive_sha256"),
        },
        "policy": {
            "strict_scope": bool(strict_scope),
            "allow_failed_checks": bool(allow_failed_checks),
            "verdicts": [dict(c) for c in checks],
        },
        "issuer": {
            **issuer,
            "signer": signer.kind,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    stmt = envelope_mod.build_statement(
        subject_name=statement.get("repo") or Path(repo_path).name,
        commit=commit,
        tree=actual_objects["tree"],
        predicate=predicate,
    )
    dsse = envelope_mod.wrap_envelope(stmt, signer)
    stmt_sha = envelope_mod.statement_sha256(stmt)

    if out_path is None:
        out_path = repo_path / DEFAULT_OUT_DIR / f"{commit}.dsse.json"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dsse, indent=2) + "\n", encoding="utf-8")
    report["out_path"] = out_path
    report["statement_sha256"] = stmt_sha

    ci_note = attest.canonical_json({
        "version": CI_NOTE_VERSION,
        "statement_sha256": stmt_sha,
        "location": _relative_location(repo_path, out_path),
        "tier": tier,
        "mission_id": statement.get("mission_id"),
        "issued_at": predicate["issuer"]["issued_at"],
    })
    report["note_attached"] = attest.attach_git_note(
        repo_path, commit, ci_note, ref=CI_NOTES_REF
    )
    report["ok"] = True
    return report


# ── Verification of an issued envelope ────────────────────────────────────────

def _check_signature(dsse, hmac_key_file, allowed_signers, signer_identity) -> dict:
    signatures = dsse.get("signatures") or []
    if not signatures:
        return _check(
            "signature", FAIL,
            "envelope is unsigned — an unsigned envelope carries no issuer "
            "identity and cannot support a ci-verified claim",
        )
    if hmac_key_file:
        ok = envelope_mod.verify_envelope_hmac(dsse, hmac_key_file)
        return _check(
            "signature",
            OK if ok else FAIL,
            "HMAC-SHA256 signature verifies against provided key"
            if ok else "HMAC-SHA256 signature does not verify",
        )
    schemes = {s.get("sao_scheme") for s in signatures}
    if "ssh" in schemes or allowed_signers:
        ok = envelope_mod.verify_envelope_ssh(
            dsse, allowed_signers=allowed_signers, identity=signer_identity
        )
        if ok is None:
            return _check("signature", FAIL, "ssh-keygen unavailable to verify")
        if not ok:
            return _check("signature", FAIL, "ssh signature does not verify")
        if allowed_signers:
            return _check("signature", OK, "ssh signature verifies (allowed signers)")
        return _check(
            "signature", WARN,
            "ssh signature structurally valid, but signer identity "
            "unverified (no --allowed-signers)",
        )
    return _check(
        "signature", FAIL,
        "no key material provided (--hmac-key-file or --allowed-signers) "
        "to verify the signature",
    )


def ci_verify(
    repo_path: Path,
    commit: str,
    attestation_path=None,
    dsse=None,
    hmac_key_file=None,
    allowed_signers=None,
    signer_identity: str = "sao",
) -> dict:
    """Verify a CI-issued DSSE envelope against the actual commit.

    Checks: DSSE signature, statement structure, subject digests
    (gitCommit + gitTree) vs the actual commit, and a re-run of the git
    reality comparison against the predicate's git_objects.

    Returns {"ok", "tier", "checks", "commit"}.
    """
    repo_path = Path(repo_path)
    checks = []
    report = {"ok": False, "tier": None, "checks": checks, "commit": None}

    if dsse is None:
        try:
            dsse = json.loads(Path(attestation_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            checks.append(_check("envelope", FAIL, f"cannot read envelope: {e}"))
            return report

    statement, _payload = envelope_mod.envelope_payload(dsse)
    if statement is None:
        checks.append(_check(
            "envelope", FAIL,
            "not a valid DSSE envelope with an in-toto JSON payload",
        ))
        return report
    checks.append(_check("envelope", OK, "DSSE envelope parses"))

    checks.append(_check_signature(dsse, hmac_key_file, allowed_signers, signer_identity))

    if (
        statement.get("_type") != envelope_mod.STATEMENT_TYPE
        or statement.get("predicateType") != envelope_mod.PREDICATE_TYPE
    ):
        checks.append(_check(
            "statement", FAIL,
            f"unexpected statement/predicate type: "
            f"{statement.get('_type')!r} / {statement.get('predicateType')!r}",
        ))
        report["ok"] = False
        return report
    checks.append(_check("statement", OK, "in-toto Statement/v1 with sao predicate"))

    resolved = _resolve_commit(repo_path, commit)
    if resolved is None:
        checks.append(_check("subject", FAIL, f"cannot resolve commit {commit!r}"))
        return report
    report["commit"] = resolved

    subjects = statement.get("subject") or []
    digest = (subjects[0].get("digest") or {}) if subjects else {}
    proc = _git(["rev-parse", "--verify", f"{resolved}^{{tree}}"], cwd=repo_path)
    actual_tree = proc.stdout.strip() if proc.returncode == 0 else None
    if digest.get("gitCommit") != resolved:
        checks.append(_check(
            "subject", FAIL,
            f"subject gitCommit {str(digest.get('gitCommit'))[:12]} is not "
            f"the requested commit {resolved[:12]}",
        ))
    elif actual_tree is None or digest.get("gitTree") != actual_tree:
        checks.append(_check(
            "subject", FAIL,
            "subject gitTree does not match the commit's actual tree",
        ))
    else:
        checks.append(_check(
            "subject", OK,
            f"subject matches commit {resolved[:10]} / tree {actual_tree[:10]}",
        ))

    predicate = statement.get("predicate") or {}
    mismatches = compare_git_objects(
        predicate.get("git_objects"), recompute_git_objects(repo_path, resolved)
    )
    if mismatches:
        checks.append(_check(
            "git-reality", FAIL,
            "predicate does not match git: " + "; ".join(mismatches[:5]),
        ))
    else:
        checks.append(_check("git-reality", OK, "predicate git_objects match git"))

    tier = predicate.get("assurance_tier")
    issuer = predicate.get("issuer") or {}
    if tier == envelope_mod.TIER_CI_VERIFIED and issuer.get("mode") != "ci":
        checks.append(_check(
            "tier", FAIL,
            "statement claims ci-verified but issuer mode is not 'ci'",
        ))
    elif envelope_mod.tier_rank(tier) < 0:
        checks.append(_check("tier", FAIL, f"unknown assurance tier {tier!r}"))
    else:
        checks.append(_check("tier", OK, f"assurance tier: {tier}"))

    report["ok"] = not any(c["level"] == FAIL for c in checks)
    report["tier"] = tier if report["ok"] else None
    return report


# ── Discovery for verify-pr ───────────────────────────────────────────────────

def find_ci_attestation(repo_path: Path, commit: str, ci_attestations_dir=None):
    """Find a CI-issued envelope for *commit*.

    Discovery order: the refs/notes/sao-ci pointer note (location field),
    then a scan of *ci_attestations_dir* for an envelope whose statement
    subject gitCommit matches. Returns (envelope_dict, source_str) or
    (None, None).
    """
    repo_path = Path(repo_path)
    note = attest.read_git_note(repo_path, commit, ref=CI_NOTES_REF)
    if isinstance(note, dict) and note.get("location"):
        loc = Path(note["location"])
        path = loc if loc.is_absolute() else repo_path / loc
        if path.exists():
            try:
                dsse = json.loads(path.read_text(encoding="utf-8"))
                return dsse, str(path)
            except (OSError, json.JSONDecodeError):
                pass
    if ci_attestations_dir:
        directory = Path(ci_attestations_dir)
        if directory.is_dir():
            for candidate in sorted(directory.glob("*.json")):
                try:
                    dsse = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                statement, _ = envelope_mod.envelope_payload(dsse)
                if statement is None:
                    continue
                subjects = statement.get("subject") or []
                for subj in subjects:
                    if (subj.get("digest") or {}).get("gitCommit") == commit:
                        return dsse, str(candidate)
    return None, None


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_report(report: dict, title: str) -> str:
    bar = "=" * 64
    lines = ["", bar, f"  SPECIAL AGENT OPS — {title}", bar]
    if report.get("commit"):
        lines.append(f"  Commit:     {report['commit']}")
    if report.get("mission_id"):
        lines.append(f"  Mission:    {report['mission_id']}")
    for check in report["checks"]:
        lines.append(f"  [{check['level']:<4}] {check['name']}: {check['detail']}")
    lines.append(bar)
    if report["ok"]:
        lines.append(f"  Result: ISSUED — tier {report['tier']}"
                     if "out_path" in report
                     else f"  Result: VERIFIED — tier {report['tier']}")
        if report.get("out_path"):
            lines.append(f"  Envelope:  {report['out_path']}")
            lines.append(f"  Statement: sha256 {report['statement_sha256']}")
            lines.append(
                "  Note:      refs/notes/sao-ci attached"
                if report.get("note_attached")
                else "  Note:      refs/notes/sao-ci NOT attached"
            )
    else:
        lines.append("  Result: REFUSED" if "out_path" in report else "  Result: FAILED")
    lines.append(bar)
    lines.append("")
    return "\n".join(lines)
