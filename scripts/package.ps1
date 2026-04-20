# ============================================================
# Dofus Bot — packaging script
# Cree un zip propre du projet pour deployer sur un autre PC.
# Usage : pwsh .\scripts\package.ps1
# ============================================================

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $projectRoot

$version = "0.1.0"
$tmpDir = Join-Path $env:TEMP "dofus-bot-package-$(Get-Random)"
$outName = "dofus-bot-installer-v$version.zip"
$outPath = Join-Path ([Environment]::GetFolderPath("Desktop")) $outName

Write-Host "=== Packaging Dofus Bot v$version ===" -ForegroundColor Cyan

# Clean copy (exclude runtime/local files)
$excludes = @(
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "*.pyc",
    ".env",
    ".git",
    ".claude",
    "logs",
    "screenshots",
    "data\bot.sqlite3",
    "data\tessdata",         # redownloaded by install.ps1
    "data\calibration",      # regenerated per PC
    "data\templates",        # regenerated per PC
    "htmlcov"
)

Write-Host "  Preparing staging dir: $tmpDir" -ForegroundColor Gray
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# Use robocopy for reliable exclude
$stageRoot = Join-Path $tmpDir "dofus-bot"
$roboExcludes = @()
foreach ($ex in $excludes) {
    if ($ex -like "*\*") { $roboExcludes += "/XF"; $roboExcludes += $ex }
    else { $roboExcludes += "/XD"; $roboExcludes += $ex }
}
# Simpler: use Copy-Item with exclusion
robocopy $projectRoot $stageRoot /E /NFL /NDL /NJH /NJS /NP `
    /XD .venv __pycache__ .pytest_cache .mypy_cache .ruff_cache .git .claude logs screenshots htmlcov `
    /XD "$projectRoot\data\tessdata" "$projectRoot\data\calibration" "$projectRoot\data\templates" `
    /XF .env .coverage *.pyc "$projectRoot\data\bot.sqlite3" | Out-Null

# Include empty placeholders
@("data", "logs", "screenshots") | ForEach-Object {
    $p = Join-Path $stageRoot $_
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
    "# placeholder" | Out-File -FilePath (Join-Path $p ".gitkeep") -Encoding utf8
}

# Write INSTALL.txt at the root for clarity
$installTxt = @"
========================================
   Dofus Bot - Installation (Windows)
========================================

INSTALLATION EN 2 ETAPES - AUCUNE COMMANDE A TAPER

1. Dezippe tout le contenu de ce zip dans un dossier stable
   (ex: C:\DofusBot\).

2. DOUBLE-CLIC sur INSTALLER.bat (a la racine).

   Windows va demander les droits admin (fenetre UAC bleue).
   Clique "Oui".

C'est tout. Le script fait le reste :
   - Installe Python 3.12 si besoin
   - Installe Tesseract OCR
   - Installe LM Studio + Ollama en arriere-plan
   - Cree le venv + les dependances
   - Cree des raccourcis Bureau et Menu Demarrer
   - LANCE le bot automatiquement a la fin

Apres l'install, pour relancer le bot :
   - Double-clic sur "Dofus Bot" sur ton Bureau
   - OU tape "Dofus Bot" dans la recherche Windows

Raccourcis en jeu :
   - F1 = arret d'urgence
   - Echap = fermer popup

En cas de probleme, consulte docs\DEPLOYMENT.md.
"@
Set-Content -Path (Join-Path $stageRoot "INSTALL.txt") -Value $installTxt -Encoding UTF8

# Create zip
Write-Host "  Compression..." -ForegroundColor Gray
if (Test-Path $outPath) { Remove-Item $outPath -Force }
Compress-Archive -Path (Join-Path $tmpDir "dofus-bot") -DestinationPath $outPath -CompressionLevel Optimal

# Cleanup
Remove-Item $tmpDir -Recurse -Force

$size = [math]::Round((Get-Item $outPath).Length / 1MB, 1)
Write-Host ""
Write-Host "  [OK] Package cree :" -ForegroundColor Green
Write-Host "       $outPath" -ForegroundColor White
Write-Host "       Taille : $size MB" -ForegroundColor Gray
Write-Host ""
Write-Host "Transfere ce zip sur le PC cible et suis INSTALL.txt dedans." -ForegroundColor Cyan
