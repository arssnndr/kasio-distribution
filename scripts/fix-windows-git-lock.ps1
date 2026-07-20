<#
.SYNOPSIS
    Fix Windows file lock issue preventing `hermes profile update` from succeeding.

.DESCRIPTION
    On Windows, git pack files in `.git/objects/pack/` may have the ReadOnly attribute
    set, which causes `shutil.rmtree()` (used by Hermes CLI's update_distribution) to
    fail with PermissionError: [WinError 5] Access is denied.

    This script clears the ReadOnly attribute recursively on the .git directory of
    the specified Hermes profile (default: kasio). After running this script,
    `hermes profile update <name>` should work.

.NOTES
    Related to commit 26dc0e7 (install-kasio.ps1 fallback to `profile install --force`).
    This script is the manual equivalent — try this BEFORE the --force fallback.

.PARAMETER ProfileName
    Name of the Hermes profile (default: 'kasio').

.PARAMETER HermesHome
    Path to Hermes home directory. Defaults to $env:LOCALAPPDATA.

.EXAMPLE
    .\fix-windows-git-lock.ps1
    # Fix the default kasio profile

.EXAMPLE
    .\fix-windows-git-lock.ps1 -ProfileName myprofile
    # Fix a different profile

.EXAMPLE
    .\fix-windows-git-lock.ps1 -HermesHome "D:\hermes"
    # Use custom Hermes home location

.LINK
    https://github.com/arssnndr/kasio-distribution
#>

[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$ProfileName = "kasio",

    [string]$HermesHome = $env:LOCALAPPDATA
)

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

# Hermes home is typically %LOCALAPPDATA%\hermes (Windows default)
$HermesProfiles = Join-Path $HermesHome "hermes\profiles"
if (-not (Test-Path $HermesProfiles)) {
    Write-Host "[ERROR] Hermes profiles directory not found: $HermesProfiles" -ForegroundColor Red
    exit 1
}

$GitPath = Join-Path $HermesProfiles "$ProfileName\.git"
if (-not (Test-Path $GitPath)) {
    Write-Host "[ERROR] Profile .git directory not found: $GitPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "Available profiles:" -ForegroundColor Yellow
    Get-ChildItem $HermesProfiles -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "  - $($_.Name)" }
    exit 1
}

Write-Host "================================================" -ForegroundColor Cyan
Write-Host " Hermes Profile Fix - Windows ReadOnly Unlock" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Profile:     $ProfileName"
Write-Host "Hermes home: $HermesProfiles"
Write-Host "Git path:    $GitPath"
Write-Host ""

# ---------------------------------------------------------------------------
# Step 1: Clear ReadOnly attribute on all files
# ---------------------------------------------------------------------------

Write-Host "[1/2] Clearing ReadOnly attribute on all files..." -ForegroundColor Yellow
$count = 0
try {
    $files = Get-ChildItem -Path $GitPath -Recurse -File -Force -ErrorAction SilentlyContinue
    foreach ($file in $files) {
        if ($file.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
            $file.Attributes = 'Normal'
            $count++
        }
    }
    Write-Host "  [OK] Cleared ReadOnly from $count file(s)" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Step 2: Verify writability
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[2/2] Verifying writability of pack files..." -ForegroundColor Yellow
$packDir = Join-Path $GitPath "objects\pack"
if (Test-Path $packDir) {
    $packFiles = @(Get-ChildItem $packDir -Filter "*.idx" -ErrorAction SilentlyContinue)
    if ($packFiles.Count -gt 0) {
        $allWritable = $true
        foreach ($file in $packFiles) {
            try {
                $stream = [System.IO.File]::OpenWrite($file.FullName)
                $stream.Close()
            } catch {
                Write-Host "  [FAIL] $($file.Name): $($_.Exception.Message)" -ForegroundColor Red
                $allWritable = $false
            }
        }
        if ($allWritable) {
            Write-Host "  [OK] All $($packFiles.Count) pack file(s) are writable" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Some files still locked - see errors above" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [INFO] No .idx pack files found (small repo)" -ForegroundColor Cyan
    }
} else {
    Write-Host "  [INFO] No pack directory (clean repo)" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# Final instructions
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host " Next Steps" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Now run:" -ForegroundColor Yellow
Write-Host "  hermes profile update $ProfileName --yes" -ForegroundColor Green
Write-Host ""
Write-Host "If still failing, alternatives:" -ForegroundColor Yellow
Write-Host "  1. Close any Hermes/agent processes that might hold the file"
Write-Host "  2. Use fallback: hermes profile install github.com/arssnndr/kasio-distribution --name $ProfileName --force --yes"
Write-Host "  3. Manually close VS Code, then retry"
Write-Host ""
