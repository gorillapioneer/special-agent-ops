"""Tests for zip archive hardening (sao.blackbox.compressor validation).

The verifier must reject traversal names, duplicate entries, symlink
entries, and archive bombs before extracting a single byte.
"""

import warnings
import zipfile
from pathlib import Path

import pytest

from sao.blackbox import browser, compressor
from sao.blackbox.compressor import (
    ArchiveSecurityError,
    validate_archive_members,
)


def make_zip(path: Path, entries) -> Path:
    """Write a zip with (name_or_ZipInfo, data) entries."""
    with warnings.catch_warnings():
        # zipfile warns on duplicate names; we craft them deliberately.
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                zf.writestr(name, data)
    return path


def validate(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        validate_archive_members(zf)


class TestEntryNames:
    def test_clean_recorded_archive_passes(self, recorded_mission):
        validate(recorded_mission["zip_path"])  # must not raise

    def test_dotdot_traversal_rejected(self, tmp_path: Path):
        evil = make_zip(
            tmp_path / "evil.zip", [("session/../../escape.txt", b"x")]
        )
        with pytest.raises(ArchiveSecurityError, match="traversal"):
            validate(evil)

    def test_backslash_traversal_rejected(self, tmp_path: Path):
        evil = make_zip(tmp_path / "evil.zip", [("..\\escape.txt", b"x")])
        with pytest.raises(ArchiveSecurityError, match="traversal"):
            validate(evil)

    def test_absolute_name_rejected(self, tmp_path: Path):
        evil = make_zip(tmp_path / "evil.zip", [("/abs/path.txt", b"x")])
        with pytest.raises(ArchiveSecurityError, match="absolute"):
            validate(evil)

    def test_drive_letter_name_rejected(self, tmp_path: Path):
        evil = make_zip(tmp_path / "evil.zip", [("C:\\evil.txt", b"x")])
        with pytest.raises(ArchiveSecurityError, match="absolute"):
            validate(evil)

    def test_duplicate_names_rejected(self, tmp_path: Path):
        evil = make_zip(
            tmp_path / "dup.zip",
            [("session/a.txt", b"first"), ("session/a.txt", b"second")],
        )
        with pytest.raises(ArchiveSecurityError, match="duplicate"):
            validate(evil)

    def test_duplicate_after_normalisation_rejected(self, tmp_path: Path):
        evil = make_zip(
            tmp_path / "dup2.zip",
            [("session/a.txt", b"first"), ("session//a.txt", b"second")],
        )
        with pytest.raises(ArchiveSecurityError, match="duplicate"):
            validate(evil)


class TestSymlinks:
    def test_symlink_entry_rejected(self, tmp_path: Path):
        info = zipfile.ZipInfo("session/link")
        info.external_attr = (0o120777 << 16)   # lrwxrwxrwx
        evil = make_zip(tmp_path / "link.zip", [(info, b"/etc/target")])
        with pytest.raises(ArchiveSecurityError, match="symlink"):
            validate(evil)


class TestDecompressionBudget:
    def test_high_ratio_bomb_rejected(self, tmp_path: Path):
        # 8 MiB of zeros deflates to a few KiB — far past the ratio cap.
        bomb = make_zip(
            tmp_path / "bomb.zip",
            [("session/bomb.bin", b"\0" * (8 * 1024 * 1024))],
        )
        with pytest.raises(ArchiveSecurityError, match="ratio"):
            validate(bomb)

    def test_total_budget_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(compressor, "MAX_TOTAL_UNCOMPRESSED_BYTES", 1024)
        big = make_zip(
            tmp_path / "big.zip",
            [("session/a.bin", b"a" * 900), ("session/b.bin", b"b" * 900)],
        )
        with pytest.raises(ArchiveSecurityError, match="budget"):
            validate(big)

    def test_small_files_not_flagged_by_ratio(self, tmp_path: Path):
        # Tiny highly-compressible files are legitimate (empty diffs etc.).
        ok = make_zip(tmp_path / "ok.zip", [("session/zeros.txt", b"\0" * 4096)])
        validate(ok)  # must not raise


class TestVerifierIntegration:
    def test_verify_archive_file_rejects_injected_bomb(self, copy_mission):
        copy = copy_mission()
        with zipfile.ZipFile(copy["zip_path"], "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                f"{copy['mission_id']}/bomb.bin", b"\0" * (8 * 1024 * 1024)
            )
        with pytest.raises(ArchiveSecurityError):
            browser.verify_archive_file(copy["zip_path"])

    def test_extract_archive_to_temp_rejects_traversal(self, tmp_path: Path):
        evil = make_zip(
            tmp_path / "evil.zip", [("mission/../../escape.txt", b"x")]
        )
        with pytest.raises(ArchiveSecurityError):
            browser.extract_archive_to_temp(evil)
        assert not (tmp_path.parent / "escape.txt").exists()
