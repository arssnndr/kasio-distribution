# ============================================================================
# KASIO One-Shot Installer for Hermes Agent (Windows PowerShell)
# ============================================================================
# Usage:
#   .\install-kasio.ps1
#   irm https://raw.githubusercontent.com/arssnndr/kasio-distribution/main/install-kasio.ps1 | iex
# ============================================================================

$ErrorActionPreference = "Stop"

$Repo = if ($env:KASIO_REPO) { $env:KASIO_REPO } else { "github.com/arssnndr/kasio-distribution" }
$ProfileName = "kasio"

function Write-Section($text) { Write-Host "[$text]" -ForegroundColor Cyan }
function Write-OK($text) { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Err($text) { Write-Host "  [ERR] $text" -ForegroundColor Red }
function Write-Warn($text) { Write-Host "  [WARN] $text" -ForegroundColor Yellow }

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " KASIO - One-Shot Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify Hermes
Write-Section "1/6 Checking Hermes installation..."
$hermes = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermes) {
    Write-Err "Hermes not found"
    Write-Host "  Install dulu:"
    Write-Host "    iex (irm https://hermes-agent.nousresearch.com/install.ps1)"
    exit 1
}
Write-OK "Hermes found"

# 2. Install / update distribution
Write-Section "2/6 Installing kasio distribution..."

# Resolve Hermes home (needed to detect existing profile)
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }

function Clear-DistributionGitMetadata($ProfileDir) {
    # Hermes currently copies the clone's top-level .git into the profile.
    # Git pack files are read-only on Windows, making the next update fail with
    # WinError 5. Remove only git metadata; distribution.yaml keeps provenance,
    # while config.yaml, .env, memories, sessions, and auth stay intact.
    $ProfileGitDir = Join-Path $ProfileDir ".git"
    if (Test-Path $ProfileGitDir) {
        Get-ChildItem -LiteralPath $ProfileGitDir -Recurse -Force -File -ErrorAction SilentlyContinue |
            ForEach-Object { $_.IsReadOnly = $false }
        Remove-Item -LiteralPath $ProfileGitDir -Recurse -Force
        Write-OK "Stale .git metadata cleaned"
    }
}

# Detect: apakah profile sudah ada?
$ProfileDir = Join-Path $HermesHome "profiles\$ProfileName"
$existingProfile = Get-Item $ProfileDir -ErrorAction SilentlyContinue
$isUpdate = $null -ne $existingProfile

if ($isUpdate) {
    Write-Warn "Profile '$ProfileName' sudah ada - menyiapkan update Windows-safe"
    Clear-DistributionGitMetadata $ProfileDir

    hermes profile update $ProfileName --yes
    Clear-DistributionGitMetadata $ProfileDir
    Write-OK "Updated via 'profile update'"
} else {
    hermes profile install $Repo --name $ProfileName --yes
    Clear-DistributionGitMetadata $ProfileDir
}
Write-OK "Installed"

# 3. Env file path
$EnvFile = Join-Path $HermesHome "profiles\$ProfileName\.env"
Write-OK "Env file: $EnvFile"

# Ensure directory exists
$envDir = Split-Path $EnvFile -Parent
if (-not (Test-Path $envDir)) {
    New-Item -ItemType Directory -Path $envDir -Force | Out-Null
}

# Ensure .env file exists (hermes profile install doesn't auto-create it)
if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

# 4. Prompt for env values
Write-Section "3/6 Setup environment variables..."
Write-Host ""

function Append-IfEmpty($key, $description) {
    $existing = if (Test-Path $EnvFile) {
        Select-String -Path $EnvFile -Pattern "^$key=.+" -Quiet -ErrorAction SilentlyContinue
    } else { $false }
    if ($existing) {
        Write-OK "$key (already set, skipping)"
        return
    }
    Write-Host "-> $key" -ForegroundColor Yellow
    Write-Host "  $description"
    $value = Read-Host "  Value"
    Add-Content -Path $EnvFile -Value "$key=$value"
    Write-Host ""
}

Append-IfEmpty "NOTION_API_KEY" "Notion integration key (https://www.notion.so/my-integrations)"
Append-IfEmpty "KASIO_TRANSACTIONS_DS_ID" "Notion data source ID untuk transactions DB"
Append-IfEmpty "KASIO_ACCOUNTS_DS_ID" "Notion data source ID untuk accounts DB"

# Optional
$mmExisting = if (Test-Path $EnvFile) {
    Select-String -Path $EnvFile -Pattern "^MINIMAX_API_KEY=.+" -Quiet -ErrorAction SilentlyContinue
} else { $false }
if (-not $mmExisting) {
    Write-Host "-> MINIMAX_API_KEY" -ForegroundColor Yellow
    Write-Host "  Optional - untuk vision reading foto struk"
    Write-Host "  Skip kalau tidak pakai (tekan Enter)"
    $mm_key = Read-Host "  Value (optional)"
    if ($mm_key) {
        Add-Content -Path $EnvFile -Value "MINIMAX_API_KEY=$mm_key"
    }
}

# 5. Activate profile (use 'hermes profile use', not 'activate')
Write-Section "4/6 Activating kasio profile..."
hermes profile use $ProfileName --yes

# 6. Verify
Write-Section "5/6 Verifying installation..."
$pluginsOutput = hermes plugins list 2>&1 | Out-String
if ($pluginsOutput -match "kasio-notion") {
    Write-OK "Plugin kasio-notion loaded"
} else {
    Write-Err "Plugin not found"
}

$toolsOutput = hermes tools list 2>&1 | Out-String
if ($toolsOutput -match "kasio") {
    Write-OK "Tools registered"
} else {
    Write-Err "Tools missing"
}

$skillsOutput = hermes skills list 2>&1 | Out-String
if ($skillsOutput -match "kasio") {
    Write-OK "Skill loaded"
} else {
    Write-Err "Skill not found"
}

Write-Host ""
Write-Section "6/6 Done!"
Write-Host ""
Write-Host "Cara pakai:" -ForegroundColor Cyan
Write-Host "  Chat di Telegram/CLI/Desktop, bilang:"
Write-Host "    " -NoNewline
Write-Host '"catat makan siang 35rb"' -ForegroundColor Yellow -NoNewline
Write-Host "    -> wizard catat transaksi"
Write-Host "    " -NoNewline
Write-Host '"/saldo"' -ForegroundColor Yellow -NoNewline
Write-Host "                       -> cek saldo rekening"
Write-Host "    " -NoNewline
Write-Host '"transfer 100rb"' -ForegroundColor Yellow -NoNewline
Write-Host "               -> transfer antar rekening"
Write-Host "    " -NoNewline
Write-Host '"undo"' -ForegroundColor Yellow -NoNewline
Write-Host "                         -> batalkan transaksi (30s)"
Write-Host ""
Write-Host "Update nanti: " -ForegroundColor Cyan -NoNewline
Write-Host "hermes profile update kasio" -ForegroundColor Yellow
Write-Host "Uninstall:    " -ForegroundColor Cyan -NoNewline
Write-Host "hermes profile remove kasio" -ForegroundColor Yellow
