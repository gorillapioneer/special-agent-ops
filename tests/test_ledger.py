"""Tests for sao.provenance.ledger — Merkle transparency log."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import ledger as L

from conftest import init_git_repo


def synthetic_leaves(n: int) -> list:
    return [L.leaf_hash(bytes([i])) for i in range(n)]


# ── Hash primitives ───────────────────────────────────────────────────────────

class TestPrimitives:
    def test_leaf_hash_is_prefixed_sha256(self):
        assert L.leaf_hash(b"data") == hashlib.sha256(b"\x00data").digest()

    def test_node_hash_is_prefixed_sha256(self):
        left, right = b"L" * 32, b"R" * 32
        assert L.node_hash(left, right) == hashlib.sha256(b"\x01" + left + right).digest()

    def test_leaf_differs_from_node_on_same_bytes(self):
        # Domain separation: a leaf can never be confused with an interior node.
        data = b"\x42" * 64
        assert L.leaf_hash(data) != L.node_hash(data[:32], data[32:])

    def test_empty_tree_root(self):
        assert L.merkle_root([]) == hashlib.sha256(b"").digest()

    def test_single_leaf_root_is_leaf(self):
        leaf = L.leaf_hash(b"only")
        assert L.merkle_root([leaf]) == leaf

    def test_two_leaf_root(self):
        a, b = L.leaf_hash(b"a"), L.leaf_hash(b"b")
        assert L.merkle_root([a, b]) == L.node_hash(a, b)

    def test_rfc6962_unbalanced_split(self):
        # For 3 leaves, k=2: root = node(node(l0,l1), l2)
        l0, l1, l2 = synthetic_leaves(3)
        assert L.merkle_root([l0, l1, l2]) == L.node_hash(L.node_hash(l0, l1), l2)


# ── Inclusion proofs ──────────────────────────────────────────────────────────

class TestInclusion:
    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 13])
    def test_all_leaves_verify(self, n):
        leaves = synthetic_leaves(n)
        root = L.merkle_root(leaves).hex()
        for i in range(n):
            proof = [p.hex() for p in L.merkle_inclusion_path(i, leaves)]
            assert L.verify_inclusion(leaves[i].hex(), i, proof, root, n)

    def test_modified_leaf_fails(self):
        leaves = synthetic_leaves(6)
        root = L.merkle_root(leaves).hex()
        proof = [p.hex() for p in L.merkle_inclusion_path(2, leaves)]
        tampered = L.leaf_hash(b"evil").hex()
        assert not L.verify_inclusion(tampered, 2, proof, root, 6)

    def test_wrong_index_fails(self):
        leaves = synthetic_leaves(6)
        root = L.merkle_root(leaves).hex()
        proof = [p.hex() for p in L.merkle_inclusion_path(2, leaves)]
        assert not L.verify_inclusion(leaves[2].hex(), 3, proof, root, 6)

    def test_wrong_root_fails(self):
        leaves = synthetic_leaves(4)
        proof = [p.hex() for p in L.merkle_inclusion_path(0, leaves)]
        bad_root = L.leaf_hash(b"other").hex()
        assert not L.verify_inclusion(leaves[0].hex(), 0, proof, bad_root, 4)

    def test_out_of_range_index(self):
        leaves = synthetic_leaves(3)
        root = L.merkle_root(leaves).hex()
        assert not L.verify_inclusion(leaves[0].hex(), 5, [], root, 3)
        with pytest.raises(IndexError):
            L.merkle_inclusion_path(3, leaves)

    def test_non_hex_input_fails_gracefully(self):
        assert not L.verify_inclusion("not-hex", 0, [], "also-not-hex", 1)


# ── Consistency proofs ────────────────────────────────────────────────────────

class TestConsistency:
    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 13])
    def test_all_prefixes_consistent(self, n):
        leaves = synthetic_leaves(n)
        new_root = L.merkle_root(leaves).hex()
        for m in range(n + 1):
            old_root = L.merkle_root(leaves[:m]).hex()
            proof = [p.hex() for p in L.merkle_consistency_path(m, leaves)]
            assert L.verify_consistency(m, n, old_root, new_root, proof), (m, n)

    def test_rewritten_history_fails(self):
        # Log observer saw root at size 3; operator then rewrites leaf 1.
        honest = synthetic_leaves(5)
        old_root = L.merkle_root(honest[:3]).hex()
        rewritten = list(honest)
        rewritten[1] = L.leaf_hash(b"rewritten")
        new_root = L.merkle_root(rewritten).hex()
        proof = [p.hex() for p in L.merkle_consistency_path(3, rewritten)]
        assert not L.verify_consistency(3, 5, old_root, new_root, proof)

    def test_same_size_requires_equal_roots(self):
        leaves = synthetic_leaves(4)
        root = L.merkle_root(leaves).hex()
        other = L.merkle_root(synthetic_leaves(3)).hex()
        assert L.verify_consistency(4, 4, root, root, [])
        assert not L.verify_consistency(4, 4, other, root, [])

    def test_old_size_larger_than_new_fails(self):
        leaves = synthetic_leaves(4)
        root = L.merkle_root(leaves).hex()
        assert not L.verify_consistency(5, 4, root, root, [])


# ── Ledger file behaviour ─────────────────────────────────────────────────────

def fake_seal_hash(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


class TestLedgerFile:
    def test_append_creates_jsonl(self, tmp_path: Path):
        ledger = L.Ledger(tmp_path)
        entry = ledger.append("m1", fake_seal_hash("m1"))
        assert ledger.path == tmp_path / "blackbox" / "ledger.jsonl"
        assert ledger.path.exists()
        assert entry["index"] == 0
        assert entry["leaf_hash"] == L.leaf_hash_for_seal(fake_seal_hash("m1"))
        line = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
        assert set(line) == {"index", "mission_id", "leaf_hash", "timestamp"}

    def test_append_is_idempotent_per_mission(self, tmp_path: Path):
        ledger = L.Ledger(tmp_path)
        first = ledger.append("m1", fake_seal_hash("m1"))
        again = ledger.append("m1", fake_seal_hash("m1"))
        assert first == again
        assert ledger.size() == 1

    def test_indexes_are_contiguous(self, tmp_path: Path):
        ledger = L.Ledger(tmp_path)
        for i in range(5):
            ledger.append(f"m{i}", fake_seal_hash(f"m{i}"))
        assert [e["index"] for e in ledger.entries()] == list(range(5))

    def test_root_changes_on_append(self, tmp_path: Path):
        ledger = L.Ledger(tmp_path)
        ledger.append("m0", fake_seal_hash("m0"))
        root_1 = ledger.root()
        ledger.append("m1", fake_seal_hash("m1"))
        root_2 = ledger.root()
        assert root_1["tree_size"] == 1 and root_2["tree_size"] == 2
        assert root_1["root_hash"] != root_2["root_hash"]

    def test_append_only_evolution_is_provable(self, tmp_path: Path):
        ledger = L.Ledger(tmp_path)
        for i in range(3):
            ledger.append(f"m{i}", fake_seal_hash(f"m{i}"))
        old = ledger.root()
        for i in range(3, 7):
            ledger.append(f"m{i}", fake_seal_hash(f"m{i}"))
        new = ledger.root()
        proof = ledger.consistency_proof(old["tree_size"])
        assert L.verify_consistency(
            old["tree_size"], new["tree_size"],
            old["root_hash"], new["root_hash"], proof,
        )

    def test_inclusion_proof_from_stored_log(self, tmp_path: Path):
        ledger = L.Ledger(tmp_path)
        for i in range(6):
            ledger.append(f"m{i}", fake_seal_hash(f"m{i}"))
        info = ledger.root()
        for entry in ledger.entries():
            proof = ledger.inclusion_proof(entry["index"])
            assert L.verify_inclusion(
                entry["leaf_hash"], entry["index"], proof,
                info["root_hash"], info["tree_size"],
            )

    def test_empty_ledger_root(self, tmp_path: Path):
        info = L.Ledger(tmp_path).root()
        assert info["tree_size"] == 0
        assert info["root_hash"] == hashlib.sha256(b"").hexdigest()


# ── Full-log verification against real sessions ──────────────────────────────

class TestVerifyLog:
    def test_verify_log_ok(self, provenance_repo):
        ledger = L.Ledger(provenance_repo["repo"])
        result = ledger.verify_log()
        assert result["ok"], result["problems"]
        assert result["tree_size"] == 2
        assert all(e["leaf_ok"] for e in result["entries"])
        assert all(e["inclusion_ok"] for e in result["entries"])

    def test_tampered_ledger_entry_detected(self, copy_provenance_repo):
        repo = copy_provenance_repo()["repo"]
        ledger = L.Ledger(repo)
        entries = ledger.entries()
        entries[0]["leaf_hash"] = L.leaf_hash(b"forged").hex()
        ledger.path.write_text(
            "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
        )
        result = L.Ledger(repo).verify_log()
        assert not result["ok"]
        assert any("leaf hash mismatch" in p for p in result["problems"])

    def test_tampered_session_seal_detected(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]
        seal_path = repo / "blackbox" / "sessions" / mission_id / "seal.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        seal["manifest_sha256"] = fake_seal_hash("forged manifest")
        seal_path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        result = L.Ledger(repo).verify_log()
        assert not result["ok"]
        assert any(mission_id in p for p in result["problems"])


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


class TestLedgerCli:
    def test_root_prints_json(self, provenance_repo):
        proc = run_cli(["ledger", "root"], cwd=provenance_repo["repo"])
        assert proc.returncode == 0, proc.stderr
        info = json.loads(proc.stdout)
        assert info["tree_size"] == 2
        assert len(info["root_hash"]) == 64

    def test_root_qr_image(self, provenance_repo, tmp_path: Path):
        pytest.importorskip("qrcode", reason="qrcode[pil] required for QR output")
        qr_path = tmp_path / "ledger_root.png"
        proc = run_cli(
            ["ledger", "root", "--qr", str(qr_path)],
            cwd=provenance_repo["repo"],
        )
        assert proc.returncode == 0, proc.stderr
        assert qr_path.exists() and qr_path.stat().st_size > 0

    def test_verify_passes(self, provenance_repo):
        proc = run_cli(["ledger", "verify"], cwd=provenance_repo["repo"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: VERIFIED" in proc.stdout

    def test_verify_fails_on_tamper(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        ledger = L.Ledger(repo)
        entries = ledger.entries()
        entries[1]["leaf_hash"] = L.leaf_hash(b"evil").hex()
        ledger.path.write_text(
            "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
        )
        proc = run_cli(["ledger", "verify"], cwd=repo)
        assert proc.returncode == 1
        assert "Result: FAILED" in proc.stdout

    def test_root_on_empty_ledger(self, git_repo):
        proc = run_cli(["ledger", "root"], cwd=git_repo)
        assert proc.returncode == 0
        assert json.loads(proc.stdout)["tree_size"] == 0
