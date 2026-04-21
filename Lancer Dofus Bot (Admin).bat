@echo off
REM Lance le bot en ADMIN (requis si Dofus tourne en admin).
REM Windows bloque les inputs d'un process normal vers un process admin (UIPI).
REM Double-clique ce fichier, confirme UAC, et tout marche.

setlocal
cd /d "%~dp0"

REM --- Verifie si on est deja en admin ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    REM Pas admin : demande l'elevation
    echo Demande des droits admin...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

REM --- On est admin, lance le bot sans console ---
if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERREUR] pythonw.exe introuvable. Lance d'abord INSTALLER.bat
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m src.main
exit /b 0
