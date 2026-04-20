@echo off
REM ============================================================
REM Dofus Bot - INSTALLEUR AUTOMATIQUE
REM Double-clique ce fichier pour installer le bot.
REM Pas besoin de taper de commandes.
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

title Installation Dofus Bot

echo.
echo ============================================
echo   DOFUS BOT - Installation automatique
echo ============================================
echo.
echo Ce script va :
echo   - Installer Python 3.12 si necessaire
echo   - Installer Tesseract OCR
echo   - Installer Ollama + LM Studio (optionnel)
echo   - Creer les dependances
echo   - Creer un raccourci Bureau et Menu Demarrer
echo   - Lancer le bot a la fin
echo.
echo Patience : 5 a 20 minutes selon ta connexion.
echo.

REM --- Choix entre PowerShell 7 (pwsh) et PowerShell 5.1 (fallback) ---
where pwsh >nul 2>&1
if %errorlevel%==0 (
    set "PSEXE=pwsh"
) else (
    set "PSEXE=powershell"
)

echo [INFO] Utilisation de %PSEXE%.
echo.

REM --- Verifie si on est deja en admin ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Demande des droits admin pour installer Python et Tesseract...
    REM Relance ce meme .bat en admin via PowerShell
    %PSEXE% -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

echo [OK] Droits admin accordes.
echo.
echo Lancement de l'installeur...
echo.

REM --- Bypass ExecutionPolicy uniquement pour CE lancement ---
%PSEXE% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"

set "INSTALL_EXIT=%errorlevel%"

echo.
echo ============================================
if %INSTALL_EXIT%==0 (
    echo   Installation terminee avec succes !
) else (
    echo   [!!] Installation terminee avec des erreurs (code %INSTALL_EXIT%^)
    echo   Regarde les messages ci-dessus pour comprendre.
)
echo ============================================
echo.
echo Le bot doit apparaitre automatiquement.
echo Sinon : double-clic sur le raccourci 'Dofus Bot' de ton Bureau.
echo.
pause
exit /b %INSTALL_EXIT%
