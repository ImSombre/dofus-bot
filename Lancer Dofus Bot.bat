@echo off
REM Lance le Dofus Bot sans fenêtre console visible.
REM Double-clique ce fichier pour démarrer le bot.

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo [ERREUR] Le bot n'a pas ete installe. Lance d'abord scripts\install.ps1
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m src.main
exit /b 0
