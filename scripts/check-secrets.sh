#!/usr/bin/env bash
# check-secrets.sh — Special Agent Ops
#
# Simple secret scanner. Scans staged files and the staged git diff for likely credentials.
# Reports only. Never deletes, modifies, or commits anything.
#
# Usage:
#   bash scripts/check-secrets.sh              # scan staged files + diff
#   bash scripts/check-secrets.sh --all        # scan all tracked files
#   bash scripts/check-secrets.sh --file PATH  # scan a specific file
#   bash scripts/check-secrets.sh --help       # show usage
#
# Requirements: bash, git, and common Unix tools (grep, sed, mktemp, file, cat)

set -euo pipefail

WARN_COUNT=0
ALERT_COUNT=0
SCRIPT_NAME="$(basename "$0")"

# ── Colours (disabled if not a terminal) ────────────────────────────────────

if [ -t 1 ]; then
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    GREEN='\033[0;32m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' YELLOW='' GREEN='' BOLD='' RESET=''
fi

# ── Patterns ─────────────────────────────────────────────────────────────────

# The TODO pattern is built from two variables so that this scanner file does
# not trigger its own TODO-near-sensitive-term rule on a single line.
_todo_base="TODO.*"
_todo_terms="(password|secret|credential|api.key)"

# Each entry: "LEVEL|DESCRIPTION|GREP_PATTERN"
# ALERT = definite secret, WARN = possible secret

PATTERNS=(
    "ALERT|AWS access key ID|AKIA[A-Z0-9]{16}"
    "ALERT|OpenAI/Stripe secret key (sk-)|sk-[A-Za-z0-9]{20,}"
    "ALERT|Stripe live publishable key|pk_live_[A-Za-z0-9]{20,}"
    "ALERT|Stripe live restricted key|rk_live_[A-Za-z0-9]{20,}"
    "ALERT|Google API key (AIza...)|AIza[A-Za-z0-9_-]{35}"
    "ALERT|GitHub personal access token (ghp_)|ghp_[A-Za-z0-9]{36,}"
    "ALERT|GitHub Actions secret token (ghs_)|ghs_[A-Za-z0-9]{36,}"
    "ALERT|GitLab personal access token|glpat-[A-Za-z0-9_-]{20,}"
    "ALERT|Slack token (xox...)|xox[bpars]-[A-Za-z0-9-]+"
    "ALERT|Google OAuth token (ya29.)|ya29\.[A-Za-z0-9_-]+"
    "ALERT|SSH private key|-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"
    "ALERT|Database connection string with password|[a-z]+://[^:]+:[^@]{4,}@"
    "ALERT|Hardcoded password assignment|(password|passwd|secret)\s*=\s*['\"][^'\"]{6,}['\"]"
    "ALERT|Hardcoded API key assignment|(api_key|apikey|access_token|auth_token)\s*=\s*['\"][^'\"]{8,}['\"]"
    "WARN|Possible JWT token|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
    "WARN|Possible base64-encoded secret|['\"][A-Za-z0-9+/]{40,}={0,2}['\"]"
    "WARN|Sensitive TODO marker|${_todo_base}${_todo_terms}"
)

# File patterns that should never be committed
RISKY_FILE_PATTERNS=(
    "\.env$"
    "\.env\."
    "credentials\.json"
    "credentials\.yaml"
    "credentials\.yml"
    "service.account\.json"
    "secret.*\.json"
    "secret.*\.yaml"
    "\.pem$"
    "\.key$"
    "id_rsa$"
    "id_ed25519$"
)

# ── Helper functions ──────────────────────────────────────────────────────────

alert() {
    echo -e "  ${RED}[ALERT]${RESET} $1"
    echo -e "          $2"
    ALERT_COUNT=$((ALERT_COUNT + 1))
}

warn() {
    echo -e "  ${YELLOW}[WARN] ${RESET} $1"
    echo -e "          $2"
    WARN_COUNT=$((WARN_COUNT + 1))
}

is_scanner_pattern_definition_line() {
    local file="$1"
    local line="$2"
    local in_python_content_patterns="${3:-0}"
    local trimmed="${line#"${line%%[![:space:]]*}"}"

    case "$file" in
        scripts/check-secrets.ps1|*/scripts/check-secrets.ps1)
            case "$trimmed" in
                \$_todoBase*|\$_todoTerms*|\$_todoPattern*|@\{*"Pattern="*) return 0 ;;
            esac
            ;;
        scripts/check-secrets.sh|*/scripts/check-secrets.sh)
            case "$trimmed" in
                _todo_base=*|_todo_terms=*|\"ALERT\|*|\"WARN\|*) return 0 ;;
            esac
            ;;
        scripts/safety-gate.py|*/scripts/safety-gate.py)
            [ "$in_python_content_patterns" -eq 1 ] || return 1
            case "$trimmed" in
                \(r\"*) return 0 ;;
            esac
            ;;
    esac

    return 1
}

write_filtered_content() {
    local file="$1"
    local content="$2"
    local line
    local trimmed
    local in_python_content_patterns=0

    while IFS= read -r line || [ -n "$line" ]; do
        trimmed="${line#"${line%%[![:space:]]*}"}"
        case "$file" in
            scripts/safety-gate.py|*/scripts/safety-gate.py)
                case "$trimmed" in
                    BLOCK_CONTENT_PATTERNS*=*\[|WARN_CONTENT_PATTERNS*=*\[) in_python_content_patterns=1 ;;
                    \]) [ "$in_python_content_patterns" -eq 1 ] && in_python_content_patterns=0 ;;
                esac
                ;;
        esac

        if is_scanner_pattern_definition_line "$file" "$line" "$in_python_content_patterns"; then
            printf '\n'
        else
            printf '%s\n' "$line"
        fi
    done <<< "$content"
}

check_file_patterns() {
    local file="$1"
    for pattern in "${RISKY_FILE_PATTERNS[@]}"; do
        if echo "$file" | grep -qE "$pattern" 2>/dev/null; then
            alert "Risky file type: $file" "This file type should not be committed."
        fi
    done
}

scan_content() {
    local file="$1"
    local content="$2"

    # Write content to a temp file for grep
    local tmpfile
    tmpfile="$(mktemp)"
    write_filtered_content "$file" "$content" > "$tmpfile"

    for entry in "${PATTERNS[@]}"; do
        local level desc pattern
        level="${entry%%|*}"
        rest="${entry#*|}"
        desc="${rest%%|*}"
        pattern="${rest#*|}"

        if grep -qE "$pattern" "$tmpfile" 2>/dev/null; then
            local match
            match="$(grep -nE "$pattern" "$tmpfile" | head -3 | sed 's/^/line /')"
            if [ "$level" = "ALERT" ]; then
                alert "$desc in: $file" "$match"
            else
                warn "$desc in: $file" "$match"
            fi
        fi
    done

    rm -f "$tmpfile"
}

scan_single_file() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo "  Skipping (not a regular file): $file"
        return
    fi

    check_file_patterns "$file"

    # Skip binary files
    if file "$file" 2>/dev/null | grep -q "binary"; then
        return
    fi

    local content
    content="$(cat "$file" 2>/dev/null || true)"
    if [ -n "$content" ]; then
        scan_content "$file" "$content"
    fi
}

# ── Main logic ────────────────────────────────────────────────────────────────

print_header() {
    echo ""
    echo -e "${BOLD}============================================================${RESET}"
    echo -e "${BOLD}  Special Agent Ops — Secrets Check${RESET}"
    echo -e "${BOLD}  Script: $SCRIPT_NAME${RESET}"
    echo -e "${BOLD}============================================================${RESET}"
    echo ""
}

print_usage() {
    cat <<'EOF'
Usage:
  bash scripts/check-secrets.sh
  bash scripts/check-secrets.sh --all
  bash scripts/check-secrets.sh --file PATH
  bash scripts/check-secrets.sh --help

Modes:
  default       Scan staged file names and staged diff content.
  --all         Scan all git-tracked files in the working tree.
  --file PATH   Scan one specific file.

Behavior:
  Reports likely credentials and risky secret file names.
  Exits nonzero when findings are present.
  Never deletes, modifies, commits, rotates, or uploads anything.
EOF
}

print_footer() {
    echo ""
    echo -e "${BOLD}============================================================${RESET}"
    if [ "$ALERT_COUNT" -gt 0 ]; then
        echo -e "  ${RED}RESULT: ALERT — $ALERT_COUNT definite secret(s) found${RESET}"
        echo ""
        echo -e "  ${RED}DO NOT push or open a PR.${RESET}"
        echo -e "  ${RED}If these are real credentials, REVOKE THEM IMMEDIATELY${RESET}"
        echo -e "  ${RED}before cleaning the code or history.${RESET}"
    elif [ "$WARN_COUNT" -gt 0 ]; then
        echo -e "  ${YELLOW}RESULT: WARN — $WARN_COUNT item(s) to review${RESET}"
        echo ""
        echo "  Review each warning before pushing."
    else
        echo -e "  ${GREEN}RESULT: CLEAN — no secrets detected${RESET}"
        echo ""
        echo "  No obvious secrets found. Proceed with your PR."
    fi
    echo -e "${BOLD}============================================================${RESET}"
    echo ""
}

main() {
    if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
        print_usage
        exit 0
    fi

    print_header

    local mode="${1:-}"
    local scan_file_arg="${2:-}"

    if [ "$mode" = "--file" ] && [ -n "$scan_file_arg" ]; then
        echo "  Scanning file: $scan_file_arg"
        echo ""
        scan_single_file "$scan_file_arg"

    elif [ "$mode" = "--all" ]; then
        echo "  Scanning all git-tracked files..."
        echo ""
        if ! git rev-parse --git-dir > /dev/null 2>&1; then
            echo "  Not a git repository. Run from within your project."
            exit 1
        fi
        while IFS= read -r file; do
            [ -f "$file" ] && scan_single_file "$file"
        done < <(git ls-files)

    else
        # Default: scan staged files + diff
        if ! git rev-parse --git-dir > /dev/null 2>&1; then
            echo "  Not a git repository. Run from within your project."
            exit 1
        fi

        echo "  Scanning staged files..."
        echo ""

        # Check staged file names
        while IFS= read -r file; do
            [ -n "$file" ] && check_file_patterns "$file"
        done < <(git diff --staged --name-only 2>/dev/null || true)

        # Check staged diff content
        local staged_diff
        staged_diff="$(git diff --staged 2>/dev/null || true)"
        if [ -n "$staged_diff" ]; then
            scan_content "[staged diff]" "$staged_diff"
        else
            echo "  No staged changes. Run 'git add' first, or use --all to scan everything."
        fi
    fi

    print_footer

    if [ "$ALERT_COUNT" -gt 0 ]; then
        exit 2
    elif [ "$WARN_COUNT" -gt 0 ]; then
        exit 1
    else
        exit 0
    fi
}

main "$@"
