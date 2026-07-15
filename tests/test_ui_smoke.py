"""Smoke tests for UI-ish modules: html_card, maproom, dashboard, summary.

These only check that output is generated, contains expected fields, and
references no external assets.
"""

import urllib.request
from pathlib import Path

from sao.blackbox import dashboard, html_card, maproom, summary
from sao.blackbox.browser import get_sessions_root

PAYLOAD = {
    "mission_id": "20260101_120000_demo",
    "name": "demo <mission> & co",
    "repo_path": "/tmp/repo",
    "started_at": "2026-01-01T12:00:00+00:00",
    "ended_at": "2026-01-01T12:00:01+00:00",
    "command": 'echo "<script>"',
    "exit_code": 0,
    "status": "PASS",
    "changed_files_count": 1,
    "archive_sha256": "d" * 64,
    "seal_version": "0.2",
}

MANIFEST = {
    "mission_id": "20260101_120000_demo",
    "name": "demo mission",
    "command": "echo hi",
    "started_at": "2026-01-01T12:00:00+00:00",
    "ended_at": "2026-01-01T12:00:01+00:00",
    "duration_seconds": 1.234,
    "exit_code": 0,
    "git_branch": "main",
    "git_commit_before": "a" * 40,
    "git_commit_after": "a" * 40,
    "changed_files": ["x.py"],
}


def _assert_no_external_assets(html_text: str):
    # "@import" listed first so this line never resembles a credentialed URL
    # to the repo's secrets scanner.
    for marker in ("@import", "http://", "https://", "cdn."):
        assert marker not in html_text, f"external asset reference found: {marker}"


class TestHtmlCard:
    def test_render_contains_fields_and_escapes(self):
        doc = html_card.render_html_card(PAYLOAD, qr_payload_text='{"sao":"0.4"}')
        assert doc.startswith("<!DOCTYPE html>")
        assert "20260101_120000_demo" in doc
        assert "d" * 64 in doc
        assert "PASS" in doc
        # HTML-escaping of user-controlled fields.
        assert "<script>" not in doc
        assert "&lt;script&gt;" in doc
        _assert_no_external_assets(doc)

    def test_write_html_card(self, tmp_path: Path):
        path = html_card.write_html_card(tmp_path, PAYLOAD, qr_payload_text="{}")
        assert path == tmp_path / "seal_card.html"
        assert "demo" in path.read_text(encoding="utf-8")

    def test_qr_image_referenced_when_present(self, tmp_path: Path):
        (tmp_path / "seal_qr.png").write_bytes(b"\x89PNG")
        path = html_card.write_html_card(tmp_path, PAYLOAD)
        assert 'src="seal_qr.png"' in path.read_text(encoding="utf-8")


class TestSummary:
    def test_summary_contains_key_sections(self):
        text = summary.generate_summary(MANIFEST, "the stdout", "the stderr")
        assert "# Mission Summary: demo mission" in text
        assert "## Git delta" in text
        assert "the stdout" in text
        assert "the stderr" in text
        assert "`x.py`" in text

    def test_stdout_truncated_at_cap(self):
        text = summary.generate_summary(MANIFEST, "x" * 10_000, "")
        assert "truncated" in text
        assert "x" * 10_000 not in text

    def test_seal_section_included(self):
        seal = {
            "seal_version": "0.2", "archive_sha256": "e" * 64,
            "manifest_sha256": "f" * 64, "session_directory_sha256": "9" * 64,
            "created_at": "2026-01-01T12:00:02+00:00",
        }
        text = summary.generate_summary(MANIFEST, "", "", seal=seal)
        assert "## Mission Seal" in text
        assert "e" * 64 in text


class TestMaproom:
    def test_empty_sessions_renders_empty_state(self, tmp_path: Path):
        out = maproom.write_maproom(get_sessions_root(tmp_path))
        assert out == tmp_path / "blackbox" / "maproom.html"
        doc = out.read_text(encoding="utf-8")
        assert "No missions recorded yet" in doc
        _assert_no_external_assets(doc)

    def test_maproom_lists_recorded_mission(self, recorded_mission):
        sessions_root = recorded_mission["session_dir"].parent
        missions = maproom.collect_maproom_missions(sessions_root)
        assert len(missions) >= 1
        m = missions[0]
        assert m["mission_id"] == recorded_mission["mission_id"]
        assert m["status"] == "PASS"
        assert m["seal_card_exists"] is True

        out = maproom.write_maproom(sessions_root)
        doc = out.read_text(encoding="utf-8")
        assert recorded_mission["mission_id"] in doc
        assert "MAPROOM" in doc
        _assert_no_external_assets(doc)
        out.unlink()  # keep the shared fixture tree pristine

    def test_custom_output_path(self, tmp_path: Path):
        out = maproom.write_maproom(tmp_path / "none", tmp_path / "sub" / "map.html")
        assert out == tmp_path / "sub" / "map.html"
        assert out.exists()


class TestDashboard:
    def test_index_renders_missions(self, copy_mission):
        copy = copy_mission()
        doc = dashboard._render_index(copy["sessions_root"])
        assert copy["mission_id"] in doc
        assert "SPECIAL AGENT OPS" in doc
        _assert_no_external_assets(doc)

    def test_index_empty_state(self, tmp_path: Path):
        doc = dashboard._render_index(tmp_path / "none")
        assert "No missions recorded yet" in doc

    def test_http_routes(self, copy_mission):
        # Real HTTP round-trip on an ephemeral port.
        import threading
        from http.server import ThreadingHTTPServer

        copy = copy_mission()
        handler = dashboard._make_handler(copy["sessions_root"])
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(path):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                        return r.status, r.read()
                except urllib.error.HTTPError as e:
                    return e.code, e.read()

            status, body = get("/")
            assert status == 200
            assert copy["mission_id"].encode() in body

            mid = copy["mission_id"]
            status, body = get(f"/missions/{mid}/card")
            assert status == 200 and b"<!DOCTYPE html>" in body

            status, body = get(f"/missions/{mid}/summary")
            assert status == 200 and b"Mission Summary" in body

            status, body = get(f"/missions/{mid}/qr-payload")
            assert status == 200 and b'"sao"' in body

            status, body = get(f"/missions/{mid}/qr-image")
            assert status == 200 and body.startswith(b"\x89PNG")

            # Unknown route and traversal attempts are rejected.
            status, _ = get("/missions/%2E%2E/qr-image")
            assert status in (403, 404)
            status, _ = get(f"/missions/{mid}/unknown-route")
            assert status == 404
            status, _ = get("/etc/passwd")
            assert status == 404
        finally:
            server.shutdown()
            server.server_close()
