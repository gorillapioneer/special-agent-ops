"""
dashboard.py — local mini dashboard for recorded mission sessions.

Starts a tiny HTTP server on 127.0.0.1 that lists all missions and
serves their HTML cards, summaries, QR payloads, and QR images through known,
validated routes.  No arbitrary file access is possible.

Routes:
    /                              Mission index
    /missions/<id>/card            seal_card.html
    /missions/<id>/summary         mission_summary.md (wrapped in HTML)
    /missions/<id>/qr-payload      seal_qr_payload.txt
    /missions/<id>/qr-image        seal_qr.png

Stdlib only — no external dependencies.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .browser import get_sessions_root, list_missions

# The only filenames this server will ever serve from a session folder.
_ROUTE_TO_FILE = {
    "card":       "seal_card.html",
    "summary":    "mission_summary.md",
    "qr-payload": "seal_qr_payload.txt",
    "qr-image":   "seal_qr.png",
}

_CONTENT_TYPE = {
    "seal_card.html":       "text/html; charset=utf-8",
    "mission_summary.md":   "text/plain; charset=utf-8",
    "seal_qr_payload.txt":  "text/plain; charset=utf-8",
    "seal_qr.png":          "image/png",
}

_BG    = "#0d1117"
_CARD  = "#161b22"
_BORD  = "#30363d"
_TEXT  = "#c9d1d9"
_MUTED = "#8b949e"
_PASS  = "#00e676"
_FAIL  = "#ff5252"
_LINK  = "#58a6ff"


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _e(v) -> str:
    return html.escape(str(v))


def _render_index(sessions_root: Path) -> str:
    missions = list_missions(sessions_root)

    if missions:
        rows = []
        for m in missions:
            mid = _e(m["mission_id"])
            status = _e(m["status"])
            badge_color = _PASS if m["status"] == "PASS" else _FAIL
            rows.append(f"""
      <tr>
        <td><code>{mid}</code></td>
        <td style="color:{badge_color};font-weight:bold">{status}</td>
        <td style="text-align:right">{_e(m['changed_files_count'])}</td>
        <td><code>{_e(m['command'])}</code></td>
        <td style="color:{_MUTED}">{_e(m['started_at'])}</td>
        <td>
          <a href="/missions/{mid}/card">card</a>
          &bull;
          <a href="/missions/{mid}/summary">summary</a>
          &bull;
          <a href="/missions/{mid}/qr-payload">qr</a>
          &bull;
          <a href="/missions/{mid}/qr-image">qr image</a>
        </td>
      </tr>""")
        table_body = "".join(rows)
        table = f"""
    <table>
      <thead>
        <tr>
          <th>Mission ID</th>
          <th>Status</th>
          <th>Changed</th>
          <th>Command</th>
          <th>Started</th>
          <th>Links</th>
        </tr>
      </thead>
      <tbody>{table_body}
      </tbody>
    </table>
    <p class="footer">{len(missions)} mission(s) &bull; {_e(sessions_root)}</p>"""
    else:
        table = "<p style='color:#8b949e'>No missions recorded yet.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Special Agent Ops — Dashboard</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {_BG};
    color: {_TEXT};
    font-family: 'Courier New', Courier, monospace;
    padding: 2rem 1rem;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  header {{ margin-bottom: 2rem; }}
  h1 {{ font-size: 1.3rem; letter-spacing: 0.1em; color: {_TEXT}; }}
  .sub {{ color: {_MUTED}; font-size: 0.8rem; margin-top: 0.3rem; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
  }}
  th, td {{
    padding: 0.55rem 0.75rem;
    text-align: left;
    border-bottom: 1px solid {_BORD};
    vertical-align: top;
    word-break: break-all;
  }}
  th {{
    color: {_MUTED};
    font-weight: normal;
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.06em;
    background: {_CARD};
  }}
  tr:hover td {{ background: {_CARD}; }}
  code {{ font-family: inherit; font-size: 0.78rem; }}
  a {{ color: {_LINK}; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .footer {{
    margin-top: 1rem;
    color: {_MUTED};
    font-size: 0.72rem;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>SPECIAL AGENT OPS</h1>
      <p class="sub">Black box recorder for AI coding agents &mdash; local dashboard</p>
    </header>
    {table}
  </div>
</body>
</html>
"""


def _render_not_found(message: str) -> str:
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Not Found</title></head>"
        f"<body style='background:#0d1117;color:#c9d1d9;font-family:monospace;padding:2rem'>"
        f"<h2>404 Not Found</h2><p>{html.escape(message)}</p></body></html>"
    )


# ── Request handler ───────────────────────────────────────────────────────────

def _make_handler(sessions_root: Path):
    """Return a BaseHTTPRequestHandler subclass bound to *sessions_root*."""

    class _Handler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            # Suppress default stderr logging; use our own concise output.
            print(f"  {self.address_string()} {fmt % args}")

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, code: int, body: str) -> None:
            self._send(code, "text/html; charset=utf-8", body.encode("utf-8"))

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"

            # ── Index ──────────────────────────────────────────────────────────
            if path == "/" or path == "":
                self._send_html(200, _render_index(sessions_root))
                return

            # ── Mission file routes ────────────────────────────────────────────
            # Expected: /missions/<mission_id>/<route_key>
            parts = path.lstrip("/").split("/")
            if len(parts) == 3 and parts[0] == "missions":
                _, mission_id, route_key = parts
                self._serve_mission_file(mission_id, route_key)
                return

            self._send_html(
                404,
                _render_not_found(f"Unknown route: {html.escape(path)}")
            )

        def _serve_mission_file(self, mission_id: str, route_key: str) -> None:
            # Validate route_key against the allowlist first.
            filename = _ROUTE_TO_FILE.get(route_key)
            if filename is None:
                self._send_html(404, _render_not_found(f"Unknown route: {route_key}"))
                return

            # Reject mission_id that contains path separators or traversal
            # sequences before we touch the filesystem at all.
            if any(c in mission_id for c in ("/", "\\", "\x00")) or ".." in mission_id:
                self._send_html(403, _render_not_found("Forbidden"))
                return

            # Resolve the candidate path and confirm it sits directly inside
            # sessions_root (one level deep — no sub-directory traversal).
            sessions_resolved = sessions_root.resolve()
            candidate = (sessions_root / mission_id).resolve()
            try:
                # relative_to() raises ValueError if candidate is not under
                # sessions_resolved, which covers all traversal attacks.
                rel = candidate.relative_to(sessions_resolved)
            except ValueError:
                self._send_html(403, _render_not_found("Forbidden"))
                return
            # A valid mission dir is exactly one level deep (no sub-paths).
            if len(rel.parts) != 1:
                self._send_html(403, _render_not_found("Forbidden"))
                return

            if not candidate.is_dir():
                self._send_html(404, _render_not_found(f"Mission not found: {mission_id}"))
                return

            file_path = candidate / filename
            if not file_path.exists():
                self._send_html(404, _render_not_found(
                    f"{filename} not found for mission {mission_id}"
                ))
                return

            content_type = _CONTENT_TYPE.get(filename, "text/plain; charset=utf-8")
            body = file_path.read_bytes()
            self._send(200, content_type, body)

    return _Handler


# ── Public API ────────────────────────────────────────────────────────────────

def run_dashboard(
    sessions_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start the dashboard server and block until Ctrl+C.

    Parameters
    ----------
    sessions_root:  Directory containing mission session folders.
    host:           Bind address (default 127.0.0.1 — loopback only).
    port:           TCP port to listen on (default 8765).
    """
    handler_class = _make_handler(sessions_root)

    with ThreadingHTTPServer((host, port), handler_class) as server:
        url = f"http://{host}:{port}"
        width = 64
        bar = "=" * width
        print()
        print(bar)
        print("  SPECIAL AGENT OPS — DASHBOARD")
        print(bar)
        print(f"  URL:            {url}")
        print(f"  Sessions Root:  {sessions_root}")
        print(f"  Press Ctrl+C to stop.")
        print(bar)
        print()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  Dashboard stopped.")
