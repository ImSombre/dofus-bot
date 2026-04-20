@echo off
REM Lance le bot AVEC console visible (pour voir les erreurs Python).
REM Double-clique si "Dofus Bot" crash direct sans message.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERREUR] Le bot n'a pas ete installe. Double-clic d'abord sur INSTALLER.bat
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   DOFUS BOT - Mode Debug (console visible)
echo ============================================
echo.
echo Si erreur, copie les 10 dernieres lignes.
echo.

".venv\Scripts\python.exe" -m src.main

echo.
echo ============================================
echo   Bot ferme. Code de sortie : %errorlevel%
echo ============================================
pause
