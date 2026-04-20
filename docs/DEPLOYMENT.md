# Deployment — Dofus Bot

**Auteur** : devops-sre (orchestrator)
**Date** : 2026-04-17

---

## 1. Cible de déploiement

**Poste local Windows 11** (pas VPS — le client de jeu tourne localement).
Python 3.11+, ~500 Mo d'espace disque (venv inclus), ~500 Mo RAM runtime.

## 2. Pré-requis système

| Composant | Version | Installation |
|---|---|---|
| Windows | 10 22H2 ou 11 | — |
| Python | ≥ 3.11 | `winget install Python.Python.3.12` |
| PowerShell | ≥ 5.1, 7.x conseillé | `winget install Microsoft.PowerShell` |
| Tesseract OCR | 5.3+ | `winget install UB-Mannheim.TesseractOCR` |
| Dofus 2.64 | — | Client officiel serveur privé |
| Git (facultatif) | — | `winget install Git.Git` |

## 3. Procédure d'installation

```powershell
cd C:\Users\<user>\Desktop\dofus-bot
pwsh ./scripts/install.ps1
notepad .env   # éditer
```

Le script `install.ps1` :
1. Vérifie Python 3.11+.
2. Crée `.venv`.
3. Installe `requirements.txt`.
4. Vérifie Tesseract et propose `winget install` si absent.
5. Copie `.env.example → .env` si absent.
6. Crée les dossiers `data/`, `logs/`, `screenshots/`.

## 4. Lancement

### Manuel
```powershell
pwsh ./scripts/run.ps1
```

### Raccourci bureau (`Dofus Bot.lnk`)
- Target : `pwsh.exe -File "C:\...\dofus-bot\scripts\run.ps1"`
- Start in : `C:\...\dofus-bot`
- Icône : `src/ui/resources/icons/app.ico`

### Auto-start via Task Scheduler (optionnel)

```powershell
$action  = New-ScheduledTaskAction -Execute "pwsh.exe" `
           -Argument "-File C:\...\dofus-bot\scripts\run.ps1" `
           -WorkingDirectory "C:\...\dofus-bot"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable `
            -DontStopOnIdleEnd -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "DofusBot" -Action $action `
                       -Trigger $trigger -Settings $settings
```

Désactiver : `Unregister-ScheduledTask -TaskName DofusBot -Confirm:$false`.

> **Recommandation** : ne PAS activer l'auto-start en prod. Le bot doit être lancé
> consciemment par l'utilisateur après vérification du contexte (client Dofus
> fenêtré, résolution, zone, etc.).

## 5. Rotation des logs

Gérée par `loguru` côté app :
- fichier principal : `logs/bot_YYYY-MM-DD.log` — rotation 50 Mo, rétention 14 jours, compression ZIP.
- fichier structuré JSONL : `logs/bot_structured_YYYY-MM-DD.jsonl` — mêmes règles.

Nettoyage additionnel (optionnel, script tâche planifiée hebdo) :

```powershell
Get-ChildItem .\logs\*.zip | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item
Get-ChildItem .\screenshots\*.png | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item
```

## 6. Monitoring local (ultra-léger)

- Log viewer : [`glogg`](https://glogg.bonnefon.org/) ou `tail -f` via Git Bash / WSL.
- Métriques runtime : onglet **Stats** de la GUI + table SQLite `stats_hourly`.
- Alerting : Discord webhook sur erreurs (déjà prévu côté `RemoteControl.notify`).

## 7. Mise à jour

```powershell
git pull
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt --upgrade
pytest           # smoke
```

## 8. CI / GitHub Actions

`.github/workflows/ci.yml` :
- Trigger : `push` / `pull_request` sur `main`.
- Runners : `ubuntu-latest`.
- Matrix : Python 3.11, 3.12.
- Étapes : ruff (lint + format check) → mypy (warn-only) → pytest + coverage → pip-audit (warn-only).

> Les tests dépendant de Qt (`pytest-qt`) tournent avec `QT_QPA_PLATFORM=offscreen`.
> Les imports `pyautogui` / `mss` peuvent échouer en CI sans display — les tests
> unitaires concernés doivent être marqués `@pytest.mark.skip_on_ci` si besoin.

## 9. Sauvegarde / restauration

**Fichiers critiques** :
- `.env`
- `data/bot.sqlite3`
- `data/maps_graph.json`
- `src/config/zones.yaml`

**Script de backup hebdomadaire** (à placer dans `scripts/backup.ps1` — TODO next release) :

```powershell
$dest = "$env:USERPROFILE\Backups\dofus-bot\$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Path $dest -Force
Copy-Item .env, data\bot.sqlite3, data\maps_graph.json, src\config\zones.yaml -Destination $dest
```

Restauration = copie inverse.

## 10. Packaging (roadmap later)

PyInstaller :
```
pip install pyinstaller
pyinstaller --onefile --windowed --icon src/ui/resources/icons/app.ico src/main.py
```
Sortie : `dist/main.exe`. À tester sur poste vierge (Tesseract doit rester installé séparément).

## 11. Troubleshooting

| Symptôme | Cause probable | Fix |
|---|---|---|
| "Dofus window not detected" | Client non lancé ou titre différent | Vérifier `DOFUS_WINDOW_TITLE` dans `.env` |
| OCR renvoie vide | Tesseract pas installé ou chemin faux | Vérifier `TESSERACT_PATH` |
| `pyautogui.FailSafeException` | Souris au coin | Ne pas bouger la souris ; ou désactiver failsafe (déconseillé) |
| GUI figée quelques sec | Tick trop lourd | Vérifier logs, réduire `tick_hz` |
| "discord.py gateway closed" | Token invalide ou rate-limit | Régénérer token, vérifier whitelist |
