"""
compressor.py — zip a mission session folder into a single archive, and
validate untrusted archives before they are extracted or verified.

Uses stdlib zipfile only. The zip archive is written next to the session
folder so both the raw folder and the compressed copy are available.

``validate_archive_members`` is the shared guard for every place a zip is
read/extracted (see browser.py): it rejects path traversal, duplicate
entries, symlink entries, and archive bombs before any byte is extracted.
"""

import re
import zipfile
from pathlib import Path

# ── Decompression budget (archive-bomb protection) ───────────────────────────
# Total uncompressed size allowed across all entries.  Mission sessions are
# text-dominated and normally far below this; raise it if you legitimately
# record enormous sessions.
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024   # 512 MiB

# Per-entry compression-ratio sanity cap.  Applied only to entries larger
# than _RATIO_CHECK_MIN_SIZE uncompressed, because tiny legitimate files
# (e.g. a few KiB of zeros) can compress at very high ratios.
MAX_ENTRY_COMPRESSION_RATIO = 200
_RATIO_CHECK_MIN_SIZE = 1 * 1024 * 1024            # 1 MiB

_S_IFMT = 0o170000
_S_IFLNK = 0o120000


class ArchiveSecurityError(ValueError):
    """Raised when a zip archive contains unsafe or bomb-like entries."""


def _normalised_parts(name: str) -> list:
    """Split an entry name on both separators, dropping empty and '.' parts."""
    return [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]


def validate_archive_members(zf: zipfile.ZipFile) -> None:
    """Validate every entry of an open ZipFile before extraction.

    Raises ArchiveSecurityError when any entry:
      * has an absolute name (leading ``/``/``\\`` or a drive letter),
      * contains a ``..`` segment (path traversal) or normalises to nothing,
      * duplicates another entry's normalised name,
      * is a symlink (per the external-attributes mode bits),
      * blows the total uncompressed-size budget
        (MAX_TOTAL_UNCOMPRESSED_BYTES), or
      * exceeds the per-entry compression-ratio cap
        (MAX_ENTRY_COMPRESSION_RATIO, entries > 1 MiB uncompressed).

    Because absolute names, ``..`` segments, and both separators are
    rejected up front, every surviving entry joins UNDER the extraction
    root after normalisation — nothing can escape it.
    """
    seen = set()
    total_uncompressed = 0
    for info in zf.infolist():
        name = info.filename
        norm = name.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
            raise ArchiveSecurityError(
                f"unsafe absolute entry name in archive: {name!r}"
            )
        parts = _normalised_parts(name)
        if not parts:
            raise ArchiveSecurityError(
                f"entry name normalises to nothing: {name!r}"
            )
        if ".." in parts:
            raise ArchiveSecurityError(
                f"path traversal entry name in archive: {name!r}"
            )
        norm_path = "/".join(parts)
        if norm_path in seen:
            raise ArchiveSecurityError(
                f"duplicate entry name in archive: {name!r}"
            )
        seen.add(norm_path)

        if (info.external_attr >> 16) & _S_IFMT == _S_IFLNK:
            raise ArchiveSecurityError(
                f"symlink entry in archive is not allowed: {name!r}"
            )

        total_uncompressed += info.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ArchiveSecurityError(
                f"archive exceeds the decompression budget of "
                f"{MAX_TOTAL_UNCOMPRESSED_BYTES} uncompressed bytes "
                f"(at entry {name!r}) — refusing to extract a possible "
                f"archive bomb"
            )
        if (
            info.file_size >= _RATIO_CHECK_MIN_SIZE
            and info.compress_size > 0
            and info.file_size / info.compress_size > MAX_ENTRY_COMPRESSION_RATIO
        ):
            raise ArchiveSecurityError(
                f"entry {name!r} has a compression ratio above "
                f"{MAX_ENTRY_COMPRESSION_RATIO}:1 — refusing to extract a "
                f"possible archive bomb"
            )


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
