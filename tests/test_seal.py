"""Tests for sao.blackbox.seal — SHA256 hashing and seal writing."""

import hashlib
import json
from pathlib import Path

from sao.blackbox import seal


class TestSha256File:
    def test_matches_hashlib(self, tmp_path: Path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello sealed world\x00\xff")
        expected = hashlib.sha256(b"hello sealed world\x00\xff").hexdigest()
        assert seal.sha256_file(f) == expected

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty"
        f.write_bytes(b"")
        assert seal.sha256_file(f) == hashlib.sha256(b"").hexdigest()

    def test_large_file_chunked(self, tmp_path: Path):
        # Larger than the 65 536-byte read chunk to exercise the loop.
        data = b"a" * 200_000
        f = tmp_path / "big"
        f.write_bytes(data)
        assert seal.sha256_file(f) == hashlib.sha256(data).hexdigest()


class TestSha256Text:
    def test_utf8_encoding(self):
        text = "mission éè unicode"
        assert seal.sha256_text(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestSha256Directory:
    def _make_tree(self, root: Path) -> None:
        (root / "a.txt").write_text("alpha", encoding="utf-8")
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("bravo", encoding="utf-8")

    def test_deterministic(self, tmp_path: Path):
        d = tmp_path / "d"
        d.mkdir()
        self._make_tree(d)
        assert seal.sha256_directory(d) == seal.sha256_directory(d)

    def test_content_change_changes_hash(self, tmp_path: Path):
        d = tmp_path / "d"
        d.mkdir()
        self._make_tree(d)
        before = seal.sha256_directory(d)
        (d / "a.txt").write_text("ALPHA", encoding="utf-8")
        assert seal.sha256_directory(d) != before

    def test_rename_changes_hash(self, tmp_path: Path):
        # Filenames are covered by the digest, not just contents.
        d = tmp_path / "d"
        d.mkdir()
        self._make_tree(d)
        before = seal.sha256_directory(d)
        (d / "a.txt").rename(d / "renamed.txt")
        assert seal.sha256_directory(d) != before

    def test_exclude_names_skipped(self, tmp_path: Path):
        d = tmp_path / "d"
        d.mkdir()
        self._make_tree(d)
        before = seal.sha256_directory(d, exclude_names={"noise.log"})
        (d / "noise.log").write_text("ignored", encoding="utf-8")
        assert seal.sha256_directory(d, exclude_names={"noise.log"}) == before

    def test_default_exclusions_cover_derived_files(self, tmp_path: Path):
        # Every derived/sealing file must be excluded so writing it after
        # sealing never invalidates the directory hash.
        d = tmp_path / "d"
        d.mkdir()
        self._make_tree(d)
        before = seal.sha256_directory(d)
        for name in [
            "seal.json", "seal.txt", "seal_card.md", "seal_card.html",
            "seal_payload.json", "seal_qr_payload.json", "seal_qr_payload.txt",
            "seal_qr.png", "mission_summary.md", "pr_report.md",
        ]:
            (d / name).write_text("derived", encoding="utf-8")
        assert seal.sha256_directory(d) == before

    def test_empty_directory(self, tmp_path: Path):
        d = tmp_path / "d"
        d.mkdir()
        # No files: outer hash of nothing == sha256 of empty input.
        assert seal.sha256_directory(d) == hashlib.sha256(b"").hexdigest()


class TestWriteSeal:
    def test_writes_seal_files_and_returns_dict(self, tmp_path: Path):
        session = tmp_path / "20260101_120000_demo"
        session.mkdir()
        manifest = {"mission_id": "20260101_120000_demo", "exit_code": 0}
        manifest_path = session / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (session / "stdout.txt").write_text("out", encoding="utf-8")

        archive = tmp_path / "20260101_120000_demo.zip"
        archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # empty zip

        result = seal.write_seal(session, archive, manifest_path)

        assert result["mission_id"] == "20260101_120000_demo"
        assert result["seal_version"] == seal.SEAL_VERSION
        assert result["manifest_sha256"] == seal.sha256_file(manifest_path)
        assert result["archive_sha256"] == seal.sha256_file(archive)
        assert result["session_directory_sha256"] == seal.sha256_directory(session)

        on_disk = json.loads((session / "seal.json").read_text(encoding="utf-8"))
        assert on_disk == result

        txt = (session / "seal.txt").read_text(encoding="utf-8")
        assert result["archive_sha256"] in txt
        assert result["manifest_sha256"] in txt
        assert "SPECIAL AGENT OPS MISSION SEAL" in txt

    def test_directory_hash_stable_after_sealing(self, tmp_path: Path):
        # seal.json / seal.txt themselves must not invalidate the seal.
        session = tmp_path / "s"
        session.mkdir()
        manifest_path = session / "manifest.json"
        manifest_path.write_text(json.dumps({"mission_id": "s"}), encoding="utf-8")
        archive = tmp_path / "s.zip"
        archive.write_bytes(b"zipbytes")

        result = seal.write_seal(session, archive, manifest_path)
        assert seal.sha256_directory(session) == result["session_directory_sha256"]

    def test_mission_id_falls_back_to_dir_name(self, tmp_path: Path):
        session = tmp_path / "fallback_name"
        session.mkdir()
        manifest_path = session / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"z")

        result = seal.write_seal(session, archive, manifest_path)
        assert result["mission_id"] == "fallback_name"
