<#
.SYNOPSIS
Reports likely secrets in staged changes or tracked files.

.DESCRIPTION
Special Agent Ops secret scanner for Windows / PowerShell workflows.
The script reports likely credentials and risky secret file names, then exits
nonzero when findings are present. It never deletes, modifies, commits, or
rotates anything.

.PARAMETER All
Scan all git-tracked files in the working tree.

.PARAMETER File
Scan one specific file.

.EXAMPLE
pwsh scripts/check-secrets.ps1

Scan staged files and the staged diff.

.EXAMPLE
pwsh scripts/check-secrets.ps1 -All

Scan all tracked files.

.EXAMPLE
pwsh scripts/check-secrets.ps1 -File README.md

Scan one file.
#>

# check-secrets.ps1 — Special Agent Ops
#
# Simple secret scanner for Windows / PowerShell.
# Scans staged files and the staged git diff for likely credentials.
# Reports only. Never deletes, modifies, or commits anything.
#
# Usage:
#   pwsh scripts/check-secrets.ps1              # scan staged files + diff
#   pwsh scripts/check-secrets.ps1 -All         # scan all tracked files
#   pwsh scripts/check-secrets.ps1 -File PATH   # scan a specific file
#
# Requirements: PowerShell 5.1+ or PowerShell 7+, git in PATH

[CmdletBinding()]
param(
    [switch]$All,
    [string]$File
)

$AlertCount = 0
$WarnCount  = 0

# ── Pattern definitions ───────────────────────────────────────────────────────

# The TODO pattern is constructed from two variables so that this scanner file
# does not trigger its own TODO-near-sensitive-term rule on a single line.
$_todoBase   = "(?i)TODO.*"
$_todoTerms  = "(password|secret|credential|api.key)"
$_todoPattern = $_todoBase + $_todoTerms

# Each hashtable: Level, Description, Pattern (regex)
$ContentPatterns = @(
    @{ Level="ALERT"; Description="AWS access key ID";                     Pattern="AKIA[A-Z0-9]{16}" }
    @{ Level="ALERT"; Description="OpenAI/Stripe secret key (sk-)";        Pattern="sk-[A-Za-z0-9]{20,}" }
    @{ Level="ALERT"; Description="Stripe live publishable key";           Pattern="pk_live_[A-Za-z0-9]{20,}" }
    @{ Level="ALERT"; Description="Stripe live restricted key";            Pattern="rk_live_[A-Za-z0-9]{20,}" }
    @{ Level="ALERT"; Description="Google API key (AIza...)";              Pattern="AIza[A-Za-z0-9_\-]{35}" }
    @{ Level="ALERT"; Description="GitHub personal access token (ghp_)";  Pattern="ghp_[A-Za-z0-9]{36,}" }
    @{ Level="ALERT"; Description="GitHub Actions secret token (ghs_)";   Pattern="ghs_[A-Za-z0-9]{36,}" }
    @{ Level="ALERT"; Description="GitLab personal access token";         Pattern="glpat-[A-Za-z0-9_\-]{20,}" }
    @{ Level="ALERT"; Description="Slack token (xox...)";                 Pattern="xox[bpars]-[A-Za-z0-9\-]+" }
    @{ Level="ALERT"; Description="Google OAuth token (ya29.)";           Pattern="ya29\.[A-Za-z0-9_\-]+" }
    @{ Level="ALERT"; Description="SSH private key material";             Pattern="-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----" }
    @{ Level="ALERT"; Description="Database connection string with password"; Pattern="[a-z]+://[^:]+:[^@]{4,}@" }
    @{ Level="ALERT"; Description="Hardcoded password/secret assignment"; Pattern="(?i)(password|passwd|secret)\s*=\s*['""][^'""]{6,}['""]" }
    @{ Level="ALERT"; Description="Hardcoded API key assignment";         Pattern="(?i)(api_key|apikey|access_token|auth_token)\s*=\s*['""][^'""]{8,}['""]" }
    @{ Level="WARN";  Description="Possible JWT token";                   Pattern="eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}" }
    @{ Level="WARN";  Description="Possible base64-encoded secret";       Pattern="['""][A-Za-z0-9+/]{40,}={0,2}['""]" }
    @{ Level="WARN";  Description="Sensitive TODO marker";                 Pattern=$_todoPattern }
)

$RiskyFilePatterns = @(
    "\.env$"
    "\.env\."
    "credentials\.(json|yaml|yml)$"
    "service.account\.json$"
    "secret.*\.(json|yaml|yml)$"
    "\.pem$"
    "\.key$"
    "id_rsa$"
    "id_ed25519$"
)

# ── Output helpers ────────────────────────────────────────────────────────────

function Write-Alert {
    param([string]$Description, [string]$Location)
    Write-Host "  [ALERT] $Description" -ForegroundColor Red
    Write-Host "          $Location"
    $script:AlertCount++
}

function Write-Warn {
    param([string]$Description, [string]$Location)
    Write-Host "  [WARN]  $Description" -ForegroundColor Yellow
    Write-Host "          $Location"
    $script:WarnCount++
}

function Test-ScannerPatternDefinitionLine {
    param(
        [string]$SourceLabel,
        [string]$Line,
        [bool]$InPythonContentPatternBlock = $false
    )

    $normalizedPath = $SourceLabel -replace "\\", "/"
    if ($normalizedPath -notmatch "(^|/)scripts/(check-secrets\.ps1|check-secrets\.sh|safety-gate\.py)$") {
        return $false
    }

    $trimmed = $Line.TrimStart()

    if ($normalizedPath -match "(^|/)scripts/check-secrets\.ps1$") {
        return (
            $trimmed -match '^\$_todo(Base|Terms|Pattern)\s*=' -or
            $trimmed -match '^@\{\s*Level=.*;\s*Description=.*;\s*Pattern='
        )
    }

    if ($normalizedPath -match "(^|/)scripts/check-secrets\.sh$") {
        return (
            $trimmed -match '^_todo_(base|terms)=' -or
            $trimmed -match '^"(ALERT|WARN)\|'
        )
    }

    return ($InPythonContentPatternBlock -and $trimmed -match '^\(r?["'']')
}

function Remove-SelfScanPatternDefinitionLines {
    param([string]$Content, [string]$SourceLabel)

    $normalizedPath = $SourceLabel -replace "\\", "/"
    $inPythonContentPatternBlock = $false
    $lines = $Content -split '\r?\n'
    $filtered = foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($normalizedPath -match "(^|/)scripts/safety-gate\.py$") {
            if ($trimmed -match "^(BLOCK|WARN)_CONTENT_PATTERNS\s*=\s*\[") {
                $inPythonContentPatternBlock = $true
            } elseif ($inPythonContentPatternBlock -and $trimmed -eq "]") {
                $inPythonContentPatternBlock = $false
            }
        }

        if (Test-ScannerPatternDefinitionLine -SourceLabel $SourceLabel -Line $line -InPythonContentPatternBlock $inPythonContentPatternBlock) {
            ""
        } else {
            $line
        }
    }
    return ($filtered -join "`n")
}

function Write-Header {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Special Agent Ops -- Secrets Check (PowerShell)" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Footer {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    if ($script:AlertCount -gt 0) {
        Write-Host "  RESULT: ALERT -- $($script:AlertCount) definite secret(s) found" -ForegroundColor Red
        Write-Host ""
        Write-Host "  DO NOT push or open a PR." -ForegroundColor Red
        Write-Host "  If these are real credentials, REVOKE THEM IMMEDIATELY" -ForegroundColor Red
        Write-Host "  before cleaning the code or history." -ForegroundColor Red
    } elseif ($script:WarnCount -gt 0) {
        Write-Host "  RESULT: WARN -- $($script:WarnCount) item(s) to review" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Review each warning before pushing."
    } else {
        Write-Host "  RESULT: CLEAN -- no secrets detected" -ForegroundColor Green
        Write-Host ""
        Write-Host "  No obvious secrets found. Proceed with your PR."
    }
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

# ── Scanning functions ────────────────────────────────────────────────────────

function Test-RiskyFilePath {
    param([string]$FilePath)
    foreach ($pattern in $RiskyFilePatterns) {
        if ($FilePath -match $pattern) {
            Write-Alert "Risky file type: $FilePath" "This file type should not be committed."
        }
    }
}

function Invoke-ContentScan {
    param([string]$Content, [string]$SourceLabel)

    $Content = Remove-SelfScanPatternDefinitionLines -Content $Content -SourceLabel $SourceLabel

    foreach ($entry in $ContentPatterns) {
        $matches = [regex]::Matches($Content, $entry.Pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($matches.Count -gt 0) {
            $matchDesc = "match found in: $SourceLabel"
            if ($entry.Level -eq "ALERT") {
                Write-Alert $entry.Description $matchDesc
            } else {
                Write-Warn $entry.Description $matchDesc
            }
        }
    }
}

function Invoke-FileScan {
    param([string]$FilePath)

    if (-not (Test-Path $FilePath -PathType Leaf)) {
        Write-Host "  Skipping (not found): $FilePath"
        return
    }

    Test-RiskyFilePath $FilePath

    try {
        $content = Get-Content -Path $FilePath -Raw -Encoding UTF8 -ErrorAction Stop
        if ($content) {
            Invoke-ContentScan -Content $content -SourceLabel $FilePath
        }
    } catch {
        Write-Warn "Could not read file: $FilePath" $_.Exception.Message
    }
}

function Assert-GitRepo {
    $null = git rev-parse --git-dir 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Not a git repository. Run this script from within your project." -ForegroundColor Red
        exit 1
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────

Write-Header

if ($File) {
    # Scan a specific file
    Write-Host "  Scanning file: $File"
    Write-Host ""
    Invoke-FileScan -FilePath $File

} elseif ($All) {
    # Scan all tracked files
    Assert-GitRepo
    Write-Host "  Scanning all git-tracked files..."
    Write-Host ""
    $trackedFiles = git ls-files 2>$null
    foreach ($f in $trackedFiles) {
        if (Test-Path $f -PathType Leaf) {
            Invoke-FileScan -FilePath $f
        }
    }

} else {
    # Default: scan staged files + staged diff
    Assert-GitRepo
    Write-Host "  Scanning staged files and diff..."
    Write-Host ""

    # Check staged file names
    $stagedFiles = git diff --staged --name-only 2>$null
    if ($stagedFiles) {
        foreach ($f in $stagedFiles) {
            if ($f) { Test-RiskyFilePath $f }
        }
    }

    # Check staged diff content
    $stagedDiff = git diff --staged 2>$null
    if ($stagedDiff) {
        $diffText = $stagedDiff -join "`n"
        Invoke-ContentScan -Content $diffText -SourceLabel "[staged diff]"
    } else {
        Write-Host "  No staged changes found."
        Write-Host "  Run 'git add' to stage files, or use -All to scan everything."
    }
}

Write-Footer

if ($AlertCount -gt 0) {
    exit 2
} elseif ($WarnCount -gt 0) {
    exit 1
} else {
    exit 0
}
