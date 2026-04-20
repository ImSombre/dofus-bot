#Requires -Version 5.1
# ============================================================
# Cree les raccourcis Dofus Bot (bureau + menu demarrer)
# Utilise pythonw.exe pour lancer sans fenetre console.
# ============================================================

$ErrorActionPreference = "Continue"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path

$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$iconPath = Join-Path $projectRoot "docs\icon.ico"

if (-not (Test-Path $pythonw)) {
    Write-Host "[ERREUR] pythonw.exe introuvable. Lance d'abord scripts\install.ps1" -ForegroundColor Red
    exit 1
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments,
        [string]$WorkingDir,
        [string]$IconLocation,
        [string]$Description
    )
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($Path)
        $sc.TargetPath = $Target
        $sc.Arguments = $Arguments
        $sc.WorkingDirectory = $WorkingDir
        if ($IconLocation -and (Test-Path $IconLocation)) {
            $sc.IconLocation = $IconLocation
        }
        $sc.Description = $Description
        $sc.WindowStyle = 7  # Minimized (pas de console visible)
        $sc.Save()
        Write-Host "  [OK] $Path" -ForegroundColor Green
    } catch {
        Write-Host "  [XX] Echec $Path : $_" -ForegroundColor Red
    }
}

Write-Host "=== Creation des raccourcis Dofus Bot ===" -ForegroundColor Cyan

# 1. Raccourci Bureau
$desktop = [Environment]::GetFolderPath("Desktop")
$desktopLnk = Join-Path $desktop "Dofus Bot.lnk"
New-Shortcut -Path $desktopLnk `
    -Target $pythonw `
    -Arguments "-m src.main" `
    -WorkingDir $projectRoot `
    -IconLocation $iconPath `
    -Description "Dofus 2.64 Bot (IA Gemini)"

# 2. Raccourci Menu Demarrer
$startMenu = [Environment]::GetFolderPath("StartMenu")
$startMenuPrograms = Join-Path $startMenu "Programs"
$startMenuLnk = Join-Path $startMenuPrograms "Dofus Bot.lnk"
New-Shortcut -Path $startMenuLnk `
    -Target $pythonw `
    -Arguments "-m src.main" `
    -WorkingDir $projectRoot `
    -IconLocation $iconPath `
    -Description "Dofus 2.64 Bot (IA Gemini)"

Write-Host ""
Write-Host "Raccourcis crees :" -ForegroundColor Cyan
Write-Host "  - Bureau : Dofus Bot.lnk (double-clic)" -ForegroundColor White
Write-Host "  - Menu Demarrer : Dofus Bot (tape 'Dofus Bot' dans la recherche Windows)" -ForegroundColor White
Write-Host ""
Write-Host "Conseil : clic droit sur le raccourci Bureau -> Epingler a la barre des taches" -ForegroundColor Gray
