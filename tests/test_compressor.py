"""Tests for sao.blackbox.compressor — session zip archiving."""

import zipfile
from pathlib import Path

from sao.blackbox import compressor


def _make_session(tmp_path: Path) -> Path:
    session = tmp_path / "sessions" / "20260101_120000_demo"
    session.mkdir(parents=True)
    (session / "manifest.json").write_text('{"mission_id": "demo"}', encoding="utf-8")
    (session / "stdout.txt").write_text("hello\n", encoding="utf-8")
    sub = session / "extra"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested", encoding="utf-8")
    return session


def test_zip_created_next_to_session_dir(tmp_path: Path):
    session = _make_session(tmp_path)
    zip_path = compressor.compress_session(session)
    assert zip_path == session.parent / "20260101_120000_demo.zip"
    assert zip_path.exists()
    assert zipfile.is_zipfile(zip_path)


def test_zip_contents_prefixed_with_mission_id(tmp_path: Path):
    session = _make_session(tmp_path)
    zip_path = compressor.compress_session(session)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert names == {
        "20260101_120000_demo/manifest.json",
        "20260101_120000_demo/stdout.txt",
        "20260101_120000_demo/extra/nested.txt",
    }


def test_extraction_round_trips_content(tmp_path: Path):
    session = _make_session(tmp_path)
    zip_path = compressor.compress_session(session)
    dest = tmp_path / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    extracted = dest / "20260101_120000_demo"
    assert (extracted / "manifest.json").read_text(encoding="utf-8") == '{"mission_id": "demo"}'
    assert (extracted / "extra" / "nested.txt").read_text(encoding="utf-8") == "nested"


def test_deterministic_member_order(tmp_path: Path):
    session = _make_session(tmp_path)
    zip_path = compressor.compress_session(session)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert names == sorted(names)
