"""Tests for sao.provenance.witness — the independent, stateful cosigner —
and the verify-pr independently-witnessed tier it enables."""

import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import (
    anchor, checkpoint, ci_issue, ledger as ledger_mod, verify_pr, witness,
)

from conftest import init_git_repo
from test_checkpoint import ORIGIN, make_hmac_key, make_ledger_repo, make_ssh_key


@pytest.fixture
def ledger_repo(tmp_path: Path) -> Path:
    return make_ledger_repo(tmp_path)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "witness-state"
    d.mkdir()
    return d


@pytest.fixture
def witness_signer(tmp_path: Path):
    key = make_hmac_key(tmp_path, "witness1.key")
    signer = checkpoint.make_cosign_signer("hmac", key)
    return {"key": key, "signer": signer, "name": "witness1"}


def emit_checkpoint(repo: Path, path: Path, bundle_from=None) -> dict:
    cp = checkpoint.build_checkpoint(
        repo, origin=ORIGIN, bundle_proof_from=bundle_from
    )
    checkpoint.write_checkpoint(cp, path)
    return cp


def get_checks(report: dict) -> dict:
    return {c["name"]: c for c in report["checks"]}


def grow_ledger(repo: Path, n: int = 1) -> None:
    ledger = ledger_mod.Ledger(repo)
    for _ in range(n):
        ledger.append(f"mission_{secrets.token_hex(4)}", secrets.token_hex(32))


# ── TOFU + happy growth ───────────────────────────────────────────────────────

class TestCosignHappyPath:
    def test_tofu_records_and_cosigns(
        self, ledger_repo, state_dir, witness_signer, tmp_path
    ):
        cpath = tmp_path / "checkpoint.json"
        cp = emit_checkpoint(ledger_repo, cpath)
        report = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )
        assert report["ok"], report["checks"]
        assert report["tofu"]
        assert report["action"] == "cosigned"
        assert "TRUST-ON-FIRST-USE" in get_checks(report)["state"]["detail"]
        # Cosignature landed in the document.
        cosigned = checkpoint.load_checkpoint(cpath)
        assert cosigned["cosignatures"][0]["witness"] == "witness1"
        # State was recorded.
        state = witness.load_state(state_dir, ORIGIN)
        assert state["origin"] == ORIGIN
        assert state["tree_size"] == cp["tree_size"]
        assert state["root_hash"] == cp["root_hash"]

    def test_growth_via_bundled_proof(
        self, ledger_repo, state_dir, witness_signer, tmp_path
    ):
        cpath = tmp_path / "checkpoint.json"
        emit_checkpoint(ledger_repo, cpath)
        assert witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )["ok"]
        old_size = ledger_mod.Ledger(ledger_repo).size()

        grow_ledger(ledger_repo, 2)
        emit_checkpoint(ledger_repo, cpath, bundle_from=old_size)
        report = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )
        assert report["ok"], report["checks"]
        assert not report["tofu"]
        assert "bundled proof" in get_checks(report)["consistency"]["detail"]
        assert witness.load_state(state_dir, ORIGIN)["tree_size"] == old_size + 2

    def test_growth_via_ledger_repo_clone(
        self, ledger_repo, state_dir, witness_signer, tmp_path
    ):
        cpath = tmp_path / "checkpoint.json"
        emit_checkpoint(ledger_repo, cpath)
        assert witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )["ok"]

        grow_ledger(ledger_repo, 1)
        emit_checkpoint(ledger_repo, cpath)  # no bundled proof this time
        clone = tmp_path / "witness-clone"
        clone.mkdir()
        shutil.copytree(ledger_repo / "blackbox", clone / "blackbox")
        report = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"],
            ledger_repo=clone,
        )
        assert report["ok"], report["checks"]
        assert "ledger clone" in get_checks(report)["consistency"]["detail"]

    def test_same_checkpoint_recosign_ok(
        self, ledger_repo, state_dir, witness_signer, tmp_path
    ):
        cpath = tmp_path / "checkpoint.json"
        emit_checkpoint(ledger_repo, cpath)
        assert witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )["ok"]
        report = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )
        assert report["ok"]
        assert len(checkpoint.load_checkpoint(cpath)["cosignatures"]) == 1

    def test_operator_signature_verified_when_pinned(
        self, ledger_repo, state_dir, witness_signer, tmp_path
    ):
        op_key = make_hmac_key(tmp_path, "op.key")
        cpath = tmp_path / "checkpoint.json"
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", op_key)
        )
        checkpoint.write_checkpoint(cp, cpath)
        report = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"],
            operator_hmac_key_file=op_key,
        )
        assert report["ok"]
        assert get_checks(report)["operator-signature"]["level"] == "OK"
        # Wrong pinned operator key → refusal.
        other = make_hmac_key(tmp_path, "not-op.key")
        report2 = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"],
            operator_hmac_key_file=other,
        )
        assert not report2["ok"]


# ── Refusals ──────────────────────────────────────────────────────────────────

class TestRefusals:
    @pytest.fixture
    def remembered(self, ledger_repo, state_dir, witness_signer, tmp_path):
        """Witness that has already cosigned the current checkpoint."""
        cpath = tmp_path / "checkpoint.json"
        cp = emit_checkpoint(ledger_repo, cpath)
        assert witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )["ok"]
        return {
            "repo": ledger_repo,
            "state_dir": state_dir,
            "signer": witness_signer,
            "cpath": cpath,
            "cp": cp,
            "state_before": witness.load_state(state_dir, ORIGIN),
        }

    def assert_refused_and_state_untouched(self, remembered, report, path):
        assert not report["ok"]
        assert report["action"] == "refused"
        assert any(
            witness.REFUSAL_MARKER in c["detail"]
            for c in report["checks"] if c["level"] == "FAIL"
        )
        # State must not move on a refusal.
        state = witness.load_state(remembered["state_dir"], ORIGIN)
        assert state == remembered["state_before"]
        # The refused checkpoint gained no cosignature.
        assert checkpoint.load_checkpoint(path).get("cosignatures", []) == []

    def test_rollback_refused(self, remembered, tmp_path):
        ledger = ledger_mod.Ledger(remembered["repo"])
        smaller = dict(remembered["cp"])
        smaller.update(
            tree_size=1, root_hash=ledger.root_at(1), cosignatures=[]
        )
        path = tmp_path / "rollback.json"
        checkpoint.write_checkpoint(smaller, path)
        report = witness.cosign(
            path, remembered["state_dir"], remembered["signer"]["name"],
            remembered["signer"]["signer"],
        )
        self.assert_refused_and_state_untouched(remembered, report, path)
        assert "ROLLBACK REFUSED" in get_checks(report)["consistency"]["detail"]

    def test_root_swap_same_size_refused(self, remembered, tmp_path):
        forked = dict(remembered["cp"])
        forked.update(root_hash="e" * 64, cosignatures=[])
        path = tmp_path / "fork.json"
        checkpoint.write_checkpoint(forked, path)
        report = witness.cosign(
            path, remembered["state_dir"], remembered["signer"]["name"],
            remembered["signer"]["signer"],
        )
        self.assert_refused_and_state_untouched(remembered, report, path)
        assert "EQUIVOCATION REFUSED" in get_checks(report)["consistency"]["detail"]

    def test_forked_growth_refused_via_bundled_proof(self, remembered, tmp_path):
        """A bigger tree whose bundled proof does not link the remembered
        root is a fork, not growth."""
        old_size = remembered["cp"]["tree_size"]
        grow_ledger(remembered["repo"], 1)
        cp = checkpoint.build_checkpoint(
            remembered["repo"], origin=ORIGIN, bundle_proof_from=old_size
        )
        cp["root_hash"] = "d" * 64  # forked head
        path = tmp_path / "forked-growth.json"
        checkpoint.write_checkpoint(cp, path)
        report = witness.cosign(
            path, remembered["state_dir"], remembered["signer"]["name"],
            remembered["signer"]["signer"],
        )
        self.assert_refused_and_state_untouched(remembered, report, path)

    def test_forked_growth_refused_via_ledger_clone(self, remembered, tmp_path):
        old_size = remembered["cp"]["tree_size"]
        # The witness's clone: honest history, grown by one.
        clone = tmp_path / "clone"
        clone.mkdir()
        shutil.copytree(remembered["repo"] / "blackbox", clone / "blackbox")
        grow_ledger(clone, 1)
        # The operator presents a DIFFERENT grown ledger (fork).
        cp = checkpoint.build_checkpoint(remembered["repo"], origin=ORIGIN)
        cp["tree_size"] = old_size + 1
        cp["root_hash"] = "c" * 64
        path = tmp_path / "forked.json"
        checkpoint.write_checkpoint(cp, path)
        report = witness.cosign(
            path, remembered["state_dir"], remembered["signer"]["name"],
            remembered["signer"]["signer"], ledger_repo=clone,
        )
        self.assert_refused_and_state_untouched(remembered, report, path)

    def test_growth_without_any_proof_path_refused(self, remembered, tmp_path):
        grow_ledger(remembered["repo"], 1)
        cp = checkpoint.build_checkpoint(remembered["repo"], origin=ORIGIN)
        path = tmp_path / "no-proof.json"
        checkpoint.write_checkpoint(cp, path)
        report = witness.cosign(
            path, remembered["state_dir"], remembered["signer"]["name"],
            remembered["signer"]["signer"],
        )
        self.assert_refused_and_state_untouched(remembered, report, path)
        assert "refusing to cosign blind" in get_checks(report)["consistency"]["detail"]

    def test_origin_mismatch_refused(self, remembered, tmp_path):
        """A state file whose recorded origin differs from the checkpoint's
        (slug collision / tampered state) is a refusal, not a TOFU."""
        state_path = witness.state_path(remembered["state_dir"], ORIGIN)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["origin"] = "someone.else/entirely"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        remembered["state_before"] = witness.load_state(
            remembered["state_dir"], ORIGIN
        )
        path = tmp_path / "checkpoint2.json"
        emit_checkpoint(remembered["repo"], path)
        report = witness.cosign(
            path, remembered["state_dir"], remembered["signer"]["name"],
            remembered["signer"]["signer"],
        )
        self.assert_refused_and_state_untouched(remembered, report, path)
        assert get_checks(report)["state"]["level"] == "FAIL"

    def test_tofu_against_mismatching_clone_refused(
        self, ledger_repo, state_dir, witness_signer, tmp_path
    ):
        cpath = tmp_path / "checkpoint.json"
        cp = emit_checkpoint(ledger_repo, cpath)
        cp["root_hash"] = "b" * 64
        checkpoint.write_checkpoint(cp, cpath)
        report = witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"],
            ledger_repo=ledger_repo,
        )
        assert not report["ok"]
        assert witness.load_state(state_dir, ORIGIN) is None


# ── State listing ─────────────────────────────────────────────────────────────

class TestState:
    def test_list_states(self, ledger_repo, state_dir, witness_signer, tmp_path):
        assert witness.list_states(state_dir) == []
        cpath = tmp_path / "checkpoint.json"
        emit_checkpoint(ledger_repo, cpath)
        witness.cosign(
            cpath, state_dir, witness_signer["name"], witness_signer["signer"]
        )
        states = witness.list_states(state_dir)
        assert len(states) == 1
        assert states[0]["origin"] == ORIGIN
        assert states[0]["cosign_count"] == 1
        text = witness.render_states(states, state_dir)
        assert ORIGIN in text

    def test_list_states_missing_dir(self, tmp_path):
        assert witness.list_states(tmp_path / "nope") == []


# ── verify-pr: the independently-witnessed tier ───────────────────────────────

CI_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "acme/widgets",
    "GITHUB_RUN_ID": "515151",
    "GITHUB_ACTOR": "policy-bot",
}


@pytest.fixture
def ci_env(monkeypatch):
    for key, value in CI_ENV.items():
        monkeypatch.setenv(key, value)


class TestVerifyPrWitnessedTier:
    @pytest.fixture
    def pipeline(self, copy_provenance_repo, tmp_path, ci_env):
        """Full pipeline: attested missions -> ci-issue -> checkpoint ->
        witness cosign. Returns everything a verify-pr call needs."""
        copied = copy_provenance_repo()
        repo = copied["repo"]
        commit = copied["mission_b"]["attestation"]["note_commit"]

        ci_key = make_hmac_key(tmp_path, "ci.key")
        issued = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=ci_key)
        assert issued["ok"], issued["checks"]

        op_key = make_hmac_key(tmp_path, "operator.key")
        cp = checkpoint.build_checkpoint(repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", op_key)
        )
        cpath = tmp_path / "checkpoint.json"
        checkpoint.write_checkpoint(cp, cpath)

        w_key = make_hmac_key(tmp_path, "witness1.key")
        state = tmp_path / "wstate"
        cosigned = witness.cosign(
            cpath, state, "witness1",
            checkpoint.make_cosign_signer("hmac", w_key),
        )
        assert cosigned["ok"], cosigned["checks"]

        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(
            f"witness1 hmac-sha256 {w_key.read_text(encoding='utf-8').strip()}\n",
            encoding="utf-8",
        )
        return {
            "repo": repo,
            "commit": commit,
            "ci_key": ci_key,
            "checkpoint_path": cpath,
            "witness_keys": pinned,
            "tmp_path": tmp_path,
        }

    def test_full_pipeline_reaches_top_tier(self, pipeline):
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pipeline["witness_keys"],
            require_witnesses=1,
            checkpoint_path=pipeline["checkpoint_path"],
        )
        assert report["ok"], report["commits"][0]["checks"]
        entry = report["commits"][0]
        assert entry["tier"] == "independently-witnessed"
        checks = get_checks(entry)
        assert checks["ci-attestation"]["level"] == "OK"
        assert checks["witnessed-checkpoint"]["level"] == "OK"
        assert checks["tier"]["level"] == "OK"

    def test_fails_without_witness_material(self, pipeline):
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
        )
        assert not report["ok"]
        entry = report["commits"][0]
        assert entry["tier"] == "ci-verified"
        checks = get_checks(entry)
        assert checks["witnessed-checkpoint"]["level"] == "SKIP"
        assert checks["tier"]["level"] == "FAIL"

    def test_fails_with_uncosigned_checkpoint(self, pipeline):
        cp = checkpoint.load_checkpoint(pipeline["checkpoint_path"])
        cp["cosignatures"] = []
        bare = pipeline["tmp_path"] / "bare-checkpoint.json"
        checkpoint.write_checkpoint(cp, bare)
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pipeline["witness_keys"],
            require_witnesses=1,
            checkpoint_path=bare,
        )
        assert not report["ok"]
        checks = get_checks(report["commits"][0])
        assert checks["witnessed-checkpoint"]["level"] == "FAIL"

    def test_fails_with_insufficient_cosignatures(self, pipeline):
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pipeline["witness_keys"],
            require_witnesses=2,
            checkpoint_path=pipeline["checkpoint_path"],
        )
        assert not report["ok"]
        checks = get_checks(report["commits"][0])
        assert checks["witnessed-checkpoint"]["level"] == "FAIL"
        assert "required 2" in checks["witnessed-checkpoint"]["detail"]

    def test_fails_with_unpinned_witness(self, pipeline):
        rogue_key = make_hmac_key(pipeline["tmp_path"], "rogue.key")
        pinned = pipeline["tmp_path"] / "other-witnesses.txt"
        pinned.write_text(
            "someone-else hmac-sha256 "
            f"{rogue_key.read_text(encoding='utf-8').strip()}\n",
            encoding="utf-8",
        )
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pinned,
            require_witnesses=1,
            checkpoint_path=pipeline["checkpoint_path"],
        )
        assert not report["ok"]
        assert get_checks(report["commits"][0])["witnessed-checkpoint"]["level"] == "FAIL"

    def test_fails_when_leaf_not_covered(self, pipeline, tmp_path):
        """A stale witnessed checkpoint (tree_size <= the commit's leaf
        index) does not cover the commit."""
        ledger = ledger_mod.Ledger(pipeline["repo"])
        stale = {
            "version": checkpoint.CHECKPOINT_VERSION,
            "origin": ORIGIN,
            "tree_size": 1,
            "root_hash": ledger.root_at(1),
            "timestamp": "2026-01-01T00:00:00+00:00",
            "signature": None,
            "cosignatures": [],
        }
        w_key = make_hmac_key(tmp_path, "w-stale.key")
        checkpoint.add_cosignature(
            stale, "witness1", checkpoint.make_cosign_signer("hmac", w_key)
        )
        stale_path = tmp_path / "stale.json"
        checkpoint.write_checkpoint(stale, stale_path)
        pinned = tmp_path / "stale-witnesses.txt"
        pinned.write_text(
            f"witness1 hmac-sha256 {w_key.read_text(encoding='utf-8').strip()}\n",
            encoding="utf-8",
        )
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pinned,
            require_witnesses=1,
            checkpoint_path=stale_path,
        )
        assert not report["ok"]
        checks = get_checks(report["commits"][0])
        assert checks["witnessed-checkpoint"]["level"] == "FAIL"
        assert "not covered" in checks["witnessed-checkpoint"]["detail"]

    def test_checkpoint_from_anchors_remote(self, pipeline, tmp_path):
        bare = tmp_path / "anchors.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        pushed = anchor.push(
            pipeline["repo"], str(bare),
            checkpoint_path=pipeline["checkpoint_path"],
        )
        assert pushed["ok"], pushed["checks"]
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pipeline["witness_keys"],
            require_witnesses=1,
            anchors_remote=str(bare),
            anchors_ref=pushed["ref"],
        )
        assert report["ok"], report["commits"][0]["checks"]
        assert report["commits"][0]["tier"] == "independently-witnessed"

    def test_top_tier_in_reports(self, pipeline):
        report = verify_pr.verify_pr(
            pipeline["repo"],
            base=f"{pipeline['commit']}^", head=pipeline["commit"],
            min_tier="independently-witnessed",
            ci_hmac_key_file=pipeline["ci_key"],
            witness_keys=pipeline["witness_keys"],
            require_witnesses=1,
            checkpoint_path=pipeline["checkpoint_path"],
        )
        text = verify_pr.render_text(report)
        assert "Min Tier:   independently-witnessed" in text
        assert "[independently-witnessed]" in text
        md = verify_pr.render_markdown(report)
        assert "**Minimum tier:** independently-witnessed" in md
        assert "| independently-witnessed |" in md


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


class TestCli:
    def test_cosign_state_and_refusal(
        self, ledger_repo, state_dir, tmp_path
    ):
        w_key = make_hmac_key(tmp_path, "w1.key")
        cpath = tmp_path / "checkpoint.json"
        emit_checkpoint(ledger_repo, cpath)

        proc = run_cli(
            [
                "witness", "cosign",
                "--checkpoint", str(cpath),
                "--state-dir", str(state_dir),
                "--name", "w1",
                "--signer", "hmac", "--key-file", str(w_key),
            ],
            cwd=tmp_path,  # the witness does NOT run inside the repo
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "trust-on-first-use" in proc.stdout.lower()

        proc = run_cli(
            ["witness", "state", "--state-dir", str(state_dir)],
            cwd=tmp_path,
        )
        assert proc.returncode == 0
        assert ORIGIN in proc.stdout

        # Rollback checkpoint → loud refusal, exit 1.
        ledger = ledger_mod.Ledger(ledger_repo)
        rollback = checkpoint.load_checkpoint(cpath)
        rollback.update(
            tree_size=1, root_hash=ledger.root_at(1), cosignatures=[]
        )
        rpath = tmp_path / "rollback.json"
        checkpoint.write_checkpoint(rollback, rpath)
        proc = run_cli(
            [
                "witness", "cosign",
                "--checkpoint", str(rpath),
                "--state-dir", str(state_dir),
                "--name", "w1",
                "--signer", "hmac", "--key-file", str(w_key),
            ],
            cwd=tmp_path,
        )
        assert proc.returncode == 1
        assert "REFUSED" in proc.stdout
        assert "possible equivocation/fork" in proc.stdout

    def test_ssh_witness_cosign_cli(self, ledger_repo, state_dir, tmp_path):
        key = make_ssh_key(tmp_path, "w1_ed25519")
        cpath = tmp_path / "checkpoint.json"
        emit_checkpoint(ledger_repo, cpath)
        proc = run_cli(
            [
                "witness", "cosign",
                "--checkpoint", str(cpath),
                "--state-dir", str(state_dir),
                "--name", "w1",
                "--signer", "ssh", "--key-file", str(key),
            ],
            cwd=tmp_path,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        pub = Path(str(key) + ".pub").read_text(encoding="utf-8").strip()
        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(f"w1 {pub}\n", encoding="utf-8")
        proc = run_cli(
            [
                "checkpoint", "verify",
                "--checkpoint", str(cpath),
                "--require-witnesses", "1",
                "--witness-keys", str(pinned),
            ],
            cwd=ledger_repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_verify_pr_min_tier_cli(self, copy_provenance_repo, tmp_path):
        """CLI pass + fail for --min-tier independently-witnessed."""
        copied = copy_provenance_repo()
        repo = copied["repo"]
        commit = copied["mission_b"]["attestation"]["note_commit"]

        ci_key = make_hmac_key(tmp_path, "ci.key")
        # Force CI identity via env for the subprocess.
        import os

        env = dict(os.environ)
        env.update(CI_ENV)
        proc = subprocess.run(
            [
                sys.executable, "-m", "sao.cli",
                "ci-issue", "--commit", commit,
                "--signer", "hmac", "--key-file", str(ci_key),
            ],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
            env=env,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        cpath = tmp_path / "checkpoint.json"
        proc = run_cli(
            ["checkpoint", "emit", "--origin", ORIGIN, "--out", str(cpath)],
            cwd=repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        w_key = make_hmac_key(tmp_path, "w1.key")
        state = tmp_path / "wstate"
        proc = run_cli(
            [
                "witness", "cosign",
                "--checkpoint", str(cpath),
                "--state-dir", str(state),
                "--name", "w1",
                "--signer", "hmac", "--key-file", str(w_key),
            ],
            cwd=tmp_path,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(
            f"w1 hmac-sha256 {w_key.read_text(encoding='utf-8').strip()}\n",
            encoding="utf-8",
        )
        proc = run_cli(
            [
                "verify-pr", "--base", f"{commit}^", "--head", commit,
                "--min-tier", "independently-witnessed",
                "--ci-hmac-key-file", str(ci_key),
                "--witness-keys", str(pinned),
                "--require-witnesses", "1",
                "--checkpoint", str(cpath),
            ],
            cwd=repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: PASS" in proc.stdout
        assert "[independently-witnessed]" in proc.stdout

        # Without the witnessed checkpoint the same gate fails.
        proc = run_cli(
            [
                "verify-pr", "--base", f"{commit}^", "--head", commit,
                "--min-tier", "independently-witnessed",
                "--ci-hmac-key-file", str(ci_key),
            ],
            cwd=repo,
        )
        assert proc.returncode == 1
        assert "Result: FAIL" in proc.stdout
