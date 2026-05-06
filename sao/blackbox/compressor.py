"""
compressor.py — zip a mission session folder into a single archive.

Uses stdlib zipfile only. The zip archive is written next to the session
folder so both the raw folder and the compressed copy are available.
"""

import zipfile
from pathlib import Path


def compress_session(session_dir: Path) -> Path:
    """Create <session_dir>.zip containing every file in session_dir.

    The archive uses paths relative to the sessions/ parent so the zip
    extracts into a named folder (e.g. ``20260506_091500_pytest_baseline/``).

    Returns the Path of the created zip file.
    """
    zip_path = session_dir.parent / f"{session_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(session_dir.rglob("*")):
            if file.is_file():
                # Store as <mission_id>/filename so extraction is self-contained
                arcname = file.relative_to(session_dir.parent)
                zf.write(file, arcname)
    return zip_path
