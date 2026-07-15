#!/usr/bin/env python3
"""
safety-gate.py - Special Agent Ops
Scans a git diff or working tree for risky changes.
Prints a PASS / WARN / BLOCK report.

No third-party dependencies required.

Usage:
    python safety-gate.py --diff              # scan git diff (HEAD vs working tree)
    python safety-gate.py --staged            # scan staged changes only
    python safety-gate.py --tree              # scan all tracked files in working tree
    python safety-gate.py --file path/to/file # scan a specific file
    python safety-gate.py --diff-file f.diff  # scan a saved diff file
"""

import sys
import os
import re
import subprocess
import argparse
from pathlib import Path


# ── Risky file path patterns ──────────────────────────────────────────────────

BLOCK_PATH_PATTERNS = [
    (r"\.env$",                     "Environment file (.env)"),
    (r"\.env\.",                    "Environment file variant (.env.*)"),
    (r"secrets?[/\\]",              "Secrets directory"),
    (r"credentials?\.(json|yaml|yml|toml)", "Credentials file"),
    (r"private[/\\]",               "Private directory"),
    (r"vault[/\\]",                 "Vault directory"),
    (r"\.(pem|key|p12|pfx)$",       "Certificate or private key file"),
    (r"id_rsa",                     "SSH private key (id_rsa)"),
    (r"id_ed25519",                 "SSH private key (id_ed25519)"),
    (r"\.secret$",                  "File with .secret extension"),
    (r"service.account",            "Service account credentials file"),
]

BLOCK_CONTENT_PATTERNS = [
    (r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----",
     "Private key material in content"),
    (r"-----BEGIN CERTIFICATE-----",
     "Certificate in content (confirm intentional)"),
    (r"(?i)(password|passwd|secret|api_key|apikey|access_token|auth_token|private_key)\s*=\s*['\"][^'\"]{8,}['\"]",
     "Hardcoded credential assignment"),
    (r"(?i)(password|passwd|secret)\s*:\s*[^\$\{][^\s]{6,}",
     "Hardcoded credential in config-style syntax"),
    # Common key prefixes for well-known services
    (r"\bsk-[A-Za-z0-9]{20,}",     "Likely OpenAI / Stripe secret key (sk-)"),
    (r"\bpk_live_[A-Za-z0-9]{20,}", "Stripe live publishable key"),
    (r"\brk_live_[A-Za-z0-9]{20,}", "Stripe live restricted key"),
    (r"\bAIza[A-Za-z0-9\-_]{35}",   "Google API key (AIza...)"),
    (r"\bAKIA[A-Z0-9]{16}",         "AWS access key ID (AKIA...)"),
    (r"\bghp_[A-Za-z0-9]{36,}",     "GitHub personal access token (ghp_)"),
    (r"\bghs_[A-Za-z0-9]{36,}",     "GitHub Actions secret token (ghs_)"),
    (r"\bglpat-[A-Za-z0-9\-_]{20,}","GitLab personal access token (glpat-)"),
    (r"\bxox[bpars]-[A-Za-z0-9\-]+","Slack token (xoxb/xoxp/xoxa/xoxr/xoxs)"),
    (r"\bya29\.[A-Za-z0-9\-_]+",    "Google OAuth 2.0 access token (ya29.)"),
    (r"\bEAAC[A-Za-z0-9]+",         "Facebook/Meta access token"),
    (r"postgres://[^:]+:[^@]+@",    "PostgreSQL connection string with credentials"),
    (r"mysql://[^:]+:[^@]+@",       "MySQL connection string with credentials"),
    (r"mongodb://[^:]+:[^@]+@",     "MongoDB connection string with credentials"),
    (r"redis://:[^@]+@",            "Redis connection string with password"),
]

WARN_PATH_PATTERNS = [
    (r"auth[/\\]",                  "Auth directory"),
    (r"authentication[/\\]",        "Authentication directory"),
    (r"payment[s]?[/\\]",           "Payments directory"),
    (r"billing[/\\]",               "Billing directory"),
    (r"crypto[/\\]",                "Crypto/cryptography directory"),
    (r"trading[/\\]",               "Trading directory"),
    (r"execution[/\\]",             "Execution directory"),
    (r"\.(sql)$",                   "SQL file (check for destructive statements)"),
    (r"migration",                  "Migration file"),
    (r"deploy",                     "Deploy script or config"),
    (r"\.github[/\\]workflows",     "GitHub Actions workflow"),
    (r"dockerfile",                 "Dockerfile"),
    (r"docker-compose",             "Docker Compose file"),
    (r"terraform",                  "Terraform config"),
    (r"\.ya?ml$",                   "YAML config file"),
]

# Repository-owned automation that runs this scanner, plus documented
# workflow templates shipped for consumer repos. Contents are still scanned.
ALLOWED_INTERNAL_PATHS = {
    ".github/workflows/safety-checks.yml",
    ".github/workflows/tests.yml",
    "templates/verify-pr.yml",
    "templates/sao-provenance-issuer.yml",
    "templates/sao-witness.yml",
}

WARN_CONTENT_PATTERNS = [
    (r"(?i)DROP\s+TABLE",           "SQL DROP TABLE statement"),
    (r"(?i)DROP\s+DATABASE",        "SQL DROP DATABASE statement"),
    (r"(?i)TRUNCATE\s+TABLE",       "SQL TRUNCATE TABLE statement"),
    (r"(?i)DELETE\s+FROM\s+\w+\s*;","SQL DELETE without WHERE clause"),
    (r"(?i)rm\s+-rf?\s+[/~]",       "Destructive rm -rf on root or home path"),
    (r"(?i)os\.remove|os\.unlink|shutil\.rmtree",
     "Python file deletion call"),
    (r"(?i)process\.exit\(0\)",     "Hard process exit (may skip cleanup)"),
    (r"(?i)exec\s*\(",              "Dynamic code execution (exec call)"),
    (r"(?i)eval\s*\(",              "Dynamic evaluation (eval call)"),
    (r"subprocess\.call|subprocess\.Popen|os\.system",
     "Shell command execution — confirm it is not user-controlled input"),
    (r"(?i)console\.log.*token|console\.log.*password|console\.log.*secret",
     "Possible logging of sensitive value"),
    (r"(?i)print.*password|print.*secret|print.*token",
     "Possible printing of sensitive value"),
    (r"TODO.*(?:auth|secret|payment|credential)",
     "TODO comment referencing sensitive area"),
    (r"FIXME.*(?:auth|secret|payment|credential)",
     "FIXME comment referencing sensitive area"),
]

# Diff lines that start with these are additions we want to check
DIFF_ADDITION_PREFIX = "+"

# ── Scanning functions ────────────────────────────────────────────────────────

def check_path(path_str):
    """Return list of (level, description) for risky path patterns."""
    findings = []
    p = path_str.replace("\\", "/").lower()
    if p in ALLOWED_INTERNAL_PATHS:
        return findings
    for pattern, description in BLOCK_PATH_PATTERNS:
        if re.search(pattern, p, re.IGNORECASE):
            findings.append(("BLOCK", description, path_str))
    for pattern, description in WARN_PATH_PATTERNS:
        if re.search(pattern, p, re.IGNORECASE):
            findings.append(("WARN", description, path_str))
    return findings


def check_content_line(line, location):
    """Return list of (level, description, location) for risky content patterns."""
    findings = []
    for pattern, description in BLOCK_CONTENT_PATTERNS:
        if re.search(pattern, line):
            findings.append(("BLOCK", description, location))
    for pattern, description in WARN_CONTENT_PATTERNS:
        if re.search(pattern, line):
            findings.append(("WARN", description, location))
    return findings


def scan_diff_text(diff_text):
    """Scan a unified diff string. Returns list of (level, description, location)."""
    findings = []
    current_file = None
    line_number = 0

    for raw_line in diff_text.splitlines():
        # Track which file we're in
        if raw_line.startswith("+++ b/") or raw_line.startswith("+++ "):
            current_file = raw_line[6:] if raw_line.startswith("+++ b/") else raw_line[4:]
            findings.extend(check_path(current_file))
            line_number = 0
        elif raw_line.startswith("@@ "):
            # Extract new-file starting line number from hunk header
            m = re.search(r"\+(\d+)", raw_line)
            if m:
                line_number = int(m.group(1)) - 1
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            line_number += 1
            location = f"{current_file}:{line_number}" if current_file else f"line {line_number}"
            findings.extend(check_content_line(raw_line[1:], location))
        elif not raw_line.startswith("-"):
            line_number += 1

    return findings


def scan_file(filepath):
    """Scan a single file's full contents. Returns list of (level, description, location)."""
    findings = list(check_path(str(filepath)))

    # When this script scans itself, lines inside BLOCK_CONTENT_PATTERNS and
    # WARN_CONTENT_PATTERNS intentionally contain the regex patterns the scanner
    # detects. They are rule definitions, not real secrets or risky statements,
    # so they are exempt from content checks.
    is_self = (Path(filepath).resolve() == Path(__file__).resolve())

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            in_pattern_block = False
            for i, line in enumerate(f, start=1):
                if is_self:
                    stripped = line.strip()
                    if re.search(r'(BLOCK|WARN)_CONTENT_PATTERNS\s*=\s*\[', stripped):
                        in_pattern_block = True
                    elif in_pattern_block and stripped == "]":
                        in_pattern_block = False
                    if in_pattern_block:
                        continue  # pattern definitions are exempt from self-scan
                location = f"{filepath}:{i}"
                findings.extend(check_content_line(line, location))
    except (OSError, PermissionError) as e:
        findings.append(("WARN", f"Could not read file: {e}", str(filepath)))
    return findings


def get_git_diff(staged=False):
    """Run git diff and return the output as a string."""
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--staged")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running git diff: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("git not found. Make sure git is installed and in your PATH.", file=sys.stderr)
        sys.exit(1)


def get_git_tracked_files():
    """Return list of tracked files in the working tree."""
    try:
        result = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True
        )
        return [f for f in result.stdout.splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error listing git files: {e}", file=sys.stderr)
        sys.exit(1)


# ── Reporting ─────────────────────────────────────────────────────────────────

def deduplicate(findings):
    seen = set()
    result = []
    for item in findings:
        key = (item[0], item[1], item[2])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def print_report(findings, source_description):
    blocks = [(d, loc) for lvl, d, loc in findings if lvl == "BLOCK"]
    warns  = [(d, loc) for lvl, d, loc in findings if lvl == "WARN"]

    print()
    print("=" * 60)
    print("  Special Agent Ops — Safety Gate")
    print(f"  Source: {source_description}")
    print("=" * 60)

    if blocks:
        print("\n  RESULT: BLOCK")
        print("  The following issues must be resolved before opening a PR.\n")
        for desc, loc in blocks:
            print(f"  [BLOCK] {desc}")
            print(f"          {loc}")
    elif warns:
        print("\n  RESULT: WARN")
        print("  Review the following before proceeding.\n")
    else:
        print("\n  RESULT: PASS")
        print("  No risky patterns detected.\n")

    if warns:
        print("\n  Warnings:")
        for desc, loc in warns:
            print(f"  [WARN]  {desc}")
            print(f"          {loc}")

    if not blocks and not warns:
        print("  All clear. Proceed to human PR review.\n")

    print()
    print("=" * 60)

    if blocks:
        print("  ACTION REQUIRED: Do not open the PR.")
        print("  If a leaked value was found, revoke the credential immediately,")
        print("  then remove it from the code and history.")
    elif warns:
        print("  ACTION: Review each WARN item. Accept or resolve before merging.")

    print("=" * 60)
    print()

    return "BLOCK" if blocks else ("WARN" if warns else "PASS")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Special Agent Ops — Safety Gate Scanner"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diff",      action="store_true",
                       help="Scan git diff (unstaged changes)")
    group.add_argument("--staged",    action="store_true",
                       help="Scan staged changes (git diff --staged)")
    group.add_argument("--tree",      action="store_true",
                       help="Scan all git-tracked files in the working tree")
    group.add_argument("--file",      metavar="PATH",
                       help="Scan a specific file")
    group.add_argument("--diff-file", metavar="PATH",
                       help="Scan a saved diff file")

    args = parser.parse_args()

    findings = []

    if args.diff:
        diff_text = get_git_diff(staged=False)
        if not diff_text.strip():
            print("No unstaged changes found. Try --staged or --tree.")
            sys.exit(0)
        findings = scan_diff_text(diff_text)
        source = "git diff (unstaged)"

    elif args.staged:
        diff_text = get_git_diff(staged=True)
        if not diff_text.strip():
            print("No staged changes found. Stage some files first with git add.")
            sys.exit(0)
        findings = scan_diff_text(diff_text)
        source = "git diff --staged"

    elif args.tree:
        tracked = get_git_tracked_files()
        for fpath in tracked:
            if Path(fpath).is_file():
                findings.extend(scan_file(fpath))
        source = f"working tree ({len(tracked)} tracked files)"

    elif args.file:
        fpath = Path(args.file)
        if not fpath.exists():
            print(f"File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        findings = scan_file(fpath)
        source = str(args.file)

    elif args.diff_file:
        fpath = Path(args.diff_file)
        if not fpath.exists():
            print(f"Diff file not found: {args.diff_file}", file=sys.stderr)
            sys.exit(1)
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            diff_text = f.read()
        findings = scan_diff_text(diff_text)
        source = str(args.diff_file)

    findings = deduplicate(findings)
    result = print_report(findings, source)

    # Exit code: 0 = PASS, 1 = WARN, 2 = BLOCK
    if result == "BLOCK":
        sys.exit(2)
    elif result == "WARN":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
