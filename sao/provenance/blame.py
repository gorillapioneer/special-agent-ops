"""
blame.py — line-level provenance: which agent mission wrote this line?

``sao blame <file>`` runs ``git blame --line-porcelain`` and maps every
line's commit to a mission by reading the commit's attestation note
(refs/notes/sao).  Lines whose commit carries no note are unattested
(human or pre-provenance work) and shown with ``-``.

Honesty note: line attribution is a DERIVED, BEST-EFFORT view.  git blame
maps the *surviving textual line* to the commit that last touched it —
code movement, copying, reformatting, and merge-conflict resolution all
distort attribution.  Commit/patch-level provenance (attestations and the
recorded diff/object IDs) is canonical; blame output is a convenience.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import attest

_LINE_TRUNCATE = 80

#: Confidence marker included in every blame result (and --json output).
CONFIDENCE = "derived-best-effort"

#: One-line caveat shown in human output.
ATTRIBUTION_NOTE = (
    "line attribution is derived from git blame (best-effort): moves, "
    "copies, reformatting, and conflict resolution can distort it; "
    "commit/patch-level provenance is canonical"
)


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_line_porcelain(output: str) -> list:
    """Parse ``git blame --line-porcelain`` output.

    Returns a list of {"line": int, "commit": sha, "text": str} in file order.
    """
    rows = []
    lines = output.splitlines()
    i = 0
    current_commit = None
    current_line = None
    while i < len(lines):
        raw = lines[i]
        if raw.startswith("\t"):
            rows.append({
                "line": current_line,
                "commit": current_commit,
                "text": raw[1:],
            })
            current_commit = None
            current_line = None
        else:
            parts = raw.split()
            # Header lines look like: "<40-hex-sha> <orig> <final> [count]"
            if (
                current_commit is None
                and len(parts) >= 3
                and len(parts[0]) == 40
                and all(ch in "0123456789abcdef" for ch in parts[0])
            ):
                current_commit = parts[0]
                try:
                    current_line = int(parts[2])
                except ValueError:
                    current_line = None
            # other metadata lines (author, summary, filename, ...) skipped
        i += 1
    return rows


def blame_file(repo_path: Path, file_path: str) -> dict:
    """Annotate *file_path* with mission provenance per line.

    Returns {"file": str, "lines": [{"line", "commit", "commit_short",
    "mission_id", "attested", "text"}], "missions": {commit: mission_id}}.

    Raises ValueError when git blame fails (untracked file, bad path, ...).
    """
    repo_path = Path(repo_path)
    proc = _git(["blame", "--line-porcelain", "--", file_path], cwd=repo_path)
    if proc.returncode != 0:
        raise ValueError(
            f"git blame failed for {file_path}: {proc.stderr.strip()}"
        )

    rows = parse_line_porcelain(proc.stdout)

    # One note lookup per unique commit, not per line.
    note_cache = {}
    for row in rows:
        commit = row["commit"]
        if commit not in note_cache:
            note = attest.read_git_note(repo_path, commit)
            note_cache[commit] = note.get("mission_id") if note else None

    annotated = []
    for row in rows:
        commit = row["commit"] or ""
        mission_id = note_cache.get(commit)
        annotated.append({
            "line": row["line"],
            "commit": commit,
            "commit_short": commit[:10],
            "mission_id": mission_id,
            "attested": mission_id is not None,
            "text": row["text"],
        })

    return {
        "file": file_path,
        "confidence": CONFIDENCE,
        "confidence_note": ATTRIBUTION_NOTE,
        "lines": annotated,
        "missions": {c: m for c, m in note_cache.items() if m},
    }


def render_text(result: dict) -> str:
    """Human-readable annotated listing."""
    lines_out = []
    mission_width = max(
        [len("MISSION")] + [len(l["mission_id"] or "-") for l in result["lines"]]
    )
    header = f"{'LINE':>5}  {'MISSION':<{mission_width}}  {'COMMIT':<10}  TEXT"
    lines_out.append(header)
    lines_out.append("-" * len(header))
    for l in result["lines"]:
        text = l["text"]
        if len(text) > _LINE_TRUNCATE:
            text = text[: _LINE_TRUNCATE - 3] + "..."
        mission = l["mission_id"] or "-"
        lines_out.append(
            f"{l['line']:>5}  {mission:<{mission_width}}  {l['commit_short']:<10}  {text}"
        )
    attested = sum(1 for l in result["lines"] if l["attested"])
    lines_out.append("")
    lines_out.append(
        f"{attested}/{len(result['lines'])} line(s) attributable to "
        f"attested agent missions"
    )
    lines_out.append(f"NOTE: {ATTRIBUTION_NOTE}.")
    return "\n".join(lines_out)
