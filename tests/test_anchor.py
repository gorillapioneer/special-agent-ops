"""Tests for sao.provenance.anchor — external git-native checkpoint anchoring."""

import json
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sao.provenance import anchor, checkpoint, ledger as ledger_mod

from test_checkpoint import ORIGIN, make_hmac_key, make_ledger_repo


@pytest.fixture
def ledger_repo(tmp_path: Path) -> Path:
    return make_ledger_repo(tmp_path)


@pytest.fixture
def anchor_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "anchors.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def grow_ledger(repo: Path, n: int = 1) -> None:
    ledger = ledger_mod.Ledger(repo)
    for _ in range(n):
        ledger.append(f"mission_{secrets.token_hex(4)}", secrets.token_hex(32))


def get_checks(report: dict) -> dict:
    """Last check per name (verify emits one 'ledger' check per anchor)."""
    return {c["name"]: c for c in report["checks"]}


def failed_checks(report: dict) -> list:
    return [c for c in report["checks"] if c["level"] == "FAIL"]


# ── push ──────────────────────────────────────────────────────────────────────

class TestPush:
    def test_first_anchor_starts_chain(self, ledger_repo, anchor_remote):
        report = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        assert report["ok"], report["checks"]
        assert report["commit"]
        assert report["ref"] == anchor.default_anchor_ref(ORIGIN)
        chain = anchor.fetch_chain(ledger_repo, str(anchor_remote), report["ref"])
        assert len(chain) == 1
        assert chain[0]["checkpoint"]["origin"] == ORIGIN

    def test_chain_grows_with_parent_links(self, ledger_repo, anchor_remote):
        first = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        grow_ledger(ledger_repo, 2)
        second = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        assert second["ok"], second["checks"]
        chain = anchor.fetch_chain(ledger_repo, str(anchor_remote), second["ref"])
        assert [link["commit"] for link in chain] == [
            first["commit"], second["commit"]
        ]
        sizes = [link["checkpoint"]["tree_size"] for link in chain]
        assert sizes[1] > sizes[0]

    def test_anchors_witnessed_checkpoint_file(
        self, ledger_repo, anchor_remote, tmp_path
    ):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        w_key = make_hmac_key(tmp_path, "w1.key")
        checkpoint.add_cosignature(
            cp, "w1", checkpoint.make_cosign_signer("hmac", w_key)
        )
        cpath = tmp_path / "checkpoint.json"
        checkpoint.write_checkpoint(cp, cpath)
        report = anchor.push(
            ledger_repo, str(anchor_remote), checkpoint_path=cpath
        )
        assert report["ok"], report["checks"]
        latest, _ = anchor.latest_anchored_checkpoint(
            ledger_repo, str(anchor_remote), ref=report["ref"]
        )
        assert latest["cosignatures"][0]["witness"] == "w1"

    def test_same_size_reanchor_refused(self, ledger_repo, anchor_remote):
        assert anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)["ok"]
        report = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        assert not report["ok"]
        assert "already anchored" in get_checks(report)["chain"]["detail"]

    def test_rollback_anchor_refused(self, ledger_repo, anchor_remote, tmp_path):
        grow_ledger(ledger_repo, 1)
        assert anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)["ok"]
        ledger = ledger_mod.Ledger(ledger_repo)
        old = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        old.update(tree_size=2, root_hash=ledger.root_at(2))
        cpath = tmp_path / "old.json"
        checkpoint.write_checkpoint(old, cpath)
        report = anchor.push(
            ledger_repo, str(anchor_remote), checkpoint_path=cpath
        )
        assert not report["ok"]
        assert "does not grow past" in get_checks(report)["chain"]["detail"]

    def test_origin_mismatch_refused(self, ledger_repo, anchor_remote, tmp_path):
        first = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        assert first["ok"]
        grow_ledger(ledger_repo, 1)
        other = checkpoint.build_checkpoint(ledger_repo, origin="other/repo")
        cpath = tmp_path / "other.json"
        checkpoint.write_checkpoint(other, cpath)
        report = anchor.push(
            ledger_repo, str(anchor_remote),
            ref=first["ref"], checkpoint_path=cpath,
        )
        assert not report["ok"]
        assert "belongs to origin" in get_checks(report)["chain"]["detail"]

    def test_unreachable_remote_fails(self, ledger_repo, tmp_path):
        report = anchor.push(
            ledger_repo, str(tmp_path / "missing-remote"), origin=ORIGIN
        )
        assert not report["ok"]
        assert get_checks(report)["remote"]["level"] == "FAIL"


# ── verify ────────────────────────────────────────────────────────────────────

class TestVerify:
    @pytest.fixture
    def anchored(self, ledger_repo, anchor_remote):
        first = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        grow_ledger(ledger_repo, 2)
        second = anchor.push(ledger_repo, str(anchor_remote), origin=ORIGIN)
        return {
            "repo": ledger_repo,
            "remote": str(anchor_remote),
            "ref": second["ref"],
            "commits": [first["commit"], second["commit"]],
        }

    def test_valid_chain_verifies(self, anchored):
        report = anchor.verify(
            anchored["repo"], anchored["remote"], origin=ORIGIN
        )
        assert report["ok"], report["checks"]
        assert report["anchors"] == 2
        assert report["latest"]["commit"] == anchored["commits"][1]
        assert report["latest"]["tree_size"] == 5
        checks = get_checks(report)
        assert checks["linearity"]["level"] == "OK"
        assert checks["freshness"]["level"] == "WARN"  # no --max-age-days

    def test_max_age_days_fresh_and_stale(self, anchored, tmp_path):
        report = anchor.verify(
            anchored["repo"], anchored["remote"], origin=ORIGIN,
            max_age_days=1,
        )
        assert report["ok"]
        assert get_checks(report)["freshness"]["level"] == "OK"

        # Anchor a checkpoint whose (operator-claimed) timestamp is old.
        grow_ledger(anchored["repo"], 1)
        cp = checkpoint.build_checkpoint(anchored["repo"], origin=ORIGIN)
        cp["timestamp"] = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        cpath = tmp_path / "stale.json"
        checkpoint.write_checkpoint(cp, cpath)
        assert anchor.push(
            anchored["repo"], anchored["remote"], checkpoint_path=cpath
        )["ok"]
        report = anchor.verify(
            anchored["repo"], anchored["remote"], origin=ORIGIN,
            max_age_days=7,
        )
        assert not report["ok"]
        assert get_checks(report)["freshness"]["level"] == "FAIL"

    def test_local_ledger_rollback_detected(self, anchored):
        """Truncating the local ledger below an anchored size is caught."""
        ledger_path = ledger_mod.Ledger(anchored["repo"]).path
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        ledger_path.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
        report = anchor.verify(
            anchored["repo"], anchored["remote"], origin=ORIGIN
        )
        assert not report["ok"]
        assert any(
            "behind what was anchored" in c["detail"]
            for c in failed_checks(report)
        )

    def test_local_ledger_fork_detected(self, anchored):
        """Rewriting a ledger entry (same size, different content) is caught."""
        ledger_path = ledger_mod.Ledger(anchored["repo"]).path
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["leaf_hash"] = secrets.token_hex(32)
        lines[0] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = anchor.verify(
            anchored["repo"], anchored["remote"], origin=ORIGIN
        )
        assert not report["ok"]
        assert any(
            "NOT consistent" in c["detail"] for c in failed_checks(report)
        )

    def test_rewritten_anchor_ref_detected(self, anchored):
        """A rewritten anchor ref whose chain no longer strictly grows
        (rollback replay of the first anchor on top) is caught."""
        repo = anchored["repo"]
        remote = anchored["remote"]
        ref = anchored["ref"]
        # The attacker rewrites the remote ref: new tip re-anchors the OLD
        # (size 3) checkpoint on top of the newer (size 5) one.
        old_cp_proc = subprocess.run(
            ["git", "cat-file", "-p",
             f"{anchored['commits'][0]}:checkpoint.json"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo, input=old_cp_proc.stdout,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "mktree"],
            cwd=repo, input=f"100644 blob {blob}\tcheckpoint.json\n",
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        replay = subprocess.run(
            ["git", "-c", "user.name=x", "-c", "user.email=x@example.com",
             "commit-tree", tree, "-p", anchored["commits"][1],
             "-m", "replay"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "push", "-q", "-f", remote, f"{replay}:{ref}"],
            cwd=repo, check=True,
        )
        report = anchor.verify(repo, remote, origin=ORIGIN)
        assert not report["ok"]
        assert any(
            "does not grow past" in c["detail"] or "rewritten" in c["detail"]
            for c in failed_checks(report)
        )

    def test_no_anchors_fails(self, ledger_repo, anchor_remote):
        report = anchor.verify(ledger_repo, str(anchor_remote), origin=ORIGIN)
        assert not report["ok"]
        assert "no anchors found" in get_checks(report)["chain"]["detail"]

    def test_push_builds_on_the_current_remote_tip(self, anchored, tmp_path):
        """anchor push always fetches and extends the remote's CURRENT tip.

        After a remote-side rewind (force-push back to the first anchor),
        the next honest push extends the rewound tip — the size-5 anchor
        silently disappears from the chain. Detecting that loss is the
        witnesses' job (they remember size 5 and refuse a rollback), not
        the anchor pusher's: this test documents the division of labour."""
        repo = anchored["repo"]
        remote = anchored["remote"]
        ref = anchored["ref"]
        subprocess.run(
            ["git", "push", "-q", "-f", remote,
             f"{anchored['commits'][0]}:{ref}"],
            cwd=repo, check=True,
        )
        grow_ledger(repo, 1)
        report = anchor.push(repo, remote, origin=ORIGIN)
        assert report["ok"]
        chain = anchor.fetch_chain(repo, remote, ref)
        assert [link["checkpoint"]["tree_size"] for link in chain] == [3, 6]


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


class TestCli:
    def test_push_and_verify(self, ledger_repo, anchor_remote):
        proc = run_cli(
            [
                "anchor", "push",
                "--remote", str(anchor_remote),
                "--origin", ORIGIN,
            ],
            cwd=ledger_repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: ANCHORED" in proc.stdout

        proc = run_cli(
            [
                "anchor", "verify",
                "--remote", str(anchor_remote),
                "--origin", ORIGIN,
                "--max-age-days", "1",
            ],
            cwd=ledger_repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: VERIFIED" in proc.stdout

    def test_verify_exit_code_on_fork(self, ledger_repo, anchor_remote):
        assert run_cli(
            ["anchor", "push", "--remote", str(anchor_remote),
             "--origin", ORIGIN],
            cwd=ledger_repo,
        ).returncode == 0
        # Fork the local ledger after anchoring.
        ledger_path = ledger_mod.Ledger(ledger_repo).path
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[-1])
        entry["leaf_hash"] = secrets.token_hex(32)
        lines[-1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            ["anchor", "verify", "--remote", str(anchor_remote),
             "--origin", ORIGIN],
            cwd=ledger_repo,
        )
        assert proc.returncode == 1
        assert "Result: FAILED" in proc.stdout
