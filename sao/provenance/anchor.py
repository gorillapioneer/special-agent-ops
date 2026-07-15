"""
anchor.py — git-native external anchoring of ledger checkpoints.

Witnesses (witness.py) remember checkpoints; anchoring PUBLISHES them
somewhere the operator's repo history cannot quietly rewrite: a ref in
an EXTERNAL git repository. Each anchor is a commit whose tree holds
exactly one file, ``checkpoint.json``, and whose parent is the previous
anchor commit — so the external repo's own history is an append-only
record of checkpoints:

    refs/sao/anchors/<origin-slug>:
        anchor #0 (size 3)  <-  anchor #1 (size 7)  <-  anchor #2 (size 12)

``sao anchor push`` fetches the current anchor tip, refuses to anchor a
checkpoint whose tree_size does not strictly grow past it, and pushes
the new anchor as a plain fast-forward — a rewritten anchor ref makes
the push fail rather than silently diverge.

``sao anchor verify`` fetches the whole chain and checks:
  * linearity — one origin, tree_size strictly increasing along the
    first-parent chain,
  * every anchored root is append-only-consistent with the LOCAL ledger
    (consistency proof from the anchored size to the current size) —
    a local ledger that has been forked or rolled back relative to what
    was anchored fails here,
  * freshness — with --max-age-days N, the newest anchor's timestamp
    must be recent enough. Checkpoint timestamps are OPERATOR CLAIMS
    unless the checkpoint carries verified witness cosignatures; and an
    anchor can only prove staleness bounds as tight as the anchoring
    cadence.

The anchor repo should live where the attested repo's operator (and its
coding agents) cannot force-push: a different hosting account, a repo
with protected refs, or the witness's own repo (the witness workflow in
templates/sao-witness.yml can anchor the checkpoints it cosigns).
Anchoring alone does not verify cosignatures — pair
``sao anchor verify`` with ``sao checkpoint verify --witness-keys …``
(or ``sao verify-pr --min-tier independently-witnessed``) for that.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import checkpoint as checkpoint_mod, ledger as ledger_mod

ANCHOR_REF_PREFIX = "refs/sao/anchors/"
ANCHOR_FILENAME = "checkpoint.json"

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


def _check(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


_GIT_IDENTITY = [
    "-c", "user.name=sao-anchor",
    "-c", "user.email=sao-anchor@blackbox.invalid",
]


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def default_anchor_ref(origin: str) -> str:
    return ANCHOR_REF_PREFIX + checkpoint_mod.origin_slug(origin)


# ── Remote chain access ───────────────────────────────────────────────────────

def _remote_tip(repo_path, remote: str, ref: str):
    """Resolve and fetch the anchor tip on *remote*.

    Returns the tip commit sha, or None when the ref does not exist yet.
    Raises ValueError when the remote itself is unreachable.
    """
    proc = _git(["ls-remote", remote, ref], cwd=repo_path)
    if proc.returncode != 0:
        raise ValueError(
            f"cannot contact anchor remote {remote!r}: {proc.stderr.strip()}"
        )
    line = proc.stdout.strip()
    if not line:
        return None
    tip = line.split()[0]
    fetch = _git(["fetch", "-q", remote, ref], cwd=repo_path)
    if fetch.returncode != 0:
        raise ValueError(
            f"cannot fetch anchor ref {ref!r} from {remote!r}: "
            f"{fetch.stderr.strip()}"
        )
    return tip


def _checkpoint_at(repo_path, commit: str):
    """Read the checkpoint document stored in an anchor commit, or None."""
    proc = _git(["cat-file", "-p", f"{commit}:{ANCHOR_FILENAME}"], cwd=repo_path)
    if proc.returncode != 0:
        return None
    try:
        cp = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return cp if isinstance(cp, dict) else None


def fetch_chain(repo_path, remote: str, ref: str) -> list:
    """Fetch the anchor chain, oldest first.

    Returns a list of {"commit": sha, "checkpoint": dict|None}. Raises
    ValueError when the remote is unreachable; an absent ref returns [].
    """
    tip = _remote_tip(repo_path, remote, ref)
    if tip is None:
        return []
    proc = _git(["rev-list", "--first-parent", tip], cwd=repo_path)
    if proc.returncode != 0:
        raise ValueError(f"cannot walk anchor chain: {proc.stderr.strip()}")
    shas = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    shas.reverse()
    return [
        {"commit": sha, "checkpoint": _checkpoint_at(repo_path, sha)}
        for sha in shas
    ]


def latest_anchored_checkpoint(repo_path, remote: str, ref=None, origin=None):
    """Return (checkpoint_dict, description) for the newest anchor, or
    (None, reason)."""
    if ref is None:
        ref = default_anchor_ref(
            checkpoint_mod.origin_for_repo(repo_path, origin)
        )
    try:
        chain = fetch_chain(repo_path, remote, ref)
    except ValueError as e:
        return None, str(e)
    if not chain:
        return None, f"no anchors on {remote!r} {ref!r}"
    cp = chain[-1]["checkpoint"]
    if cp is None:
        return None, f"newest anchor commit carries no readable {ANCHOR_FILENAME}"
    return cp, f"{remote} {ref} @ {chain[-1]['commit'][:10]}"


# ── Push ──────────────────────────────────────────────────────────────────────

def push(
    repo_path,
    remote: str,
    ref=None,
    checkpoint_path=None,
    origin=None,
) -> dict:
    """Anchor a checkpoint as a new commit on the external anchor ref.

    Without *checkpoint_path*, a fresh UNSIGNED checkpoint of the current
    ledger is anchored (anchor what the ledger says right now); pass the
    signed/cosigned checkpoint file to anchor that instead.

    Returns {"ok", "checks", "remote", "ref", "commit", "origin",
             "tree_size", "root_hash"}.
    """
    repo_path = Path(repo_path)
    checks = []
    report = {
        "ok": False,
        "checks": checks,
        "remote": remote,
        "ref": None,
        "commit": None,
        "origin": None,
        "tree_size": None,
        "root_hash": None,
    }

    if checkpoint_path is not None:
        try:
            cp = checkpoint_mod.load_checkpoint(checkpoint_path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            checks.append(_check("checkpoint", FAIL, f"cannot load: {e}"))
            return report
        source = str(checkpoint_path)
    else:
        cp = checkpoint_mod.build_checkpoint(repo_path, origin=origin)
        source = "current ledger (unsigned checkpoint built on the fly)"
    report.update(
        origin=cp.get("origin"),
        tree_size=cp.get("tree_size"),
        root_hash=cp.get("root_hash"),
    )
    if ref is None:
        ref = default_anchor_ref(cp["origin"])
    report["ref"] = ref
    checks.append(_check(
        "checkpoint", OK,
        f"size {cp['tree_size']}, {len(cp.get('cosignatures', []))} "
        f"cosignature(s), from {source}",
    ))

    try:
        tip = _remote_tip(repo_path, remote, ref)
    except ValueError as e:
        checks.append(_check("remote", FAIL, str(e)))
        return report

    parent_args = []
    if tip is not None:
        prev = _checkpoint_at(repo_path, tip)
        if prev is None:
            checks.append(_check(
                "chain", FAIL,
                f"existing anchor tip {tip[:10]} carries no readable "
                f"{ANCHOR_FILENAME} — refusing to extend an unknown chain",
            ))
            return report
        if prev.get("origin") != cp.get("origin"):
            checks.append(_check(
                "chain", FAIL,
                f"anchor ref belongs to origin {prev.get('origin')!r}, "
                f"checkpoint is for {cp.get('origin')!r}",
            ))
            return report
        if cp["tree_size"] <= prev.get("tree_size", -1):
            same = (
                cp["tree_size"] == prev.get("tree_size")
                and cp["root_hash"] == prev.get("root_hash")
            )
            checks.append(_check(
                "chain", FAIL,
                (
                    f"size {cp['tree_size']} is already anchored (anchors "
                    "must strictly grow)"
                    if same else
                    f"checkpoint size {cp['tree_size']} does not grow past "
                    f"the anchored size {prev.get('tree_size')} — refusing "
                    "a rollback/equivocation anchor"
                ),
            ))
            return report
        checks.append(_check(
            "chain", OK,
            f"extends anchor {tip[:10]} (size {prev['tree_size']} -> "
            f"{cp['tree_size']})",
        ))
        parent_args = ["-p", tip]
    else:
        checks.append(_check("chain", OK, "no existing anchors — starting the chain"))

    # Build the anchor commit with plumbing: blob -> tree -> commit.
    blob_proc = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo_path,
        input=json.dumps(cp, indent=2, sort_keys=True) + "\n",
        capture_output=True,
        text=True,
    )
    tree_proc = subprocess.run(
        ["git", "mktree"],
        cwd=repo_path,
        input=f"100644 blob {blob_proc.stdout.strip()}\t{ANCHOR_FILENAME}\n",
        capture_output=True,
        text=True,
    )
    message = (
        f"sao anchor: {cp['origin']} size {cp['tree_size']} "
        f"root {cp['root_hash'][:12]}"
    )
    commit_proc = _git(
        [*_GIT_IDENTITY, "commit-tree", tree_proc.stdout.strip(),
         *parent_args, "-m", message],
        cwd=repo_path,
    )
    commit = commit_proc.stdout.strip()
    if commit_proc.returncode != 0 or not commit:
        checks.append(_check(
            "anchor-commit", FAIL,
            f"could not build anchor commit: {commit_proc.stderr.strip()}",
        ))
        return report
    checks.append(_check("anchor-commit", OK, f"anchor commit {commit[:10]}"))

    # Plain (fast-forward) push — a rewritten remote ref makes this fail
    # loudly instead of silently replacing history.
    push_proc = _git(["push", "-q", remote, f"{commit}:{ref}"], cwd=repo_path)
    if push_proc.returncode != 0:
        checks.append(_check(
            "push", FAIL,
            f"push to {remote!r} {ref!r} failed (non-fast-forward means "
            f"the anchor ref moved or was rewritten): "
            f"{push_proc.stderr.strip()}",
        ))
        return report
    checks.append(_check("push", OK, f"anchored on {remote} {ref}"))
    report["commit"] = commit
    report["ok"] = True
    return report


# ── Verify ────────────────────────────────────────────────────────────────────

def _age_days(timestamp: str):
    try:
        then = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0


def verify(
    repo_path,
    remote: str,
    ref=None,
    origin=None,
    max_age_days=None,
) -> dict:
    """Verify the anchor chain on *remote* against the local ledger.

    Returns {"ok", "checks", "remote", "ref", "anchors", "latest"} where
    latest is {"commit", "tree_size", "root_hash", "timestamp",
    "age_days", "cosignature_count"} or None.
    """
    repo_path = Path(repo_path)
    if ref is None:
        ref = default_anchor_ref(checkpoint_mod.origin_for_repo(repo_path, origin))
    checks = []
    report = {
        "ok": False,
        "checks": checks,
        "remote": remote,
        "ref": ref,
        "anchors": 0,
        "latest": None,
    }

    try:
        chain = fetch_chain(repo_path, remote, ref)
    except ValueError as e:
        checks.append(_check("remote", FAIL, str(e)))
        return report
    if not chain:
        checks.append(_check(
            "chain", FAIL, f"no anchors found on {remote!r} {ref!r}"
        ))
        return report
    report["anchors"] = len(chain)
    checks.append(_check(
        "chain", OK, f"{len(chain)} anchor commit(s) fetched from {remote}"
    ))

    ledger = ledger_mod.Ledger(repo_path)
    current = ledger.root()

    prev = None
    origins = set()
    linear = True
    for link in chain:
        cp = link["checkpoint"]
        short = link["commit"][:10]
        if (
            cp is None
            or cp.get("version") != checkpoint_mod.CHECKPOINT_VERSION
            or not isinstance(cp.get("tree_size"), int)
            or not cp.get("root_hash")
        ):
            checks.append(_check(
                "linearity", FAIL,
                f"anchor {short} carries no valid checkpoint",
            ))
            linear = False
            continue
        origins.add(cp.get("origin"))
        if prev is not None and cp["tree_size"] <= prev["tree_size"]:
            checks.append(_check(
                "linearity", FAIL,
                f"anchor {short} (size {cp['tree_size']}) does not grow "
                f"past the previous anchor (size {prev['tree_size']}) — "
                "the anchor ref has been rewritten or replayed",
            ))
            linear = False
        prev = cp

        # Root vs the local ledger, via a consistency proof to now.
        size = cp["tree_size"]
        if size > current["tree_size"]:
            checks.append(_check(
                "ledger", FAIL,
                f"anchor {short} claims size {size} but the local ledger "
                f"has only {current['tree_size']} entries — the LOCAL "
                "ledger is behind what was anchored (possible local "
                "rollback/fork)",
            ))
            continue
        proof = ledger.consistency_proof(size)
        if ledger_mod.verify_consistency(
            size, current["tree_size"], cp["root_hash"],
            current["root_hash"], proof,
        ):
            checks.append(_check(
                "ledger", OK,
                f"anchor {short} (size {size}) is consistent with the "
                f"local ledger (size {current['tree_size']})",
            ))
        else:
            checks.append(_check(
                "ledger", FAIL,
                f"anchor {short} root at size {size} is NOT consistent "
                "with the local ledger — fork/equivocation between what "
                "was anchored and what the repo now shows",
            ))

    if linear and prev is not None:
        checks.append(_check(
            "linearity", OK,
            f"tree sizes strictly increase along the chain "
            f"(latest size {prev['tree_size']})",
        ))
    if len(origins) > 1:
        checks.append(_check(
            "linearity", FAIL,
            f"anchor chain mixes origins: {sorted(map(str, origins))}",
        ))

    latest_cp = chain[-1]["checkpoint"] or {}
    age = _age_days(latest_cp.get("timestamp"))
    report["latest"] = {
        "commit": chain[-1]["commit"],
        "tree_size": latest_cp.get("tree_size"),
        "root_hash": latest_cp.get("root_hash"),
        "timestamp": latest_cp.get("timestamp"),
        "age_days": round(age, 3) if age is not None else None,
        "cosignature_count": len(latest_cp.get("cosignatures", [])),
    }
    if max_age_days is not None:
        if age is None:
            checks.append(_check(
                "freshness", FAIL,
                "latest anchor carries no parseable timestamp",
            ))
        elif age > float(max_age_days):
            checks.append(_check(
                "freshness", FAIL,
                f"latest anchor is {age:.1f} day(s) old, over the "
                f"--max-age-days {max_age_days} limit",
            ))
        else:
            checks.append(_check(
                "freshness", OK,
                f"latest anchor is {age:.2f} day(s) old "
                f"(limit {max_age_days})",
            ))
    else:
        checks.append(_check(
            "freshness", WARN,
            (
                f"latest anchor is {age:.2f} day(s) old — timestamps are "
                "operator claims unless the checkpoint is witnessed; pass "
                "--max-age-days to enforce a bound"
            ) if age is not None else
            "latest anchor carries no parseable timestamp",
        ))

    report["ok"] = not any(c["level"] == FAIL for c in checks)
    return report


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_report(report: dict, title: str) -> str:
    bar = "=" * 64
    lines = ["", bar, f"  SPECIAL AGENT OPS — {title}", bar]
    lines.append(f"  Remote:     {report.get('remote')}")
    lines.append(f"  Ref:        {report.get('ref')}")
    if report.get("origin"):
        lines.append(f"  Origin:     {report['origin']}")
    if report.get("anchors"):
        lines.append(f"  Anchors:    {report['anchors']}")
    latest = report.get("latest")
    if latest:
        lines.append(
            f"  Latest:     size {latest['tree_size']}, "
            f"{latest['cosignature_count']} cosignature(s), "
            f"age {latest['age_days']} day(s)"
        )
    for check in report["checks"]:
        lines.append(f"  [{check['level']:<4}] {check['name']}: {check['detail']}")
    lines.append(bar)
    if report["ok"]:
        lines.append(
            f"  Result: ANCHORED — commit {report['commit'][:10]}"
            if report.get("commit")
            else "  Result: VERIFIED"
        )
    else:
        lines.append("  Result: FAILED")
    lines.append(bar)
    lines.append("")
    return "\n".join(lines)
