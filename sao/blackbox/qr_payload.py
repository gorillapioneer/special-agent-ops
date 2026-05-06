"""
qr_payload.py — compact QR-ready payload for a recorded mission session.

Writes two files into the session directory:

  seal_qr_payload.json  — compact JSON (no whitespace), suitable for QR encoding
  seal_qr_payload.txt   — same compact JSON string, plain text

The payload is intentionally minimal so it fits within a standard QR code
capacity (version 10 or lower at medium error correction).

Stdlib only — no external dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path

QR_VERSION = "0.4"


def build_qr_payload(seal_payload: dict) -> dict:
    """Return a minimal dict suitable for compact QR encoding.

    Parameters
    ----------
    seal_payload:  The full seal payload produced by seal_card.build_seal_payload().

    Returns
    -------
    dict with keys: sao, id, status, sha256, seal
    """
    return {
        "sao":    QR_VERSION,
        "id":     seal_payload["mission_id"],
        "status": seal_payload["status"],
        "sha256": seal_payload["archive_sha256"],
        "seal":   seal_payload["seal_version"],
    }


def render_qr_payload_text(payload: dict) -> str:
    """Return the compact JSON string for the QR payload (no whitespace)."""
    return json.dumps(payload, separators=(",", ":"))


def write_qr_payload(session_dir: Path, seal_payload: dict) -> dict:
    """Build and write the QR payload files into *session_dir*.

    Parameters
    ----------
    session_dir:   The mission session folder.
    seal_payload:  The full seal payload dict from seal_card.build_seal_payload().

    Returns
    -------
    dict with keys: qr_payload_json_path (Path), qr_payload_txt_path (Path)
    """
    payload = build_qr_payload(seal_payload)
    compact = render_qr_payload_text(payload)

    json_path = session_dir / "seal_qr_payload.json"
    txt_path = session_dir / "seal_qr_payload.txt"

    json_path.write_text(compact, encoding="utf-8")
    txt_path.write_text(compact, encoding="utf-8")

    return {
        "qr_payload_json_path": json_path,
        "qr_payload_txt_path":  txt_path,
    }
