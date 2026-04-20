#Requires -Version 5.1
# ============================================================
# Dofus Bot - standalone installer (Windows)
# Compatible Windows PowerShell 5.1 et PowerShell 7+.
# Lance depuis la racine du projet:
#   .\scripts\install.ps1
# ============================================================

# Note : on N'UTILISE PAS $ErrorActionPreference=Stop globalement.
# PS5.1 transforme certains stderr de commandes natives (ex: py.exe "[ERROR]")
# en exceptions fatales, ce qui casse la detection defensive.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $projectRoot

# Versions Python supportees (wheels precompiles dispo pour Pillow/pydantic-core)
$pythonMin = [version]"3.11"
$pythonMax = [version]"3.13"
$pythonPreferred = "3.12"

function Write-Step { param([string]$msg) Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Write-OK   { param([string]$msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Write-Err2 { param([string]$msg) Write-Host "  [XX] $msg" -ForegroundColor Red }

function Invoke-Silent {
    # Execute une commande native en capturant toute sortie (stdout+stderr) et
    # retourne un objet avec .ExitCode et .Output (liste de lignes stdout uniquement).
    param([string]$cmd, [string[]]$argsArray)
    $result = [PSCustomObject]@{ ExitCode = 1; Output = @() }
    try {
        $all = & $cmd @argsArray 2>&1
        $result.ExitCode = $LASTEXITCODE
        # Garde uniquement les strings stdout (exclut ErrorRecord de stderr)
        $result.Output = @($all | Where-Object { $_ -is [string] })
    } catch {
        $result.ExitCode = 1
    }
    return $result
}

function Test-PythonVersion {
    param([string]$exePath)
    if (-not (Test-Path $exePath)) { return $null }
    $r = Invoke-Silent $exePath @("-c", "import sys; v = sys.version_info; print('{0}.{1}'.format(v.major, v.minor))")
    if ($r.ExitCode -ne 0 -or $r.Output.Count -eq 0) { return $null }
    try {
        $ver = [version]($r.Output[0].Trim())
        if ($ver -ge $pythonMin -and $ver -le $pythonMax) { return $ver.ToString() }
    } catch { }
    return $null
}

function Find-CompatiblePython {
    # 1) py.exe launcher (installe avec Python sur Windows)
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($v in @("3.12", "3.11", "3.13")) {
            $r = Invoke-Silent "py" @("-$v", "-c", "import sys; print(sys.executable)")
            if ($r.ExitCode -eq 0 -and $r.Output.Count -gt 0) {
                $p = $r.Output[0].Trim()
                if ($p -and (Test-Path $p)) { return $p }
            }
        }
    }
    # 2) python dans le PATH
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $ok = Test-PythonVersion $py.Source
        if ($ok) { return $py.Source }
    }
    # 3) Chemins usuels winget/installeurs
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\Python313\python.exe"
    )) {
        if (Test-Path $candidate) {
            $ok = Test-PythonVersion $candidate
            if ($ok) { return $candidate }
        }
    }
    return $null
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "   Dofus Bot - Installation" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "Projet : $projectRoot" -ForegroundColor Gray

# ------------------------------------------------------------
# 1) winget
# ------------------------------------------------------------
Write-Step "Verification winget"
$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Err2 "winget non trouve. Installe 'App Installer' depuis le Microsoft Store puis relance."
    exit 1
}
Write-OK "winget disponible"

# ------------------------------------------------------------
# 2) Python 3.11 - 3.13 (3.14 PAS SUPPORTE : wheels absentes)
# ------------------------------------------------------------
Write-Step "Python ($($pythonMin.ToString()) - $($pythonMax.ToString()))"
$pythonExe = Find-CompatiblePython

if (-not $pythonExe) {
    Write-Warn "Aucun Python compatible trouve (3.11-3.13 requis, 3.14 NON supporte)."

    # Strategie 1 : py.exe launcher peut auto-installer les runtimes (Windows 11)
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        Write-Host "  Tentative installation via le launcher py : py install $pythonPreferred" -ForegroundColor Yellow
        $r = Invoke-Silent "py" @("install", $pythonPreferred)
        if ($r.ExitCode -eq 0) {
            Write-OK "py install $pythonPreferred reussi"
            $pythonExe = Find-CompatiblePython
        } else {
            Write-Warn "py install a echoue - fallback winget"
        }
    }

    # Strategie 2 : winget
    if (-not $pythonExe) {
        Write-Host "  Installation Python $pythonPreferred via winget..." -ForegroundColor Yellow
        winget install --id "Python.Python.$pythonPreferred" --accept-source-agreements --accept-package-agreements --silent
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $pythonExe = Find-CompatiblePython
    }

    if (-not $pythonExe) {
        Write-Err2 "Python $pythonPreferred n'a pas pu etre installe."
        Write-Host "  Fais-le manuellement :" -ForegroundColor Yellow
        Write-Host "    1. Telecharge Python $pythonPreferred : https://www.python.org/downloads/release/python-3120/" -ForegroundColor Gray
        Write-Host "    2. Installe en cochant 'Add python.exe to PATH'" -ForegroundColor Gray
        Write-Host "    3. Redemarre PowerShell et relance ce script" -ForegroundColor Gray
        exit 1
    }
}
$pyVer = Test-PythonVersion $pythonExe
Write-OK "Python $pyVer : $pythonExe"

# ------------------------------------------------------------
# 3) venv + dependances Python
# ------------------------------------------------------------
Write-Step "Environnement virtuel Python"

# Nettoie un venv existant incompatible (ex: Python 3.14)
$venvPy = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $existingVer = Test-PythonVersion $venvPy
    if (-not $existingVer) {
        Write-Warn "venv existant utilise une version Python incompatible - suppression"
        Remove-Item ".venv" -Recurse -Force
    }
}

if (-not (Test-Path ".venv")) {
    Write-Host "  Creation du venv avec Python $pyVer..." -ForegroundColor Yellow
    & $pythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Err2 "Echec de la creation du venv."
        exit 1
    }
    Write-OK "venv cree"
} else {
    Write-OK "venv existe deja (Python compatible)"
}

Write-Host "  Mise a jour pip..." -ForegroundColor Yellow
& $venvPy -m pip install --upgrade pip wheel --quiet
if ($LASTEXITCODE -ne 0) { Write-Err2 "Echec upgrade pip"; exit 1 }

Write-Host "  Installation des dependances (peut prendre 2-3 min)..." -ForegroundColor Yellow
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Err2 "Echec de l'installation des dependances."
    Write-Host "  Details a verifier dans la sortie ci-dessus." -ForegroundColor Yellow
    exit 1
}
Write-OK "Dependances Python installees"

# ------------------------------------------------------------
# 4) Tesseract OCR
# ------------------------------------------------------------
Write-Step "Tesseract OCR"
$tesseract = "C:\Program Files\Tesseract-OCR\tesseract.exe"
if (-not (Test-Path $tesseract)) {
    Write-Host "  Installation de Tesseract via winget..." -ForegroundColor Yellow
    winget install --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements --silent
    if (-not (Test-Path $tesseract)) {
        Write-Err2 "Tesseract non installe. Telecharge manuellement: https://github.com/UB-Mannheim/tesseract/wiki"
        exit 1
    }
    Write-OK "Tesseract installe"
} else {
    Write-OK "Tesseract deja installe"
}

# ------------------------------------------------------------
# 5) tessdata fra + eng (telecharges localement dans data/tessdata)
# ------------------------------------------------------------
Write-Step "Language packs Tesseract (fra + eng)"
$tessdataDir = Join-Path $projectRoot "data\tessdata"
if (-not (Test-Path $tessdataDir)) {
    New-Item -ItemType Directory -Force -Path $tessdataDir | Out-Null
}

$langs = @("fra", "eng")
foreach ($langName in $langs) {
    $dest = Join-Path $tessdataDir "$langName.traineddata"
    $url  = "https://github.com/tesseract-ocr/tessdata/raw/main/$langName.traineddata"

    $needDownload = $true
    if (Test-Path $dest) {
        $sizeMB = [math]::Round((Get-Item $dest).Length / 1MB, 1)
        if ($sizeMB -ge 3) {
            $needDownload = $false
            Write-OK "$langName.traineddata deja present ($sizeMB MB)"
        } else {
            Write-Warn "$langName.traineddata suspect (trop petit) - re-telechargement"
            Remove-Item $dest -Force
        }
    }
    if ($needDownload) {
        Write-Host "  Telechargement $langName.traineddata..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        $sizeMB = [math]::Round((Get-Item $dest).Length / 1MB, 1)
        Write-OK "$langName.traineddata telecharge ($sizeMB MB)"
    }
}

# ------------------------------------------------------------
# 6) .env
# ------------------------------------------------------------
Write-Step "Configuration .env"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"

    $envContent = Get-Content ".env" -Raw

    $reTessCommented = '(?m)^# *TESSDATA_DIR=.*$'
    $reTessActive    = '(?m)^TESSDATA_DIR=.*$'
    $reDiscordTok    = '(?m)^DISCORD_TOKEN=\s*$'
    $reDiscordGuild  = '(?m)^DISCORD_GUILD_ID=\s*$'
    $reDiscordUsers  = '(?m)^DISCORD_ALLOWED_USER_IDS=.*$'

    $tessdataLine = "TESSDATA_DIR=" + $tessdataDir

    if ($envContent -match $reTessActive) {
        $envContent = [regex]::Replace($envContent, $reTessActive, $tessdataLine)
    } elseif ($envContent -match $reTessCommented) {
        $envContent = [regex]::Replace($envContent, $reTessCommented, $tessdataLine)
    } else {
        $envContent = $envContent.TrimEnd() + "`r`n" + $tessdataLine + "`r`n"
    }

    $envContent = [regex]::Replace($envContent, $reDiscordTok,   '# DISCORD_TOKEN=')
    $envContent = [regex]::Replace($envContent, $reDiscordGuild, '# DISCORD_GUILD_ID=')
    $envContent = [regex]::Replace($envContent, $reDiscordUsers, '# DISCORD_ALLOWED_USER_IDS=  # comma-separated')

    Set-Content -Path ".env" -Value $envContent -Encoding UTF8
    Write-OK ".env cree et pre-configure"
    Write-Host "  -> Edite .env pour personnaliser DOFUS_WINDOW_TITLE / DEFAULT_JOB / DEFAULT_ZONE" -ForegroundColor Gray
} else {
    Write-OK ".env existe deja (non ecrase)"
}

# ------------------------------------------------------------
# 7) Dossiers runtime
# ------------------------------------------------------------
Write-Step "Dossiers runtime"
$dirs = @("data", "data\calibration", "data\templates", "logs", "screenshots")
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
Write-OK "Dossiers data/, logs/, screenshots/ prets"

# ------------------------------------------------------------
# 8) Validation
# ------------------------------------------------------------
Write-Step "Validation"

Write-Host "  Tests pytest..." -ForegroundColor Yellow
& $venvPy -m pytest -q --no-header 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -eq 0) {
    Write-OK "Tests pytest OK"
} else {
    Write-Warn "Certains tests ont echoue (le bot peut quand meme tourner)"
}

Write-Host "  Test OCR Tesseract..." -ForegroundColor Yellow
$smokeScript = Join-Path $env:TEMP "dofusbot_tess_smoke.py"
$smokeCode = @()
$smokeCode += "import os"
$smokeCode += "os.environ['TESSDATA_PREFIX'] = r'" + $tessdataDir + "'"
$smokeCode += "import pytesseract"
$smokeCode += "pytesseract.pytesseract.tesseract_cmd = r'" + $tesseract + "'"
$smokeCode += "langs = pytesseract.get_languages()"
$smokeCode += "print('Tesseract langs:', langs)"
$smokeCode += "assert 'fra' in langs and 'eng' in langs, 'fra ou eng manquant'"
Set-Content -Path $smokeScript -Value ($smokeCode -join "`r`n") -Encoding UTF8
& $venvPy $smokeScript
if ($LASTEXITCODE -eq 0) {
    Write-OK "Tesseract OCR fra+eng OK"
} else {
    Write-Warn "Tesseract smoke test KO - verifie data/tessdata/"
}
Remove-Item $smokeScript -Force -ErrorAction SilentlyContinue

# ------------------------------------------------------------
# 8.5) Installation LM Studio (provider principal) + Ollama (fallback)
# ------------------------------------------------------------
Write-Step "LM Studio (IA locale, provider principal)"
Write-Host "  LM Studio = interface GUI, serveur local compatible OpenAI." -ForegroundColor Gray
Write-Host "  Installation silencieuse via winget..." -ForegroundColor Gray

# --- Detecte winget ---
$wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
$lmstudioInstalled = $false

# Tente plusieurs IDs winget possibles (LM Studio a change de nom au fil du temps)
$wingetIds = @("ElementLabs.LMStudio", "LMStudio.LMStudio", "LMStudio")

if ($null -ne $wingetCmd) {
    foreach ($wid in $wingetIds) {
        try {
            $listOut = & winget list --id $wid --exact 2>$null
            if ($LASTEXITCODE -eq 0 -and $listOut -match "LM Studio") {
                Write-OK "LM Studio deja installe ($wid)"
                $lmstudioInstalled = $true
                break
            }
        } catch {}
    }
    if (-not $lmstudioInstalled) {
        foreach ($wid in $wingetIds) {
            try {
                Write-Host "  Tentative winget : $wid ..." -ForegroundColor Gray
                & winget install --id $wid --exact --silent `
                    --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-OK "LM Studio installe via winget ($wid)"
                    $lmstudioInstalled = $true
                    break
                }
            } catch {}
        }
    }
}

# --- Fallback : ouvre navigateur sur le site officiel ---
if (-not $lmstudioInstalled) {
    Write-Warn "Install automatique de LM Studio impossible (winget absent ou ID change)"
    Write-Host "  Ouverture du site officiel pour telechargement manuel..." -ForegroundColor Yellow
    try {
        Start-Process "https://lmstudio.ai/download" | Out-Null
        Write-Host "  -> Telecharge et installe LM Studio depuis la page qui s'ouvre." -ForegroundColor White
        Write-Host "  -> Puis relance ce script pour continuer." -ForegroundColor White
    } catch {}
}

# ------------------------------------------------------------
# 8.6) Ollama (fallback secondaire)
# ------------------------------------------------------------
Write-Step "Ollama (fallback, arriere-plan)"
Write-Host "  Au cas ou LM Studio ne convient pas, Ollama est installe en fond." -ForegroundColor Gray

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($null -eq $ollamaCmd) {
    $ollamaInstaller = Join-Path $env:TEMP "OllamaSetup.exe"
    try {
        if (-not (Test-Path $ollamaInstaller)) {
            Write-Host "  Telechargement installeur Ollama (~130 MB)..." -ForegroundColor Gray
            Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller -UseBasicParsing
        }
        Write-Host "  Installation silencieuse d'Ollama..." -ForegroundColor Gray
        $p = Start-Process -FilePath $ollamaInstaller -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -PassThru
        $finished = $p.WaitForExit(90000)
        if (-not $finished) {
            Write-Warn "Install Ollama trop longue, on continue."
            try { $p.Kill() } catch {}
        } else {
            Write-OK "Ollama installe"
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
            $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
            if ($null -eq $ollamaCmd) {
                Start-Sleep -Seconds 5
                $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
                $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Warn ("Install Ollama echouee : " + $_.Exception.Message)
    }
} else {
    Write-OK ("Ollama deja installe : " + $ollamaCmd.Source)
}

# Pull qwen2.5vl:3b en arriere-plan (compatible 8 GB VRAM + Dofus)
# Dofus consomme 2-3 GB, il reste 5-6 GB pour le LLM. Le 3B (~2 GB) laisse de la marge.
if ($ollamaCmd) {
    Write-Host "  Pull qwen2.5vl:3b en fond (~2 GB) - modele leger compatible Dofus + 8 GB VRAM" -ForegroundColor Gray
    Start-Process -FilePath $ollamaCmd.Source -ArgumentList "pull","qwen2.5vl:3b" -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    Write-OK "Pull qwen2.5vl:3b lance en fond"
}

# ------------------------------------------------------------
# 8.7) Notice config LM Studio
# ------------------------------------------------------------
if ($lmstudioInstalled) {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Cyan
    Write-Host "   LM STUDIO : 3 etapes pour etre operationnel" -ForegroundColor Cyan
    Write-Host "=====================================================" -ForegroundColor Cyan
    Write-Host "  1. Lance LM Studio (icone bureau/menu demarrer)" -ForegroundColor White
    Write-Host "  2. Onglet 'Search' (loupe) -> cherche : qwen2.5 vl 3b" -ForegroundColor White
    Write-Host "     Choisis la version Q4_K_M (~2 GB) de bartowski" -ForegroundColor Gray
    Write-Host "     Leger pour laisser de la VRAM a Dofus qui en bouffe 2-3 GB" -ForegroundColor Gray
    Write-Host "  3. Onglet 'Developer' (</>) -> charge le modele -> 'Start Server'" -ForegroundColor White
    Write-Host "     Avant de lancer : regle 'GPU Layers' au max (slider)" -ForegroundColor White
    Write-Host "     Le serveur doit tourner sur http://localhost:1234" -ForegroundColor White
    Write-Host ""
    Write-Host "  Le bot detectera LM Studio automatiquement quand le serveur tourne." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Tu as plus de VRAM ? Prends une version plus grosse :" -ForegroundColor Gray
    Write-Host "    12 GB     -> qwen2.5 vl 7b Q4_K_M (~5 GB)" -ForegroundColor Gray
    Write-Host "    24 GB     -> qwen2.5 vl 32b Q4_K_M (~20 GB)" -ForegroundColor Gray
    Write-Host "    48+ GB    -> qwen2.5 vl 72b Q4_K_M (~45 GB)" -ForegroundColor Gray
    Write-Host "=====================================================" -ForegroundColor Cyan
}

# ------------------------------------------------------------
# 9) Raccourci Bureau (auto-cree)
# ------------------------------------------------------------
Write-Step "Raccourci Bureau"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Dofus Bot.lnk"
$runPath = Join-Path $projectRoot "scripts\run.ps1"
$psExe = Get-Command pwsh -ErrorAction SilentlyContinue
if ($psExe) { $shellTarget = $psExe.Source } else { $shellTarget = "powershell.exe" }
try {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnkPath)
    $sc.TargetPath = $shellTarget
    $sc.Arguments = "-ExecutionPolicy Bypass -NoExit -File `"$runPath`""
    $sc.WorkingDirectory = $projectRoot
    $sc.IconLocation = $shellTarget + ",0"
    $sc.Description = "Dofus 2.64 Bot"
    $sc.Save()
    Write-OK "Raccourci cree: $lnkPath"
} catch {
    Write-Warn ("Creation raccourci echouee (non bloquant) : " + $_.Exception.Message)
}

# ------------------------------------------------------------
# Fin - lance le bot automatiquement
# ------------------------------------------------------------
Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "   Installation terminee !" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Lancement automatique du bot dans 3s..." -ForegroundColor Cyan
Start-Sleep -Seconds 3
$pyExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $pyExe) {
    Set-Location $projectRoot
    & $pyExe -m src.main
} else {
    Write-Warn "python.exe du venv introuvable. Relance .\scripts\install.ps1 ou lance : .\scripts\run.ps1"
}
