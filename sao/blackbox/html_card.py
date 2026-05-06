"""
html_card.py — standalone HTML mission card for a recorded session.

Generates a single HTML file with no JavaScript or remote resources. The
HTML may reference the local seal_qr.png image beside it.
"""

from __future__ import annotations

import html
from pathlib import Path

_PASS_COLOR = "#00e676"
_FAIL_COLOR = "#ff5252"
_BG         = "#0d1117"
_CARD_BG    = "#161b22"
_BORDER     = "#30363d"
_TEXT       = "#c9d1d9"
_MUTED      = "#8b949e"
_CODE_BG    = "#0d1117"


def _e(value) -> str:
    """HTML-escape a value, converting it to string first."""
    return html.escape(str(value))


def render_html_card(
    payload: dict,
    qr_payload_text: str | None = None,
    qr_image_src: str | None = None,
) -> str:
    """Return a complete standalone HTML document for one mission session.

    Parameters
    ----------
    payload:         Seal payload dict (from seal_card.build_seal_payload).
    qr_payload_text: Compact QR JSON string, or None if not available.
    """
    status       = payload.get("status", "UNKNOWN")
    badge_color  = _PASS_COLOR if status == "PASS" else _FAIL_COLOR
    mission_id   = _e(payload.get("mission_id", ""))
    name         = _e(payload.get("name", ""))
    command      = _e(payload.get("command", ""))
    started      = _e(payload.get("started_at", ""))
    ended        = _e(payload.get("ended_at", ""))
    changed      = _e(payload.get("changed_files_count", 0))
    sha256       = _e(payload.get("archive_sha256", ""))
    seal_ver     = _e(payload.get("seal_version", ""))

    qr_image_block = ""
    if qr_image_src:
        qr_image_block = f"""
      <tr>
        <td class="label">QR Image</td>
        <td>
          <img class="qr-image" src="{_e(qr_image_src)}" alt="QR code for mission seal payload">
        </td>
      </tr>"""

    qr_block = ""
    if qr_payload_text:
        qr_block = f"""
      <tr>
        <td class="label">QR Payload</td>
        <td><code class="qr">{_e(qr_payload_text)}</code></td>
      </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAO Mission Card — {mission_id}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {_BG};
    color: {_TEXT};
    font-family: 'Courier New', Courier, monospace;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    min-height: 100vh;
    padding: 2rem 1rem;
  }}
  .card {{
    background: {_CARD_BG};
    border: 1px solid {_BORDER};
    border-radius: 8px;
    width: 100%;
    max-width: 680px;
    overflow: hidden;
  }}
  .header {{
    background: #21262d;
    border-bottom: 1px solid {_BORDER};
    padding: 1.25rem 1.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.75rem;
  }}
  .title-group {{ display: flex; flex-direction: column; gap: 0.2rem; }}
  .org {{
    font-size: 0.65rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: {_MUTED};
  }}
  .mission-name {{
    font-size: 1.05rem;
    font-weight: bold;
    color: {_TEXT};
    word-break: break-all;
  }}
  .badge {{
    display: inline-block;
    padding: 0.3rem 0.9rem;
    border-radius: 4px;
    font-size: 0.85rem;
    font-weight: bold;
    letter-spacing: 0.08em;
    color: {_BG};
    background: {badge_color};
    flex-shrink: 0;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  td {{
    padding: 0.6rem 1.5rem;
    font-size: 0.82rem;
    vertical-align: top;
    border-bottom: 1px solid {_BORDER};
    word-break: break-all;
  }}
  tr:last-child td {{ border-bottom: none; }}
  td.label {{
    color: {_MUTED};
    white-space: nowrap;
    width: 38%;
    padding-right: 1rem;
  }}
  code {{
    background: {_CODE_BG};
    border: 1px solid {_BORDER};
    border-radius: 3px;
    padding: 0.1rem 0.35rem;
    font-size: 0.78rem;
    font-family: inherit;
    word-break: break-all;
  }}
  code.qr {{
    display: block;
    padding: 0.5rem 0.6rem;
    font-size: 0.72rem;
    line-height: 1.5;
  }}
  .qr-image {{
    display: block;
    width: 192px;
    height: 192px;
    image-rendering: pixelated;
    background: #ffffff;
    border: 1px solid {_BORDER};
    border-radius: 4px;
  }}
  .footer {{
    border-top: 1px solid {_BORDER};
    padding: 0.6rem 1.5rem;
    font-size: 0.65rem;
    color: {_MUTED};
    text-align: right;
    letter-spacing: 0.05em;
  }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <div class="title-group">
      <span class="org">Special Agent Ops &mdash; Mission Card</span>
      <span class="mission-name">{name}</span>
    </div>
    <span class="badge">{_e(status)}</span>
  </div>
  <table>
    <tr>
      <td class="label">Mission ID</td>
      <td><code>{mission_id}</code></td>
    </tr>
    <tr>
      <td class="label">Command</td>
      <td><code>{command}</code></td>
    </tr>
    <tr>
      <td class="label">Changed Files</td>
      <td>{changed}</td>
    </tr>
    <tr>
      <td class="label">Started</td>
      <td>{started}</td>
    </tr>
    <tr>
      <td class="label">Ended</td>
      <td>{ended}</td>
    </tr>
    <tr>
      <td class="label">Archive SHA256</td>
      <td><code>{sha256}</code></td>
    </tr>
    <tr>
      <td class="label">Seal Version</td>
      <td>{seal_ver}</td>
    </tr>{qr_image_block}{qr_block}
  </table>
  <div class="footer">Recorded by Special Agent Ops &bull; seal v{seal_ver}</div>
</div>
</body>
</html>
"""


def write_html_card(
    session_dir: Path,
    payload: dict,
    qr_payload_text: str | None = None,
    qr_image_path: Path | None = None,
) -> Path:
    """Render and write seal_card.html into *session_dir*.

    Parameters
    ----------
    session_dir:     The mission session folder.
    payload:         Seal payload dict (from seal_card.build_seal_payload).
    qr_payload_text: Compact QR JSON string (from qr_payload.render_qr_payload_text).
    qr_image_path:   Optional generated seal_qr.png path.

    Returns the Path of the written file.
    """
    qr_image_src = None
    if qr_image_path and Path(qr_image_path).exists():
        qr_image_src = "seal_qr.png"
    elif (session_dir / "seal_qr.png").exists():
        qr_image_src = "seal_qr.png"

    html_path = session_dir / "seal_card.html"
    html_path.write_text(
        render_html_card(payload, qr_payload_text, qr_image_src),
        encoding="utf-8",
    )
    return html_path
