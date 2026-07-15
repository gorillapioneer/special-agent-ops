"""Tests for sao.blackbox.browser — listing, loading, and seal verification.

These use a real recorded mission (session-scoped fixture) copied into a
private tmp directory whenever a test tampers with files.
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from sao.blackbox import browser


# ── Discovery ─────────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_get_sessions_root(self, tmp_path: Path):
        assert browser.get_sessions_root(tmp_path) == tmp_path / "blackbox" / "sessions"

    def test_list_missions_empty_when_missing(self, tmp_path: Path):
        assert browser.list_missions(tmp_path / "nope") == []

    def test_list_missions_reads_manifests(self, copy_mission):
        copy = copy_mission()
        missions = browser.list_missions(copy["sessions_root"])
        assert len(missions) == 1
        m = missions[0]
        assert m["mission_id"] == copy["mission_id"]
        assert m["status"] == "PASS"
        assert m["name"] == "Fixture Mission"

    def test_list_missions_skips_broken_manifest(self, copy_mission):
        copy = copy_mission()
        broken = copy["sessions_root"] / "zz_broken"
        broken.mkdir()
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")
        empty = copy["sessions_root"] / "zz_no_manifest"
        empty.mkdir()
        missions = browser.list_missions(copy["sessions_root"])
        assert [m["mission_id"] for m in missions] == [copy["mission_id"]]

    def test_find_mission(self, copy_mission):
        copy = copy_mission()
        found = browser.find_mission(copy["sessions_root"], copy["mission_id"])
        assert found == copy["session_dir"]

    def test_find_mission_missing_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            browser.find_mission(tmp_path, "no_such_mission")


# ── Loaders ───────────────────────────────────────────────────────────────────

class TestLoaders:
    def test_load_manifest_seal_and_payloads(self, recorded_mission):
        sd = recorded_mission["session_dir"]
        assert browser.load_manifest(sd)["mission_id"] == recorded_mission["mission_id"]
        assert len(browser.load_seal(sd)["archive_sha256"]) == 64
        assert browser.load_seal_payload(sd)["status"] == "PASS"
        assert browser.load_qr_payload(sd)["id"] == recorded_mission["mission_id"]

    def test_loaders_raise_on_missing_files(self, tmp_path: Path):
        for loader in (browser.load_manifest, browser.load_seal,
                       browser.load_seal_payload, browser.load_qr_payload):
            with pytest.raises(FileNotFoundError):
                loader(tmp_path)


# ── verify_mission ────────────────────────────────────────────────────────────

class TestVerifyMission:
    def test_untampered_mission_verifies(self, recorded_mission):
        result = browser.verify_mission(recorded_mission["session_dir"])
        assert result["verified"] is True
        assert result["manifest_ok"] and result["archive_ok"]
        assert result["session_directory_ok"]
        assert result["archive_found"] is True

    def test_tampered_manifest_detected(self, copy_mission):
        copy = copy_mission()
        manifest_path = copy["session_dir"] / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["exit_code"] = 0
        data["name"] = "totally legit mission"
        manifest_path.write_text(json.dumps(data, indent=2) + " ", encoding="utf-8")

        result = browser.verify_mission(copy["session_dir"])
        assert result["manifest_ok"] is False
        assert result["session_directory_ok"] is False
        assert result["verified"] is False

    def test_tampered_stdout_detected(self, copy_mission):
        copy = copy_mission()
        (copy["session_dir"] / "stdout.txt").write_text("forged", encoding="utf-8")
        result = browser.verify_mission(copy["session_dir"])
        assert result["session_directory_ok"] is False
        assert result["verified"] is False
        # Manifest and archive themselves are untouched.
        assert result["manifest_ok"] is True
        assert result["archive_ok"] is True

    def test_tampered_archive_detected(self, copy_mission):
        copy = copy_mission()
        copy["zip_path"].write_bytes(copy["zip_path"].read_bytes() + b"x")
        result = browser.verify_mission(copy["session_dir"])
        assert result["archive_ok"] is False
        assert result["verified"] is False

    def test_missing_archive_reported(self, copy_mission):
        copy = copy_mission()
        copy["zip_path"].unlink()
        result = browser.verify_mission(copy["session_dir"])
        assert result["archive_found"] is False
        assert result["archive_ok"] is False
        assert result["verified"] is False

    def test_added_unexpected_file_detected(self, copy_mission):
        # A file smuggled into the sealed directory must break verification.
        copy = copy_mission()
        (copy["session_dir"] / "planted.txt").write_text("evil", encoding="utf-8")
        result = browser.verify_mission(copy["session_dir"])
        assert result["session_directory_ok"] is False

    def test_pr_report_does_not_break_seal(self, copy_mission):
        # Regression: pr_report.md is a derived file written after sealing
        # (sao pr-report --output <session>/pr_report.md); it must be
        # excluded from the directory hash like the other derived files.
        from sao.blackbox import pr_report

        copy = copy_mission()
        pr_report.write_pr_report(copy["session_dir"])
        result = browser.verify_mission(copy["session_dir"])
        assert result["session_directory_ok"] is True
        assert result["verified"] is True


# ── verify_archive_file ───────────────────────────────────────────────────────

class TestVerifyArchiveFile:
    def test_verifies_with_session_folder_present(self, copy_mission):
        copy = copy_mission()
        result = browser.verify_archive_file(copy["zip_path"])
        assert result["verified"] is True
        assert result["archive_ok"] and result["manifest_ok"]
        assert result["session_directory_ok"]
        assert result["mission_id"] == copy["mission_id"]

    def test_verifies_portably_with_companion_seal(self, copy_mission, tmp_path: Path):
        # Distribute only the .zip plus "<archive>.seal.json", exactly as the
        # error message instructs: cp <session_dir>/seal.json <archive>.seal.json
        copy = copy_mission()
        portable = tmp_path / "portable"
        portable.mkdir()
        zip_copy = portable / copy["zip_path"].name
        shutil.copy2(copy["zip_path"], zip_copy)
        companion = portable / (zip_copy.name + ".seal.json")
        shutil.copy2(copy["session_dir"] / "seal.json", companion)

        result = browser.verify_archive_file(zip_copy)
        assert result["verified"] is True

    def test_legacy_companion_name_still_accepted(self, copy_mission, tmp_path: Path):
        # Older layout: suffix replacement -> "<stem>.seal.json".
        copy = copy_mission()
        portable = tmp_path / "portable_legacy"
        portable.mkdir()
        zip_copy = portable / copy["zip_path"].name
        shutil.copy2(copy["zip_path"], zip_copy)
        legacy = zip_copy.with_suffix(".seal.json")
        shutil.copy2(copy["session_dir"] / "seal.json", legacy)

        result = browser.verify_archive_file(zip_copy)
        assert result["verified"] is True

    def test_missing_seal_raises_with_hint(self, copy_mission, tmp_path: Path):
        copy = copy_mission()
        lonely = tmp_path / "lonely"
        lonely.mkdir()
        zip_copy = lonely / copy["zip_path"].name
        shutil.copy2(copy["zip_path"], zip_copy)
        with pytest.raises(FileNotFoundError, match="seal.json not found"):
            browser.verify_archive_file(zip_copy)

    def test_tampered_archive_member_detected(self, copy_mission):
        # Rebuild the zip with a modified stdout.txt: archive hash AND
        # extracted directory hash must both fail.
        copy = copy_mission()
        zip_path = copy["zip_path"]
        with zipfile.ZipFile(zip_path) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        for name in members:
            if name.endswith("stdout.txt"):
                members[name] = b"forged output"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)

        result = browser.verify_archive_file(zip_path)
        assert result["archive_ok"] is False
        assert result["session_directory_ok"] is False
        assert result["verified"] is False

    def test_missing_archive_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            browser.verify_archive_file(tmp_path / "ghost.zip")

    def test_non_zip_rejected(self, tmp_path: Path):
        bogus = tmp_path / "bogus.zip"
        bogus.write_text("not a zip", encoding="utf-8")
        with pytest.raises(ValueError):
            browser.verify_archive_file(bogus)


# ── Extraction helpers ────────────────────────────────────────────────────────

class TestExtractionHelpers:
    def test_extract_archive_to_temp(self, recorded_mission, tmp_path, monkeypatch):
        # Point the tempfile module at pytest's tmp_path so the extracted
        # directory is cleaned up with the test's own temp tree.
        import tempfile

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        tmp = browser.extract_archive_to_temp(recorded_mission["zip_path"])
        assert tmp.name.startswith("sao_verify_")
        session = browser.find_session_dir_in_extracted_archive(tmp)
        assert session.name == recorded_mission["mission_id"]
        assert (session / "manifest.json").exists()

    def test_find_session_dir_empty_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            browser.find_session_dir_in_extracted_archive(tmp_path)


# ── HTML card path ────────────────────────────────────────────────────────────

class TestHtmlCardPath:
    def test_get_html_card_path(self, recorded_mission):
        path = browser.get_html_card_path(recorded_mission["session_dir"])
        assert path.name == "seal_card.html"

    def test_missing_card_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            browser.get_html_card_path(tmp_path)
