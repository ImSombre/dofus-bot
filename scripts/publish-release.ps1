#Requires -Version 5.1
# ============================================================
# Dofus Bot - Publication d'une mise à jour sur GitHub
# Usage :
#   .\scripts\publish-release.ps1 "v0.1.2" "Description courte"
# ============================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$Version,
    [Parameter(Mandatory=$false)]
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $projectRoot

function Write-Step { param([string]$msg) Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Write-OK   { param([string]$msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$msg) Write-Host "  [XX] $msg" -ForegroundColor Red }

# Normalise la version (enleve le "v" eventuel)
$cleanVersion = $Version -replace '^v', ''
$tagName = "v$cleanVersion"

# Verifie gh CLI
$ghCmd = Get-Command gh -ErrorAction SilentlyContinue
if ($null -eq $ghCmd) {
    Write-Err "gh CLI non installe. Lance : winget install GitHub.cli"
    exit 1
}

# Verifie auth
$authCheck = & gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "gh non authentifie. Lance : gh auth login"
    exit 1
}

# Verifie git repo
if (-not (Test-Path ".git")) {
    Write-Warn "Pas encore un repo git. Initialisation..."
    & git init
    & git branch -M main
}

# Update VERSION file
Write-Step "Mise a jour du fichier VERSION"
Set-Content -Path "VERSION" -Value $cleanVersion -Encoding UTF8 -NoNewline
Write-OK "VERSION = $cleanVersion"

# Stage all + commit
Write-Step "Git commit"
& git add -A
$commitMsg = "Release $tagName"
if ($Notes) { $commitMsg += " - $Notes" }
& git commit -m $commitMsg
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Aucun changement a commit (peut-etre deja fait)"
}

# Push
Write-Step "Push vers GitHub"
& git push origin main 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Push echoue. Le repo n'est peut-etre pas encore cree."
    Write-Host "  Cree-le avec : gh repo create dofus-bot --public --source=. --push" -ForegroundColor Gray
    exit 1
}
Write-OK "Code pushe"

# Build le zip
Write-Step "Build du zip"
& pwsh -ExecutionPolicy Bypass -File ".\scripts\package.ps1"
$zipPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "dofus-bot-installer-v0.1.0.zip"
if (-not (Test-Path $zipPath)) {
    Write-Err "Zip introuvable : $zipPath"
    exit 1
}
# Renomme avec la bonne version
$finalZip = Join-Path ([Environment]::GetFolderPath("Desktop")) "dofus-bot-$tagName.zip"
Copy-Item $zipPath $finalZip -Force
Write-OK "Zip pret : $finalZip"

# Cree la release
Write-Step "Creation de la release GitHub"
$releaseNotes = "Mise a jour $tagName"
if ($Notes) { $releaseNotes += "`n`n$Notes" }
& gh release create $tagName $finalZip --title "$tagName" --notes $releaseNotes
if ($LASTEXITCODE -ne 0) {
    Write-Err "Creation release echouee"
    exit 1
}
Write-OK "Release $tagName publiee"

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "   Release $tagName publiee avec succes !" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Les utilisateurs du bot verront la mise a jour au prochain demarrage." -ForegroundColor Cyan
