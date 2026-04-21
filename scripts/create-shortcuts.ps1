#Requires -Version 5.1
# ============================================================
# Cree les raccourcis Dofus Bot (bureau + menu demarrer) EN ADMIN.
# Pointe vers "Lancer Dofus Bot (Admin).bat" qui demande UAC.
# Le flag RunAsAdmin est active sur les .lnk pour Windows montre UAC.
# ============================================================

$ErrorActionPreference = "Continue"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path

$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$iconPath = Join-Path $projectRoot "docs\icon.ico"
$adminBat = Join-Path $projectRoot "Lancer Dofus Bot (Admin).bat"
$simpleBat = Join-Path $projectRoot "Lancer Dofus Bot.bat"

if (-not (Test-Path $pythonw)) {
    Write-Host "[ERREUR] pythonw.exe introuvable. Lance d'abord scripts\install.ps1" -ForegroundColor Red
    exit 1
}

# Target : .bat admin si existe, sinon pythonw direct
$targetBat = if (Test-Path $adminBat) { $adminBat } else { $simpleBat }

function New-AdminShortcut {
    param(
        [string]$Path,
        [string]$BatTarget,
        [string]$WorkingDir,
        [string]$IconLocation,
        [string]$Description
    )
    try {
        # Cree le .lnk
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($Path)
        if (Test-Path $BatTarget) {
            $sc.TargetPath = "cmd.exe"
            $sc.Arguments = "/c `"$BatTarget`""
        } else {
            # Fallback : pythonw direct
            $sc.TargetPath = $pythonw
            $sc.Arguments = "-m src.main"
        }
        $sc.WorkingDirectory = $WorkingDir
        if ($IconLocation -and (Test-Path $IconLocation)) {
            $sc.IconLocation = $IconLocation
        }
        $sc.Description = $Description
        $sc.WindowStyle = 7  # Minimized
        $sc.Save()

        # Active le flag RunAsAdmin sur le .lnk (byte 0x15, bit 0x20)
        # Windows affichera l'UAC au double-clic
        try {
            $bytes = [System.IO.File]::ReadAllBytes($Path)
            if ($bytes.Length -gt 0x15) {
                $bytes[0x15] = $bytes[0x15] -bor 0x20
                [System.IO.File]::WriteAllBytes($Path, $bytes)
                Write-Host "  [OK] $Path (flag admin active)" -ForegroundColor Green
            }
        } catch {
            Write-Host "  [!!] Flag admin non active : $_" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [XX] Echec $Path : $_" -ForegroundColor Red
    }
}

Write-Host "=== Creation des raccourcis Dofus Bot (admin) ===" -ForegroundColor Cyan

$desktop = [Environment]::GetFolderPath("Desktop")
New-AdminShortcut -Path (Join-Path $desktop "Dofus Bot.lnk") `
    -BatTarget $targetBat `
    -WorkingDir $projectRoot `
    -IconLocation $iconPath `
    -Description "Dofus 2.64 Bot (IA Gemini) - Admin"

$startMenuPrograms = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
New-AdminShortcut -Path (Join-Path $startMenuPrograms "Dofus Bot.lnk") `
    -BatTarget $targetBat `
    -WorkingDir $projectRoot `
    -IconLocation $iconPath `
    -Description "Dofus 2.64 Bot (IA Gemini) - Admin"

Write-Host ""
Write-Host "Raccourcis admin crees. Double-clic -> UAC -> Oui -> bot en admin." -ForegroundColor Cyan
Write-Host ""
