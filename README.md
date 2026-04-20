# Dofus Bot — 2.64

Bot d'automatisation pour Dofus 2.64 — farm métiers (bûcheron, paysan) + combat PvM solo.

> **Avertissement légal / usage autorisé**
>
> Ce projet est destiné **exclusivement** à un usage sur **serveur privé** avec l'**accord écrit du staff**.
> Il **ne doit pas** être utilisé sur les serveurs officiels Ankama : cela viole les CGU et peut conduire à un bannissement définitif du compte.
> Le propriétaire de ce projet atteste avoir obtenu l'autorisation écrite du staff du serveur privé ciblé.
> Les auteurs déclinent toute responsabilité en cas d'utilisation en dehors de ce cadre.

---

## Prérequis

- Windows 11 (testé) — Windows 10 probablement OK mais non vérifié.
- Python 3.11+ (3.12 recommandé).
- Client Dofus 2.64 installé et fonctionnel.
- Tesseract OCR 5.x ([installeur Windows](https://github.com/UB-Mannheim/tesseract/wiki)).
- PowerShell 7 conseillé (5.1 OK).

## Installation rapide

```powershell
# depuis le dossier du projet
pwsh ./scripts/install.ps1
```

Le script :
1. Vérifie Python 3.11+.
2. Crée un venv `.venv`.
3. Installe les dépendances `requirements.txt`.
4. Vérifie la présence de Tesseract (ou propose l'installation via winget).
5. Copie `.env.example` → `.env` si absent.

## Configuration

Édite `.env` :

```ini
DOFUS_WINDOW_TITLE=Dofus 2.64
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
DEFAULT_JOB=lumberjack
DEFAULT_ZONE=bonta_forest_sud
DISCORD_ENABLED=false
```

Édite `src/config/zones.example.yaml` (renomme en `zones.yaml`) pour configurer tes zones de farm.

## Lancement

```powershell
pwsh ./scripts/run.ps1
```

ou manuellement :

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main
```

## Utilisation

1. Lance Dofus en mode **fenêtré** (pas plein écran).
2. Lance la GUI du bot.
3. Dashboard → sélectionne mode (Farm / Combat), job, zone.
4. Clique **Start** (ou F5).
5. Stats en temps réel + logs en bas.
6. **Panic stop** : Ctrl+Shift+P (arrêt immédiat + screenshot).

### Contrôle Discord (optionnel)

Active dans `.env` avec token + guild ID + user IDs whitelistés.

Commandes slash :
- `/start <mode> <job_or_zone>`
- `/stop`
- `/pause` / `/resume`
- `/status`
- `/stop_loss <xp_per_hour>`
- `/screenshot`

## Développement

```powershell
# lint
ruff check src tests

# format
ruff format src tests

# type check
mypy src

# tests
pytest

# coverage
pytest --cov=src --cov-report=html
```

## Arborescence

Voir [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Documentation

- [`docs/PRD.md`](docs/PRD.md) — spécifications produit
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture technique
- [`docs/UI_DESIGN.md`](docs/UI_DESIGN.md) — design de la GUI
- [`docs/TESTING.md`](docs/TESTING.md) — stratégie de tests
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — déploiement Windows
- [`docs/SECURITY.md`](docs/SECURITY.md) — checklist sécurité
- [`ROADMAP.md`](ROADMAP.md) — feuille de route

## Contribution

Projet privé mono-dev pour l'instant. Convention de commits : [Conventional Commits](https://www.conventionalcommits.org/).

## Licence

Propriétaire. Tous droits réservés. Ne pas redistribuer.
