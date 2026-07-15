"""
witness.py — an independent, stateful cosigner for ledger checkpoints.

A witness is the anti-equivocation half of the transparency design.
It is a small program that, PER ORIGIN:

  1. REMEMBERS the last checkpoint it cosigned (a JSON state file),
  2. verifies that a newly presented checkpoint is an append-only
     EXTENSION of the remembered one (consistency proof from the
     remembered size/root to the new size/root),
  3. REFUSES forks, rollbacks, and same-size root swaps — loudly,
     with a non-zero exit and without updating its state,
  4. cosigns the new checkpoint with the witness's own key
     (see checkpoint.py for the cosignature format).

A repo operator can fabricate a fresh internally-consistent ledger at
any time — but it cannot make a witness that remembers yesterday's
checkpoint accept today's rewrite. Show two witnesses two different
logs and each will happily cosign what it saw — but no single
checkpoint can then gather cosignatures from both: a client requiring
N pinned cosignatures forces the operator to show the SAME log to N
independent parties.

INDEPENDENCE IS THE WHOLE POINT. The witness must run OUTSIDE the
attested repo's trust domain: a different machine, repo, and CI, with
its own key and its own state storage, none of which the attested
repo's operator (or its coding agents) can write to. A witness running
on the operator's workstation remembers only what the operator lets it
remember. See templates/sao-witness.yml for a separate-repo CI setup.

Trust-on-first-use: the FIRST checkpoint a witness sees for an origin
is recorded as-is — the witness attests to append-only-ness *from that
point on*, not to the honesty of history before it. This TOFU bootstrap
is a real limitation; distribute the first checkpoint over a second
channel if it matters.

To verify growth the witness needs the ledger's leaves — via its own
read-only clone of the attested repo (--ledger-repo) or via a
consistency proof bundled into the checkpoint at emit time
(``sao checkpoint emit --bundle-proof-from <last-size>``). A checkpoint
that provides neither is REFUSED, not trusted.

State: one JSON file per origin in --state-dir
(``<origin-slug>.json``), updated atomically, and only after a
successful cosign.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import checkpoint as checkpoint_mod, ledger as ledger_mod

STATE_VERSION = "sao-witness-state/1"

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

#: The loud refusal marker — grep-able, and present in every refusal path.
REFUSAL_MARKER = "possible equivocation/fork"


def _check(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


# ── State files ───────────────────────────────────────────────────────────────

def state_path(state_dir, origin: str) -> Path:
    return Path(state_dir) / f"{checkpoint_mod.origin_slug(origin)}.json"


def load_state(state_dir, origin: str):
    """Return the remembered state for *origin*, or None (first encounter)."""
    path = state_path(state_dir, origin)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state_dir, origin: str, state: dict) -> Path:
    """Atomically persist *state* for *origin* (write temp + rename)."""
    path = state_path(state_dir, origin)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def list_states(state_dir) -> list:
    """All remembered origins in *state_dir* (list of state dicts)."""
    directory = Path(state_dir)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(state, dict) and state.get("version") == STATE_VERSION:
            state["_path"] = str(path)
            out.append(state)
    return out


# ── Consistency verification paths ────────────────────────────────────────────

def _verify_growth(cp: dict, state: dict, ledger_repo):
    """Verify append-only growth from the remembered state to *cp*.

    Uses the witness's own ledger clone when *ledger_repo* is given,
    otherwise a proof bundled in the checkpoint. Returns a check dict.
    """
    old_size = state["tree_size"]
    old_root = state["root_hash"]
    new_size = cp["tree_size"]
    new_root = cp["root_hash"]

    if ledger_repo is not None:
        ledger = ledger_mod.Ledger(ledger_repo)
        clone_size = ledger.size()
        if clone_size < new_size:
            return _check(
                "consistency", FAIL,
                f"ledger clone at {ledger_repo} has only {clone_size} "
                f"entries but the checkpoint claims {new_size} — cannot "
                f"verify growth ({REFUSAL_MARKER})",
            )
        if ledger.root_at(new_size) != new_root:
            return _check(
                "consistency", FAIL,
                f"checkpoint root at size {new_size} does not match the "
                f"ledger clone — {REFUSAL_MARKER}",
            )
        proof = ledger.consistency_proof(old_size, new_size)
        source = "ledger clone"
    else:
        bundled = next(
            (
                p for p in cp.get("bundled_proofs", [])
                if p.get("old_size") == old_size
            ),
            None,
        )
        if bundled is None:
            return _check(
                "consistency", FAIL,
                f"no way to verify growth from remembered size {old_size}: "
                "checkpoint bundles no proof for that size and no "
                "--ledger-repo was given — refusing to cosign blind "
                f"({REFUSAL_MARKER})",
            )
        proof = bundled.get("proof", [])
        source = "bundled proof"

    if ledger_mod.verify_consistency(old_size, new_size, old_root, new_root, proof):
        return _check(
            "consistency", OK,
            f"append-only growth {old_size} -> {new_size} verified via "
            f"{source}",
        )
    return _check(
        "consistency", FAIL,
        f"consistency proof from remembered ({old_size}, "
        f"{old_root[:12]}…) to ({new_size}, {new_root[:12]}…) does NOT "
        f"verify — {REFUSAL_MARKER}",
    )


# ── Cosigning ─────────────────────────────────────────────────────────────────

def cosign(
    checkpoint_path,
    state_dir,
    name: str,
    signer,
    ledger_repo=None,
    operator_hmac_key_file=None,
    operator_allowed_signers=None,
    operator_identity: str = "sao",
) -> dict:
    """Verify a checkpoint against remembered state, then cosign it.

    On ANY verification failure the witness refuses: ok=False, the
    checkpoint file is left untouched, and the state is NOT updated.
    On success the cosignature is appended to the checkpoint document
    and the state advances atomically.

    Returns {"ok", "action", "tofu", "origin", "tree_size", "root_hash",
             "checks", "checkpoint_path", "state_path", "witness"}.
    """
    checks = []
    report = {
        "ok": False,
        "action": "refused",
        "tofu": False,
        "origin": None,
        "tree_size": None,
        "root_hash": None,
        "checks": checks,
        "checkpoint_path": str(checkpoint_path),
        "state_path": None,
        "witness": name,
    }

    try:
        cp = checkpoint_mod.load_checkpoint(checkpoint_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        checks.append(_check("checkpoint", FAIL, f"cannot load checkpoint: {e}"))
        return report
    origin = cp.get("origin")
    report.update(
        origin=origin,
        tree_size=cp.get("tree_size"),
        root_hash=cp.get("root_hash"),
    )
    if not origin or not isinstance(cp.get("tree_size"), int) or not cp.get("root_hash"):
        checks.append(_check(
            "checkpoint", FAIL, "checkpoint lacks origin/tree_size/root_hash"
        ))
        return report
    checks.append(_check(
        "checkpoint", OK,
        f"{cp['version']} origin {origin!r} size {cp['tree_size']}",
    ))

    # Operator signature: verified when the witness pinned key material,
    # WARN otherwise — a witness only attests to CONSISTENCY, but it
    # should know whether the operator even stands behind the checkpoint.
    if not cp.get("signature"):
        checks.append(_check(
            "operator-signature", WARN,
            "checkpoint is unsigned — cosigning consistency of an "
            "anonymous claim",
        ))
    elif operator_hmac_key_file or operator_allowed_signers:
        sig_ok = checkpoint_mod.verify_operator_signature(
            cp,
            hmac_key_file=operator_hmac_key_file,
            allowed_signers=operator_allowed_signers,
            identity=operator_identity,
        )
        if sig_ok:
            checks.append(_check(
                "operator-signature", OK, "operator signature verifies"
            ))
        else:
            checks.append(_check(
                "operator-signature", FAIL,
                "operator signature does not verify against the pinned "
                "operator key",
            ))
    else:
        checks.append(_check(
            "operator-signature", WARN,
            "operator signature present but unverified (no pinned "
            "operator key material)",
        ))

    state = load_state(state_dir, origin)
    if state is None:
        # Trust-on-first-use bootstrap.
        report["tofu"] = True
        checks.append(_check(
            "state", WARN,
            f"TRUST-ON-FIRST-USE: no remembered checkpoint for origin "
            f"{origin!r}; recording (size {cp['tree_size']}, root "
            f"{cp['root_hash'][:12]}…) as the trust anchor. This witness "
            "attests to append-only-ness from this point on only.",
        ))
        if ledger_repo is not None:
            ledger = ledger_mod.Ledger(ledger_repo)
            if (
                ledger.size() < cp["tree_size"]
                or ledger.root_at(cp["tree_size"]) != cp["root_hash"]
            ):
                checks.append(_check(
                    "consistency", FAIL,
                    "first-seen checkpoint does not match the ledger clone "
                    f"at size {cp['tree_size']} — {REFUSAL_MARKER}",
                ))
                return report
            checks.append(_check(
                "consistency", OK,
                f"first-seen root matches the ledger clone at size "
                f"{cp['tree_size']}",
            ))
    else:
        if state.get("origin") != origin:
            checks.append(_check(
                "state", FAIL,
                f"state file for this origin slug remembers origin "
                f"{state.get('origin')!r}, not {origin!r} — refusing "
                f"({REFUSAL_MARKER})",
            ))
            return report
        checks.append(_check(
            "state", OK,
            f"remembered checkpoint: size {state['tree_size']}, root "
            f"{state['root_hash'][:12]}…",
        ))
        if cp["tree_size"] < state["tree_size"]:
            checks.append(_check(
                "consistency", FAIL,
                f"ROLLBACK REFUSED: checkpoint size {cp['tree_size']} < "
                f"remembered size {state['tree_size']} — "
                f"{REFUSAL_MARKER}",
            ))
            return report
        if cp["tree_size"] == state["tree_size"]:
            if cp["root_hash"] != state["root_hash"]:
                checks.append(_check(
                    "consistency", FAIL,
                    f"EQUIVOCATION REFUSED: same size {cp['tree_size']} "
                    f"but a different root than remembered — "
                    f"{REFUSAL_MARKER}",
                ))
                return report
            checks.append(_check(
                "consistency", OK,
                f"checkpoint equals the remembered one (size "
                f"{cp['tree_size']}) — re-cosigning",
            ))
        else:
            growth = _verify_growth(cp, state, ledger_repo)
            checks.append(growth)
            if growth["level"] == FAIL:
                return report

    if any(c["level"] == FAIL for c in checks):
        return report

    # All checks passed: cosign, then advance state (in that order — a
    # crash between the two leaves a cosigned checkpoint and stale state,
    # which is safe: the next run just re-verifies growth from earlier).
    checkpoint_mod.add_cosignature(cp, name, signer)
    checkpoint_mod.write_checkpoint(cp, checkpoint_path)
    new_state = {
        "version": STATE_VERSION,
        "origin": origin,
        "tree_size": cp["tree_size"],
        "root_hash": cp["root_hash"],
        "checkpoint_sha256": checkpoint_mod.checkpoint_sha256(cp),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cosign_count": (state or {}).get("cosign_count", 0) + 1,
    }
    report["state_path"] = str(save_state(state_dir, origin, new_state))
    report["ok"] = True
    report["action"] = "cosigned"
    return report


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_report(report: dict) -> str:
    bar = "=" * 64
    lines = ["", bar, "  SPECIAL AGENT OPS — WITNESS COSIGN", bar]
    lines.append(f"  Witness:    {report.get('witness')}")
    if report.get("origin"):
        lines.append(f"  Origin:     {report['origin']}")
    if report.get("tree_size") is not None:
        lines.append(f"  Tree Size:  {report['tree_size']}")
    if report.get("root_hash"):
        lines.append(f"  Root Hash:  {report['root_hash']}")
    for check in report["checks"]:
        lines.append(f"  [{check['level']:<4}] {check['name']}: {check['detail']}")
    lines.append(bar)
    if report["ok"]:
        verb = "COSIGNED (trust-on-first-use)" if report.get("tofu") else "COSIGNED"
        lines.append(f"  Result: {verb}")
        lines.append(f"  Checkpoint: {report['checkpoint_path']}")
        lines.append(f"  State:      {report['state_path']}")
    else:
        lines.append(f"  Result: REFUSED — {REFUSAL_MARKER}; state NOT updated")
    lines.append(bar)
    lines.append("")
    return "\n".join(lines)


def render_states(states: list, state_dir) -> str:
    bar = "=" * 64
    lines = ["", bar, "  SPECIAL AGENT OPS — WITNESS STATE", bar]
    lines.append(f"  State Dir:  {state_dir}")
    if not states:
        lines.append("  (no remembered origins)")
    for state in states:
        lines.append(f"  Origin:     {state.get('origin')}")
        lines.append(
            f"      size {state.get('tree_size')}, "
            f"root {str(state.get('root_hash'))[:16]}…, "
            f"cosigned {state.get('cosign_count')} time(s), "
            f"updated {state.get('updated_at')}"
        )
    lines.append(bar)
    lines.append("")
    return "\n".join(lines)
