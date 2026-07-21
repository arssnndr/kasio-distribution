# ============================================================================
# KASIO Plugin + Skill Installer for Hermes Agent (Windows PowerShell)
# ============================================================================
# Usage:
#   .\install-kasio.ps1
#   irm https://raw.githubusercontent.com/arssnndr/kasio-distribution/main/install-kasio.ps1 | iex
#
# Behavior: Install plugin `kasio-notion` dan skill `kasio` ke profile Hermes
# yang SEDANG AKTIF. Tidak membuat profile baru. Aman dipakai berulang-ulang
# (idempotent) — kalau sudah terpasang, di-skip atau di-update.
# ============================================================================

$ErrorActionPreference = "Stop"

$Repo = if ($env:KASIO_REPO) { $env:KASIO_REPO } else { "github.com/arssnndr/kasio-distribution" }
$Branch = if ($env:KASIO_BRANCH) { $env:KASIO_BRANCH } else { "main" }
$PluginName = "kasio-notion"
$SkillName = "kasio"

function Write-Section($text) { Write-Host "[$text]" -ForegroundColor Cyan }
function Write-OK($text) { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Err($text) { Write-Host "  [ERR] $text" -ForegroundColor Red }
function Write-Warn($text) { Write-Host "  [WARN] $text" -ForegroundColor Yellow }
function Write-Skip($text) { Write-Host "  [SKIP] $text" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " KASIO - Plugin & Skill Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Source : $Repo @ $Branch" -ForegroundColor Gray
Write-Host "  Target : active Hermes profile (tidak buat profile baru)" -ForegroundColor Gray
Write-Host ""

# 1. Verify Hermes
Write-Section "1/5 Checking Hermes installation..."
$hermes = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermes) {
    Write-Err "Hermes not found"
    Write-Host "  Install dulu:"
    Write-Host "    iex (irm https://hermes-agent.nousresearch.com/install.ps1)"
    exit 1
}
Write-OK "Hermes found at $($hermes.Source)"

# Detect active profile (read-only, untuk info)
$ActiveProfile = (& hermes profile list 2>$null | Select-String "^\*\s+" | Select-Object -First 1) -replace "^\*\s+","" -replace "\s.*$",""
if (-not $ActiveProfile) { $ActiveProfile = "(unknown)" }
Write-OK "Active profile: $ActiveProfile"

# 2. Resolve Hermes dirs
Write-Section "2/5 Resolving target directories..."
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }
if (-not (Test-Path $HermesHome)) {
    Write-Err "Hermes home not found: $HermesHome"
    exit 1
}
$PluginsDir = Join-Path $HermesHome "plugins"
$SkillsDir  = Join-Path $HermesHome "skills"
New-Item -ItemType Directory -Path $PluginsDir -Force | Out-Null
New-Item -ItemType Directory -Path $SkillsDir  -Force | Out-Null
Write-OK "Plugins dir: $PluginsDir"
Write-OK "Skills dir : $SkillsDir"

# 3. Download distribution (zipball) ke temp, extract plugin + skill
Write-Section "3/5 Downloading distribution from $Repo ..."
$TmpRoot = Join-Path $env:TEMP "kasio-install-$PID"
New-Item -ItemType Directory -Path $TmpRoot -Force | Out-Null
$ZipUrl = "https://codeload.github.com/$($Repo -replace '^github.com/','')/zip/refs/heads/$Branch"
$ZipPath = Join-Path $TmpRoot "kasio.zip"
try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing -ErrorAction Stop
    Write-OK "Downloaded $([math]::Round((Get-Item $ZipPath).Length / 1KB, 1)) KB"
} catch {
    Write-Err "Download failed: $_"
    Write-Host "  URL: $ZipUrl"
    exit 1
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $TmpRoot)
$ExtractedRoot = Get-ChildItem -Path $TmpRoot -Directory | Where-Object { $_.Name -like "kasio-distribution-*" } | Select-Object -First 1
if (-not $ExtractedRoot) {
    Write-Err "Extracted zip doesn't contain kasio-distribution-* folder"
    exit 1
}
Write-OK "Extracted to $($ExtractedRoot.Name)"

# 4. Install plugin
Write-Section "4/5 Installing plugin '$PluginName' ..."
$SrcPlugin = Join-Path $ExtractedRoot.FullName "plugins\$PluginName"
$DstPlugin = Join-Path $PluginsDir $PluginName
if (-not (Test-Path $SrcPlugin)) {
    Write-Err "Plugin source not found in zip: $SrcPlugin"
    exit 1
}

if (Test-Path $DstPlugin) {
    # Backup existing
    $Backup = "$DstPlugin.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Move-Item -LiteralPath $DstPlugin -Destination $Backup -Force
    Write-Warn "Existing plugin backed up to $Backup"
}
Copy-Item -LiteralPath $SrcPlugin -Destination $DstPlugin -Recurse -Force

# Clean read-only attributes (umum di Windows)
Get-ChildItem -LiteralPath $DstPlugin -Recurse -File -ErrorAction SilentlyContinue |
    ForEach-Object { $_.IsReadOnly = $false }
# Hapus __pycache__ + .git dari extracted copy (bukan bagian runtime)
Remove-Item -LiteralPath (Join-Path $DstPlugin "__pycache__") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $DstPlugin ".git")         -Recurse -Force -ErrorAction SilentlyContinue

# Enable plugin di runtime
$pluginList = & hermes plugins list 2>&1 | Out-String
if ($pluginList -match [regex]::Escape($PluginName)) {
    Write-OK "Plugin $PluginName installed and registered"
} else {
    Write-Warn "Plugin $PluginName copied but belum muncul di 'hermes plugins list'"
    Write-Host "         Mungkin perlu restart Hermes session untuk auto-load."
}

# 5. Install skill
Write-Section "5/5 Installing skill '$SkillName' ..."
$SrcSkill = Join-Path $ExtractedRoot.FullName "skills\$SkillName"
$DstSkill = Join-Path $SkillsDir $SkillName
if (-not (Test-Path $SrcSkill)) {
    Write-Err "Skill source not found in zip: $SrcSkill"
    exit 1
}

if (Test-Path $DstSkill) {
    $Backup = "$DstSkill.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Move-Item -LiteralPath $DstSkill -Destination $Backup -Force
    Write-Warn "Existing skill backed up to $Backup"
}
Copy-Item -LiteralPath $SrcSkill -Destination $DstSkill -Recurse -Force

$skillList = & hermes skills list 2>&1 | Out-String
if ($skillList -match [regex]::Escape($SkillName)) {
    Write-OK "Skill $SkillName installed and registered"
} else {
    Write-Warn "Skill $SkillName copied but belum muncul di 'hermes skills list'"
}

# Cleanup temp
Remove-Item -LiteralPath $TmpRoot -Recurse -Force -ErrorAction SilentlyContinue

# 6. Env setup
Write-Host ""
$EnvFile = Join-Path $HermesHome ".env"
if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

function Append-IfEmpty($key, $description) {
    $existing = if (Test-Path $EnvFile) {
        Select-String -Path $EnvFile -Pattern "^$key=.+" -Quiet -ErrorAction SilentlyContinue
    } else { $false }
    if ($existing) { Write-OK "$key (already set, skipping)"; return }
    Write-Host ""
    Write-Host "-> $key" -ForegroundColor Yellow
    Write-Host "  $description"
    $value = Read-Host "  Value"
    Add-Content -Path $EnvFile -Value "$key=$value"
}

Write-Section "Setup env (skip kalau sudah ada)..."
Append-IfEmpty "NOTION_API_KEY" "Notion integration key (https://www.notion.so/my-integrations)"
Append-IfEmpty "KASIO_TRANSACTIONS_DS_ID" "Notion data source ID untuk transactions DB"
Append-IfEmpty "KASIO_ACCOUNTS_DS_ID" "Notion data source ID untuk accounts DB"

$mmExisting = if (Test-Path $EnvFile) {
    Select-String -Path $EnvFile -Pattern "^MINIMAX_API_KEY=.+" -Quiet -ErrorAction SilentlyContinue
} else { $false }
if (-not $mmExisting) {
    Write-Host ""
    Write-Host "-> MINIMAX_API_KEY" -ForegroundColor Yellow
    Write-Host "  Optional - untuk vision reading foto struk"
    Write-Host "  Skip kalau tidak pakai (tekan Enter)"
    $mm_key = Read-Host "  Value (optional)"
    if ($mm_key) { Add-Content -Path $EnvFile -Value "MINIMAX_API_KEY=$mm_key" }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Done!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Installed to active profile (no new profile created)." -ForegroundColor Cyan
Write-Host ""
Write-Host "Cara pakai:" -ForegroundColor Cyan
Write-Host "  Chat bilang:" -ForegroundColor Gray
Write-Host '    "catat makan siang 35rb"' -ForegroundColor Yellow -NoNewline
Write-Host "   -> wizard catat transaksi"
Write-Host '    "cek saldo"' -ForegroundColor Yellow -NoNewline
Write-Host "                -> cek saldo rekening"
Write-Host '    "transfer 100rb dari cash ke seabank"' -ForegroundColor Yellow -NoNewline
Write-Host " -> transfer"
Write-Host ""
Write-Host "Update nanti: " -ForegroundColor Cyan -NoNewline
Write-Host ".\install-kasio.ps1" -ForegroundColor Yellow
Write-Host "Uninstall:    " -ForegroundColor Cyan -NoNewline
Write-Host "rm -rf $PluginsDir\$PluginName  $SkillsDir\$SkillName" -ForegroundColor Yellow
