# ============================================================================
# KASIO One-Shot Installer for Hermes Agent (Windows PowerShell)
# ============================================================================
# Usage:
#   .\install-kasio.ps1
#   irm https://raw.githubusercontent.com/arissunandar/kasio-distribution/main/install-kasio.ps1 | iex
# ============================================================================

$ErrorActionPreference = "Stop"

$Repo = if ($env:KASIO_REPO) { $env:KASIO_REPO } else { "github.com/arssnndr/kasio-distribution" }
$ProfileName = "kasio"

function Write-Section($text) { Write-Host "[$text]" -ForegroundColor Cyan }
function Write-OK($text) { Write-Host "  ✓ $text" -ForegroundColor Green }
function Write-Err($text) { Write-Host "  ✗ $text" -ForegroundColor Red }
function Write-Warn($text) { Write-Host "  ! $text" -ForegroundColor Yellow }

Write-Host ""
Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  KASIO — One-Shot Installer            ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Cyan
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

# 2. Install distribution
Write-Section "2/6 Installing kasio distribution..."
hermes profile install $Repo --name $ProfileName --yes
Write-OK "Installed"

# 3. Env file path
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }
$EnvFile = Join-Path $HermesHome "profiles\$ProfileName\.env"
Write-OK "Env file: $EnvFile"

# Ensure directory exists
$envDir = Split-Path $EnvFile -Parent
if (-not (Test-Path $envDir)) {
    New-Item -ItemType Directory -Path $envDir -Force | Out-Null
}

# 4. Prompt for env values
Write-Section "3/6 Setup environment variables..."
Write-Host ""

function Append-IfEmpty($key, $description) {
    if (Select-String -Path $EnvFile -Pattern "^$key=.+" -Quiet -ErrorAction SilentlyContinue) {
        Write-OK "$key (already set, skipping)"
        return
    }
    Write-Host "→ $key" -ForegroundColor Yellow
    Write-Host "  $description"
    $value = Read-Host "  Value"
    Add-Content -Path $EnvFile -Value "$key=$value"
    Write-Host ""
}

Append-IfEmpty "NOTION_API_KEY" "Notion integration key (https://www.notion.so/my-integrations)"
Append-IfEmpty "KASIO_TRANSACTIONS_DS_ID" "Notion data source ID untuk transactions DB"
Append-IfEmpty "KASIO_ACCOUNTS_DS_ID" "Notion data source ID untuk accounts DB"

# Optional
if (-not (Select-String -Path $EnvFile -Pattern "^MINIMAX_API_KEY=.+" -Quiet -ErrorAction SilentlyContinue)) {
    Write-Host "→ MINIMAX_API_KEY" -ForegroundColor Yellow
    Write-Host "  Optional — untuk vision reading foto struk"
    Write-Host "  Skip kalau tidak pakai (tekan Enter)"
    $mm_key = Read-Host "  Value (optional)"
    if ($mm_key) {
        Add-Content -Path $EnvFile -Value "MINIMAX_API_KEY=$mm_key"
    }
}

# 5. Activate profile
Write-Section "4/6 Activating kasio profile..."
hermes profile activate $ProfileName --yes

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
Write-Host "    → wizard catat transaksi"
Write-Host "    " -NoNewline
Write-Host '"/saldo"' -ForegroundColor Yellow -NoNewline
Write-Host "                       → cek saldo rekening"
Write-Host "    " -NoNewline
Write-Host '"transfer 100rb"' -ForegroundColor Yellow -NoNewline
Write-Host "               → transfer antar rekening"
Write-Host "    " -NoNewline
Write-Host '"undo"' -ForegroundColor Yellow -NoNewline
Write-Host "                         → batalkan transaksi (30s)"
Write-Host ""
Write-Host "Update nanti: " -ForegroundColor Cyan -NoNewline
Write-Host "hermes profile update kasio" -ForegroundColor Yellow
Write-Host "Uninstall:    " -ForegroundColor Cyan -NoNewline
Write-Host "hermes profile remove kasio" -ForegroundColor Yellow
