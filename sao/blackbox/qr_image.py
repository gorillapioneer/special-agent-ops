"""
qr_image.py - PNG QR image generation for mission seals.

Creates seal_qr.png from the compact QR payload text. The image is a
derived artefact: it is display/share convenience, not additional proof data.
"""

from __future__ import annotations

from pathlib import Path


def generate_qr_png(payload_text: str, output_path: Path) -> Path:
    """Generate a PNG QR code for *payload_text* at *output_path*."""
    payload = payload_text.strip()
    if not payload:
        raise ValueError("QR payload text is empty")

    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:
        raise RuntimeError(
            "QR image generation requires the 'qrcode[pil]' dependency. "
            "Install the project dependencies before recording missions."
        ) from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    image.save(output_path)
    return output_path


def write_qr_image(session_dir: Path, qr_payload_txt_path: Path) -> Path:
    """Read the compact QR payload text and write seal_qr.png."""
    qr_payload_txt_path = Path(qr_payload_txt_path)
    payload_text = qr_payload_txt_path.read_text(encoding="utf-8")
    return generate_qr_png(payload_text, Path(session_dir) / "seal_qr.png")
