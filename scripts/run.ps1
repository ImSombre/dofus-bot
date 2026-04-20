# Launch Dofus Bot (GUI).
# Usage:  pwsh .\scripts\run.ps1   (or double-click the desktop shortcut)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $projectRoot

if (-not (Test-Path ".venv")) {
    Write-Host "venv manquant. Lance d'abord .\scripts\install.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".env")) {
    Write-Host ".env manquant. Copie .env.example vers .env ou relance install.ps1" -ForegroundColor Red
    exit 1
}

& ".\.venv\Scripts\python.exe" -m src.main @args
