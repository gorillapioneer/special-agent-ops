"""
maproom.py - standalone mission timeline for Special Agent Ops.

Reads recorded mission folders from blackbox/sessions/ and writes a local
HTML control-room view. The output is runtime-generated and should not be
committed.
"""

from __future__ import annotations

import html
import json
import os
from collections import defaultdict
from pathlib import Path


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _status_from_manifest(manifest: dict, seal_payload: dict) -> str:
    if "exit_code" in manifest:
        return "PASS" if manifest.get("exit_code") == 0 else "FAIL"
    status = seal_payload.get("status")
    return status if status in {"PASS", "FAIL"} else "UNKNOWN"


def collect_maproom_missions(sessions_root: Path) -> list[dict]:
    """Return mission dictionaries parsed from blackbox/sessions/*."""
    sessions_root = Path(sessions_root)
    if not sessions_root.is_dir():
        return []

    base = sessions_root.parent
    missions = []
    for session_dir in sorted(sessions_root.iterdir(), reverse=True):
        if not session_dir.is_dir():
            continue

        manifest_path = session_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        manifest = _load_json(manifest_path)
        if not manifest:
            continue

        seal_payload = _load_json(session_dir / "seal_payload.json")
        qr_payload_path = session_dir / "seal_qr_payload.txt"
        qr_payload_text = ""
        if qr_payload_path.exists():
            try:
                qr_payload_text = qr_payload_path.read_text(encoding="utf-8").strip()
            except Exception:
                qr_payload_text = ""

        mission_id = manifest.get("mission_id", session_dir.name)
        started_at = manifest.get("started_at", "")
        seal_card = session_dir / "seal_card.html"
        summary = session_dir / "mission_summary.md"
        qr_image = session_dir / "seal_qr.png"

        missions.append({
            "mission_id": mission_id,
            "name": manifest.get("name", mission_id),
            "status": _status_from_manifest(manifest, seal_payload),
            "branch": manifest.get("git_branch", "unknown") or "unknown",
            "command_mode": manifest.get("command_mode", "shell"),
            "command": manifest.get("command", ""),
            "changed_files_count": manifest.get("changed_files_count", 0),
            "started_at": started_at,
            "started_date": started_at[:10] if started_at else "unknown",
            "archive_sha256": seal_payload.get("archive_sha256", ""),
            "qr_payload_present": bool(qr_payload_text),
            "session_dir": str(session_dir),
            "seal_card_exists": seal_card.exists(),
            "seal_card_path": _display_path(seal_card, base),
            "seal_card_fs_path": str(seal_card),
            "mission_summary_exists": summary.exists(),
            "mission_summary_path": _display_path(summary, base),
            "mission_summary_fs_path": str(summary),
            "qr_image_exists": qr_image.exists(),
            "qr_image_path": _display_path(qr_image, base),
            "qr_image_fs_path": str(qr_image),
        })

    missions.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return missions


def _summary_cards(missions: list[dict]) -> str:
    total = len(missions)
    pass_count = sum(1 for m in missions if m.get("status") == "PASS")
    fail_count = sum(1 for m in missions if m.get("status") == "FAIL")
    branches = {m.get("branch", "unknown") for m in missions}
    latest = missions[0].get("started_at", "n/a") if missions else "n/a"

    cards = [
        ("Total Missions", total),
        ("PASS", pass_count),
        ("FAIL", fail_count),
        ("Unique Branches", len(branches) if missions else 0),
        ("Latest Mission", latest),
    ]
    return "\n".join(
        f"""      <div class="summary-card">
        <div class="summary-label">{_e(label)}</div>
        <div class="summary-value">{_e(value)}</div>
      </div>"""
        for label, value in cards
    )


def _path_link(mission: dict, prefix: str, label: str) -> str:
    exists = mission.get(f"{prefix}_exists")
    path = mission.get(f"{prefix}_path", "")
    href = mission.get(f"{prefix}_href", "")
    if exists and href:
        return (
            f'<a href="{_e(href)}" title="{_e(path)}">{_e(label)}</a>'
            f'<div class="path">{_e(path)}</div>'
        )
    if exists:
        return f'<span>{_e(label)}</span><div class="path">{_e(path)}</div>'
    return '<span class="muted">missing</span>'


def _mission_table(missions: list[dict]) -> str:
    if not missions:
        return ""

    rows = []
    for mission in missions:
        status = mission.get("status", "UNKNOWN")
        rows.append(f"""
        <tr>
          <td><code>{_e(mission.get("mission_id", ""))}</code></td>
          <td><span class="status status-{_e(status.lower())}">{_e(status)}</span></td>
          <td>{_e(mission.get("branch", "unknown"))}</td>
          <td>{_e(mission.get("command_mode", "shell"))}</td>
          <td><code>{_e(mission.get("command", ""))}</code></td>
          <td class="number">{_e(mission.get("changed_files_count", 0))}</td>
          <td>{_e(mission.get("started_at", ""))}</td>
          <td>{_path_link(mission, "seal_card", "card")}</td>
          <td>{_path_link(mission, "mission_summary", "summary")}</td>
          <td>{_path_link(mission, "qr_image", "qr")}</td>
        </tr>""")

    return f"""
    <section class="section">
      <h2>Mission Table</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Mission ID</th>
              <th>Status</th>
              <th>Branch</th>
              <th>Mode</th>
              <th>Command</th>
              <th>Changed</th>
              <th>Started</th>
              <th>Card</th>
              <th>Summary</th>
              <th>QR</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}
          </tbody>
        </table>
      </div>
    </section>"""


def _timeline(missions: list[dict]) -> str:
    if not missions:
        return """
    <section class="section empty">
      <h2>No missions recorded yet</h2>
      <p>Create a mission, then regenerate this MapRoom page.</p>
      <pre><code>sao wrap --name "demo mission" -- python --version</code></pre>
    </section>"""

    grouped = defaultdict(list)
    for mission in missions:
        grouped[mission.get("branch", "unknown")].append(mission)

    branch_blocks = []
    for branch in sorted(grouped):
        items = []
        for mission in grouped[branch]:
            status = mission.get("status", "UNKNOWN")
            items.append(f"""
        <div class="timeline-item timeline-{_e(status.lower())}">
          <span class="status status-{_e(status.lower())}">{_e(status)}</span>
          <span class="date">{_e(mission.get("started_date", "unknown"))}</span>
          <strong>{_e(mission.get("name", ""))}</strong>
          <code>{_e(mission.get("command", ""))}</code>
        </div>""")
        branch_blocks.append(f"""
      <div class="branch-block">
        <h3>Branch: {_e(branch)}</h3>
        {''.join(items)}
      </div>""")

    return f"""
    <section class="section">
      <h2>Timeline By Branch</h2>
      {''.join(branch_blocks)}
    </section>"""


def render_maproom_html(missions: list[dict]) -> str:
    """Return standalone MapRoom HTML for mission dictionaries."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Special Agent Ops MapRoom</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: #0d1117;
    color: #c9d1d9;
    font-family: "Courier New", Courier, monospace;
  }}
  .wrap {{ max-width: 1280px; margin: 0 auto; padding: 2rem 1rem 3rem; }}
  header {{ margin-bottom: 1.5rem; }}
  h1 {{ margin: 0; font-size: 1.45rem; letter-spacing: 0.08em; }}
  h2 {{ margin: 0 0 1rem; font-size: 1rem; color: #f0f6fc; }}
  h3 {{ margin: 0 0 0.75rem; font-size: 0.9rem; color: #f0f6fc; }}
  .sub {{ margin-top: 0.45rem; color: #8b949e; font-size: 0.82rem; }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1.5rem;
  }}
  .summary-card {{
    border: 1px solid #30363d;
    background: #161b22;
    border-radius: 8px;
    padding: 0.85rem;
    min-height: 82px;
  }}
  .summary-label {{ color: #8b949e; font-size: 0.7rem; text-transform: uppercase; }}
  .summary-value {{ margin-top: 0.4rem; color: #f0f6fc; font-size: 1rem; word-break: break-all; }}
  .section {{
    border: 1px solid #30363d;
    background: #0f141b;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 1rem;
  }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; min-width: 1080px; }}
  th, td {{
    border-bottom: 1px solid #30363d;
    padding: 0.55rem 0.65rem;
    text-align: left;
    vertical-align: top;
    font-size: 0.78rem;
  }}
  th {{ color: #8b949e; text-transform: uppercase; font-weight: normal; font-size: 0.68rem; }}
  code {{ font-family: inherit; color: #f0f6fc; word-break: break-all; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .path {{ color: #8b949e; font-size: 0.66rem; margin-top: 0.25rem; word-break: break-all; }}
  .muted {{ color: #8b949e; }}
  .number {{ text-align: right; }}
  .status {{
    display: inline-block;
    border-radius: 4px;
    padding: 0.16rem 0.45rem;
    font-weight: bold;
    color: #0d1117;
    font-size: 0.7rem;
  }}
  .status-pass {{ background: #00e676; }}
  .status-fail {{ background: #ff5252; }}
  .status-unknown {{ background: #8b949e; }}
  .branch-block {{ margin-top: 1rem; }}
  .branch-block:first-of-type {{ margin-top: 0; }}
  .timeline-item {{
    display: grid;
    grid-template-columns: auto minmax(6.5rem, auto) minmax(12rem, 1fr) minmax(14rem, 2fr);
    gap: 0.75rem;
    align-items: center;
    border-left: 3px solid #8b949e;
    background: #161b22;
    border-radius: 6px;
    padding: 0.65rem 0.75rem;
    margin-top: 0.5rem;
  }}
  .timeline-pass {{ border-left-color: #00e676; }}
  .timeline-fail {{ border-left-color: #ff5252; }}
  .date {{ color: #8b949e; font-size: 0.78rem; }}
  .empty p {{ color: #8b949e; }}
  pre {{
    overflow-x: auto;
    border: 1px solid #30363d;
    background: #0d1117;
    border-radius: 6px;
    padding: 0.75rem;
  }}
  footer {{ color: #8b949e; font-size: 0.72rem; margin-top: 1rem; text-align: right; }}
  @media (max-width: 760px) {{
    .timeline-item {{ grid-template-columns: 1fr; gap: 0.35rem; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>SPECIAL AGENT OPS MAPROOM</h1>
      <p class="sub">Mission timeline for AI coding agent black boxes</p>
    </header>
    <section class="summary-grid">
{_summary_cards(missions)}
    </section>
{_mission_table(missions)}
{_timeline(missions)}
    <footer>Generated locally by Special Agent Ops. No JavaScript. No external assets.</footer>
  </div>
</body>
</html>
"""


def _add_relative_hrefs(missions: list[dict], output_path: Path) -> list[dict]:
    output_dir = output_path.parent.resolve()
    enriched = []
    for mission in missions:
        item = dict(mission)
        for prefix in ("seal_card", "mission_summary", "qr_image"):
            fs_path = Path(item.get(f"{prefix}_fs_path", ""))
            if item.get(f"{prefix}_exists") and fs_path.exists():
                try:
                    href = os.path.relpath(fs_path.resolve(), output_dir)
                    item[f"{prefix}_href"] = href.replace(os.sep, "/")
                except ValueError:
                    item[f"{prefix}_href"] = fs_path.resolve().as_uri()
            else:
                item[f"{prefix}_href"] = ""
        enriched.append(item)
    return enriched


def write_maproom(sessions_root: Path, output_path: Path | None = None) -> Path:
    """Write maproom.html and return the output path."""
    sessions_root = Path(sessions_root)
    if output_path is None:
        output_path = sessions_root.parent / "maproom.html"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    missions = _add_relative_hrefs(
        collect_maproom_missions(sessions_root),
        output_path,
    )
    output_path.write_text(render_maproom_html(missions), encoding="utf-8")
    return output_path
