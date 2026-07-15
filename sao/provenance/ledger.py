"""
ledger.py — append-only Merkle transparency log for mission seals.

Certificate-Transparency style (RFC 6962 / RFC 9162 hashing):

    leaf hash     = SHA256(0x00 || leaf_data)
    interior hash = SHA256(0x01 || left || right)

The log lives at blackbox/ledger.jsonl (repo-root relative, same convention
as blackbox/sessions/).  One JSON object per line:

    {"index": n, "mission_id": "...", "leaf_hash": "<sha256-hex>",
     "timestamp": "<iso8601>"}

The leaf material for a mission is its seal's ``manifest_sha256`` (from the
session's seal.json), encoded as ASCII hex.  Anyone holding the log can:

  * recompute the Merkle Tree Hash (``root``),
  * prove a mission is included (``inclusion_proof`` / ``verify_inclusion``),
  * prove the log is append-only between two sizes
    (``consistency_proof`` / ``verify_consistency``).

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LEDGER_FILENAME = "ledger.jsonl"

_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


# ── Hash primitives ───────────────────────────────────────────────────────────

def leaf_hash(data: bytes) -> bytes:
    """RFC 6962 leaf hash: SHA256(0x00 || data)."""
    return hashlib.sha256(_LEAF_PREFIX + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """RFC 6962 interior node hash: SHA256(0x01 || left || right)."""
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def leaf_hash_for_seal(manifest_sha256: str) -> str:
    """Return the hex leaf hash for a mission seal's manifest_sha256."""
    return leaf_hash(manifest_sha256.encode("ascii")).hex()


def _largest_power_of_two_lt(n: int) -> int:
    """Largest power of two strictly less than n (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def merkle_root(leaves: list) -> bytes:
    """Merkle Tree Hash over *leaves* (list of leaf-hash bytes)."""
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _largest_power_of_two_lt(n)
    return node_hash(merkle_root(leaves[:k]), merkle_root(leaves[k:]))


def merkle_inclusion_path(index: int, leaves: list) -> list:
    """RFC 6962 PATH(m, D[n]) — audit path for leaf *index* (list of bytes)."""
    n = len(leaves)
    if index >= n:
        raise IndexError(f"leaf index {index} out of range for tree size {n}")
    if n == 1:
        return []
    k = _largest_power_of_two_lt(n)
    if index < k:
        return merkle_inclusion_path(index, leaves[:k]) + [merkle_root(leaves[k:])]
    return merkle_inclusion_path(index - k, leaves[k:]) + [merkle_root(leaves[:k])]


def _subproof(m: int, leaves: list, complete: bool) -> list:
    """RFC 6962 SUBPROOF(m, D[n], b)."""
    n = len(leaves)
    if m == n:
        return [] if complete else [merkle_root(leaves)]
    k = _largest_power_of_two_lt(n)
    if m <= k:
        return _subproof(m, leaves[:k], complete) + [merkle_root(leaves[k:])]
    return _subproof(m - k, leaves[k:], False) + [merkle_root(leaves[:k])]


def merkle_consistency_path(old_size: int, leaves: list) -> list:
    """RFC 6962 PROOF(m, D[n]) — consistency path from *old_size* to len(leaves)."""
    n = len(leaves)
    if old_size < 0 or old_size > n:
        raise ValueError(f"old_size {old_size} out of range for tree size {n}")
    if old_size == 0 or old_size == n:
        return []
    return _subproof(old_size, leaves, True)


# ── Proof verification (RFC 9162 algorithms, no leaves required) ─────────────

def verify_inclusion(
    leaf_hash_hex: str,
    index: int,
    proof: list,
    root_hex: str,
    tree_size: int,
) -> bool:
    """Verify an inclusion proof (hex leaf hash, hex proof nodes, hex root)."""
    if index < 0 or index >= tree_size:
        return False
    try:
        r = bytes.fromhex(leaf_hash_hex)
        path = [bytes.fromhex(p) for p in proof]
        root = bytes.fromhex(root_hex)
    except ValueError:
        return False

    fn, sn = index, tree_size - 1
    for p in path:
        if sn == 0:
            return False
        if fn % 2 == 1 or fn == sn:
            r = node_hash(p, r)
            if fn % 2 == 0:
                while fn % 2 == 0 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root


def verify_consistency(
    old_size: int,
    new_size: int,
    old_root_hex: str,
    new_root_hex: str,
    proof: list,
) -> bool:
    """Verify a consistency proof between two tree sizes (hex inputs)."""
    if old_size < 0 or old_size > new_size:
        return False
    try:
        old_root = bytes.fromhex(old_root_hex)
        new_root = bytes.fromhex(new_root_hex)
        path = [bytes.fromhex(p) for p in proof]
    except ValueError:
        return False

    if old_size == new_size:
        return not path and old_root == new_root
    if old_size == 0:
        # Empty tree is consistent with anything; nothing to check beyond size.
        return not path

    # If old_size is an exact power of two, the old root is implicit.
    if old_size & (old_size - 1) == 0:
        path = [old_root] + path
    if not path:
        return False

    fn, sn = old_size - 1, new_size - 1
    while fn % 2 == 1:
        fn >>= 1
        sn >>= 1

    fr = sr = path[0]
    for p in path[1:]:
        if sn == 0:
            return False
        if fn % 2 == 1 or fn == sn:
            fr = node_hash(p, fr)
            sr = node_hash(p, sr)
            if fn % 2 == 0:
                while fn % 2 == 0 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = node_hash(sr, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == old_root and sr == new_root


# ── Ledger file ───────────────────────────────────────────────────────────────

class Ledger:
    """Append-only Merkle log stored at <repo>/blackbox/ledger.jsonl."""

    def __init__(self, repo_path=None):
        base = Path(repo_path) if repo_path is not None else Path.cwd()
        self.repo_path = base
        self.path = base / "blackbox" / LEDGER_FILENAME

    # ── Reading ──────────────────────────────────────────────────────────────

    def entries(self) -> list:
        """Return all ledger entries (list of dicts), in log order."""
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def size(self) -> int:
        return len(self.entries())

    def find(self, mission_id: str):
        """Return the ledger entry for *mission_id*, or None."""
        for entry in self.entries():
            if entry.get("mission_id") == mission_id:
                return entry
        return None

    def _leaves(self, entries=None) -> list:
        if entries is None:
            entries = self.entries()
        return [bytes.fromhex(e["leaf_hash"]) for e in entries]

    # ── Writing ──────────────────────────────────────────────────────────────

    def append(self, mission_id: str, seal_manifest_sha256: str) -> dict:
        """Append one entry for *mission_id*; idempotent per mission_id.

        The leaf material is the seal's manifest_sha256 hex string.
        Returns the entry (existing entry if the mission is already logged).
        """
        existing = self.find(mission_id)
        if existing is not None:
            return existing

        entry = {
            "index": self.size(),
            "mission_id": mission_id,
            "leaf_hash": leaf_hash_for_seal(seal_manifest_sha256),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        return entry

    # ── Proofs over the stored log ───────────────────────────────────────────

    def root(self) -> dict:
        """Return {"tree_size": n, "root_hash": hex} over all entries."""
        leaves = self._leaves()
        return {
            "tree_size": len(leaves),
            "root_hash": merkle_root(leaves).hex(),
        }

    def inclusion_proof(self, index: int) -> list:
        """Return the audit path (list of hex node hashes) for leaf *index*."""
        return [p.hex() for p in merkle_inclusion_path(index, self._leaves())]

    def consistency_proof(self, old_size: int, new_size=None) -> list:
        """Return the consistency path (hex) from *old_size* to *new_size*."""
        leaves = self._leaves()
        if new_size is None:
            new_size = len(leaves)
        if new_size > len(leaves):
            raise ValueError(f"new_size {new_size} exceeds log size {len(leaves)}")
        return [p.hex() for p in merkle_consistency_path(old_size, leaves[:new_size])]

    def root_at(self, size: int) -> str:
        """Return the hex root of the first *size* entries."""
        leaves = self._leaves()
        if size > len(leaves):
            raise ValueError(f"size {size} exceeds log size {len(leaves)}")
        return merkle_root(leaves[:size]).hex()

    # ── Full log verification ────────────────────────────────────────────────

    def verify_log(self) -> dict:
        """Re-verify the whole ledger.

        Checks, per entry:
          * indexes are contiguous from 0,
          * where the mission's session folder (and seal.json) still exists,
            the leaf hash recomputed from the seal matches the logged one,
          * the inclusion proof for the entry verifies against the current root.

        Returns {"ok": bool, "tree_size": int, "root_hash": hex,
                 "entries": [per-entry dicts], "problems": [str]}.
        """
        entries = self.entries()
        leaves = self._leaves(entries)
        root_hex = merkle_root(leaves).hex()
        tree_size = len(leaves)
        sessions_root = self.repo_path / "blackbox" / "sessions"

        problems = []
        results = []
        for pos, entry in enumerate(entries):
            detail = {
                "index": entry.get("index"),
                "mission_id": entry.get("mission_id"),
                "index_ok": entry.get("index") == pos,
                "leaf_recomputed": None,
                "leaf_ok": None,
                "inclusion_ok": False,
            }
            if not detail["index_ok"]:
                problems.append(
                    f"entry at position {pos} has index {entry.get('index')}"
                )

            seal_path = sessions_root / str(entry.get("mission_id", "")) / "seal.json"
            if seal_path.exists():
                seal = json.loads(seal_path.read_text(encoding="utf-8"))
                recomputed = leaf_hash_for_seal(seal.get("manifest_sha256", ""))
                detail["leaf_recomputed"] = True
                detail["leaf_ok"] = recomputed == entry.get("leaf_hash")
                if not detail["leaf_ok"]:
                    problems.append(
                        f"leaf hash mismatch for {entry.get('mission_id')} "
                        f"(session seal does not match ledger)"
                    )
            else:
                detail["leaf_recomputed"] = False  # session gone; leaf kept as-is

            proof = [p.hex() for p in merkle_inclusion_path(pos, leaves)]
            detail["inclusion_ok"] = verify_inclusion(
                entry.get("leaf_hash", ""), pos, proof, root_hex, tree_size
            )
            if not detail["inclusion_ok"]:
                problems.append(
                    f"inclusion proof failed for {entry.get('mission_id')}"
                )
            results.append(detail)

        return {
            "ok": not problems,
            "tree_size": tree_size,
            "root_hash": root_hex,
            "entries": results,
            "problems": problems,
        }


# ── QR helper ────────────────────────────────────────────────────────────────

def build_root_qr_payload(root_info: dict) -> str:
    """Compact JSON payload for a QR image of the current ledger root."""
    payload = {
        "v": "sao-ledger-root/1",
        "tree_size": root_info["tree_size"],
        "root": root_info["root_hash"],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
