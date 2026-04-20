# Architecture — Dofus Bot

**Auteur** : backend-architect (orchestrator)
**Date** : 2026-04-17
**Version cible** : 0.1.0 (MVP)

---

## 1. Vue d'ensemble modulaire

```
                         ┌─────────────────┐
                         │      main.py    │
                         │  (entrypoint)   │
                         └────────┬────────┘
                                  │
                     ┌────────────┴────────────┐
                     ▼                         ▼
             ┌────────────┐           ┌────────────────┐
             │   ui/      │           │  handlers/     │
             │  (PyQt6)   │◀─signals─▶│  state machine │
             └────────────┘           │  job runners   │
                                      │  combat runner │
                                      └───────┬────────┘
                                              │
                       ┌──────────────────────┼──────────────────────┐
                       ▼                      ▼                      ▼
                ┌────────────┐        ┌────────────┐         ┌────────────┐
                │ services/  │        │  models/   │         │  config/   │
                │ - vision   │        │ pydantic   │         │ settings   │
                │ - input    │        │ dataclass  │         │ + zones    │
                │ - path     │        │ enums      │         │ + jobs YAML│
                │ - persist  │        └────────────┘         └────────────┘
                │ - remote   │
                └────────────┘
```

---

## 2. Responsabilités par module

### `src/main.py`
- Parse arguments CLI.
- Charge `settings`, initialise logger.
- Lance l'application Qt et injecte dépendances (DI manuelle).
- Gère le cycle de vie global.

### `src/config/`
- **`settings.py`** : `Settings` (pydantic-settings) chargée depuis `.env` + `config/default.yaml`. Expose paths Tesseract, window title, Discord token/guild, seuils.
- **`zones.yaml`** : définition des zones de farm (coords cells, banque la plus proche, type de ressources).
- **`jobs.yaml`** : config métiers (templates à matcher, animations).
- **`combat.yaml`** : builds de sorts, rotations, conditions.

### `src/services/`

#### `vision.py` → `VisionService` (Protocol) + 3 stratégies composables

```
VisionService (Protocol)
        │
        └── MssVisionService (impl concrète)
                │
                ├── ColorShapeDetector      — HSV segmentation + contours
                │   Fast (<10 ms/frame), retourne bounding boxes non-classifiées.
                │
                ├── TooltipOCRDetector      — hover souris 300 ms + OCR tooltip
                │   Lent (~500 ms/candidat), identifie "Frêne (Niveau 15)".
                │
                ├── TemplateMatchingDetector — cv2.matchTemplate (existant)
                │   Rapide si templates présents, fragile aux patches UI.
                │
                └── YoloDetector (optionnel) — YOLOv8n inférence CPU
                    Plug-and-play via VisionDetector Protocol.
                    Désactivé si YOLO_MODEL_PATH vide.
```

**API publique** (VisionService Protocol) :
- `capture(region) -> Frame`
- `find_templates(frame, templates, threshold) -> list[Detection]`  ← backward-compat
- `read_text(frame, region, lang) -> str`  ← backward-compat
- `detect_popup(frame) -> str | None`  ← backward-compat
- `scan_interactables(frame) -> list[DetectedObject]`  ← **NEW** — zéro template
- `read_ui_text(region: UIRegion) -> str`  ← **NEW** — lit zones UI fixes
- `detect_tooltip(frame) -> Tooltip | None`  ← **NEW**
- `detect_popup_typed(frame) -> Popup | None`  ← **NEW**

**Impl concrète** : `MssVisionService` (mss + pillow + opencv-python + pytesseract).

#### `auto_calibration.py` → `AutoCalibrationService` ← **NEW**

```
AutoCalibrationService
    ├── calibrate_ui_regions() → UIRegionsCalibration
    │       Phase 1 : heuristiques OpenCV + confirmation Qt overlay
    │       Sauvegarde dans data/calibration/calibration.json
    │
    └── calibrate_map(map_id) → MapCalibration
            Phase 2 : ColorShape → groupe par couleur → OCR → SQLite known_resources
            Templates 32x32 sauvegardés dans data/calibration/templates/

#### `yolo_detector.py` → `YoloDetector` ← **NEW**

Import paresseux d'ultralytics. YoloDetector.is_available() == False si :
    - YOLO_MODEL_PATH vide
    - ultralytics non installé
    - fichier .pt introuvable
```

#### `input_service.py` → `InputService` (Protocol)
> renommé `input_service.py` pour éviter collision avec builtin `input`.
- `move_mouse(x, y, duration_ms=None)`
- `click(x, y, button="left", jitter=True)`
- `drag(x1, y1, x2, y2)`
- `press_key(key)`
- `type_text(text)`
- Impl : `PyAutoGuiInputService` avec humanisation (Bezier paths, délais log-normal).

#### `pathfinding.py` → `PathfindingService`
- Graph de maps (nodes = maps, edges = transitions avec cell de sortie).
- `shortest_path(from_map: MapId, to_map: MapId) -> list[MoveInstruction]`
- Algorithme : BFS (graph petit <500 nodes MVP) → Dijkstra later si coûts variables.
- Source du graph : `data/maps_graph.json` bootstrappé à la main pour zones MVP.

#### `persistence.py` → `PersistenceService`
- Wrapper SQLite.
- Tables : `sessions`, `events`, `stats_hourly`, `known_maps`, `inventory_snapshots`, `errors`.
- Pattern repository : `SessionRepo`, `StatsRepo`, etc.
- Migrations : script SQL versionné, exécuté au démarrage (`schema_version` table).

#### `remote_control.py` → `DiscordControl`
- `discord.py` bot.
- Commandes slash : `/start`, `/stop`, `/pause`, `/resume`, `/status`, `/stop_loss <xp_per_hour>`, `/screenshot`.
- Whitelist user IDs depuis settings.
- Rate limiting : max 10 commandes / user / minute (token bucket).
- Notifications outbound : erreurs critiques, pop-up détecté, session terminée.

### `src/handlers/`

#### `state_machine.py` → `BotStateMachine`
- States : `IDLE`, `CALIBRATING` (NEW), `STARTING`, `MOVING`, `SCANNING`, `ACTING`, `COMBAT`, `CHECKING_INVENTORY`, `BANKING`, `PAUSED`, `ERROR`, `RECONNECTING`, `STOPPING`.
- Transitions explicites (dict `{(State, Event): State}`).
- Event loop tick ~10 Hz.
- Hooks : `on_enter_state`, `on_exit_state` pour logging + metrics.
- Guards : fonctions qui bloquent une transition (ex: ne pas entrer BANKING si combat en cours).

#### `job_runner.py` → `JobRunner` (ABC) + `LumberjackRunner`, `FarmerRunner`
- Boucle : locate_resource → move_to → interact → wait_harvest → validate → loop.
- Partage utilisation du `VisionService` + `InputService`.

#### `combat_runner.py` → `CombatRunner`
- Détecte début/fin tour via template matching barre de tour.
- Rotation de sorts depuis YAML.
- Détection PV joueur via OCR sur bandeau.
- Décision de fuite si PV < seuil.

#### `inventory_manager.py` → `InventoryManager`
- Compteur interne (incrémenté à chaque récolte).
- Scan visuel périodique de confirmation (toutes les 5 min).
- Déclenche `BANKING` quand seuil atteint.

### `src/models/`
- `game_state.py` : `GameState` (pydantic) agrégat mutable du state bot.
- `map.py` : `MapId`, `MapNode`, `CellCoord`.
- `monster.py`, `resource.py`, `job.py`.
- `detection.py` : `Detection` (legacy), `DetectedObject` (unifié), `Tooltip`, `Popup`, `UIRegion`, `UIRegionsCalibration`, `MapCalibration`, `Calibration`, `DetectionConfidence` (LOW/MEDIUM/HIGH).
- `enums.py` : `BotState`, `JobType`, `ResourceType`.

### `src/ui/`
- Voir `docs/UI_DESIGN.md`.

---

## 3. State machine principale (diagramme)

```
                       ┌──────┐
                  ┌───▶│ IDLE │◀───────────────┐
                  │    └──┬───┘                │
                  │       │start_requested     │
                  │       ▼                    │
                  │   ┌─────────┐              │
                  │   │STARTING │              │
                  │   └────┬────┘              │
                  │        │ cal?              │
                  │        ▼                   │
                  │  ┌───────────┐             │
                  │  │CALIBRATING│             │
                  │  └─────┬─────┘             │
                  │        │ done/skip         │
                  │        ▼                   │
     ┌────────────┴──┐  ┌─────────┐            │
     │CHECKING_INV   │◀─│SCANNING │◀───────┐   │
     └────┬──────────┘  └────┬────┘        │   │
          │inv_full          │target_found │   │
          ▼                  ▼             │   │
     ┌─────────┐        ┌────────┐         │   │
     │BANKING  │        │ ACTING │─done────┤   │
     └────┬────┘        └────┬───┘         │   │
          │done              │engage       │   │
          ▼                  ▼             │   │
     ┌─────────┐        ┌────────┐         │   │
     │ MOVING  │───────▶│ COMBAT │──win────┘   │
     └─────────┘        └───┬────┘             │
                            │dead / stop       │
                            ▼                  │
                       ┌─────────┐             │
                       │  ERROR  │─recover────▶│
                       └────┬────┘             │
                            │fatal             │
                            ▼                  │
                       ┌──────────────┐        │
                       │RECONNECTING  │────────┘
                       └──────────────┘

   (PAUSED can be entered from any state on user command / popup detection,
    and returns to the previous state on resume.)
```

---

## 4. Schéma SQLite

```sql
-- schema_version pour migrations
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);

-- sessions de bot
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,         -- ISO 8601
    ended_at TEXT,
    mode TEXT NOT NULL,               -- 'farm' | 'combat'
    job_or_zone TEXT NOT NULL,
    total_xp INTEGER DEFAULT 0,
    total_actions INTEGER DEFAULT 0,
    total_errors INTEGER DEFAULT 0,
    end_reason TEXT                   -- 'user_stop' | 'error' | 'stop_loss' | 'crash'
);
CREATE INDEX idx_sessions_started ON sessions(started_at);

-- événements détaillés (sampled)
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,               -- 'harvest' | 'kill' | 'state_change' | 'bank_deposit'
    payload TEXT                      -- JSON
);
CREATE INDEX idx_events_session ON events(session_id, ts);

-- stats agrégées par heure (pour graph historique)
CREATE TABLE stats_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    hour TEXT NOT NULL,               -- '2026-04-17 14:00'
    xp_gained INTEGER DEFAULT 0,
    actions_count INTEGER DEFAULT 0,
    kamas_estimated INTEGER DEFAULT 0
);

-- maps connues (pour pathfinding + debug)
CREATE TABLE known_maps (
    id TEXT PRIMARY KEY,              -- ex: 'bonta_forest_sud_7_3'
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    zone TEXT,
    last_visited TEXT,
    notes TEXT
);

-- snapshots inventaire
CREATE TABLE inventory_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    kamas INTEGER,
    items_json TEXT                   -- JSON array
);

-- erreurs (avec ref vers screenshot sur disque)
CREATE TABLE errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    level TEXT NOT NULL,              -- 'warning' | 'error' | 'critical'
    message TEXT NOT NULL,
    traceback TEXT,
    screenshot_path TEXT
);
```

---

## 5. Dépendances Python (requirements.txt pinned)

```
# Core
python>=3.11
pydantic==2.8.2
pydantic-settings==2.4.0
loguru==0.7.2
pyyaml==6.0.2

# Vision
mss==9.0.1
pillow==10.4.0
opencv-python==4.10.0.84
numpy==2.0.1
pytesseract==0.3.13

# Input
pyautogui==0.9.54
pynput==1.7.7

# GUI
PyQt6==6.7.1
pyqtgraph==0.13.7

# Remote control (optionnel)
discord.py==2.4.0

# Crypto secrets locaux
keyring==25.3.0

# Dev
pytest==8.3.2
pytest-qt==4.4.0
pytest-cov==5.0.0
ruff==0.6.2
mypy==1.11.1
pip-audit==2.7.3
```

---

## 6. Arborescence complète

```
dofus-bot/
├── .claude/
│   └── decisions.log
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── UI_DESIGN.md
│   ├── ROADMAP.md
│   ├── SECURITY.md
│   ├── DEPLOYMENT.md
│   └── TESTING.md
├── scripts/
│   ├── install.ps1
│   └── run.ps1
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py
│   │   ├── default.yaml
│   │   └── zones.example.yaml
│   ├── services/
│   │   ├── __init__.py
│   │   ├── vision.py
│   │   ├── input_service.py
│   │   ├── pathfinding.py
│   │   ├── persistence.py
│   │   └── remote_control.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── state_machine.py
│   │   ├── job_runner.py
│   │   ├── combat_runner.py
│   │   └── inventory_manager.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── enums.py
│   │   ├── game_state.py
│   │   ├── map.py
│   │   ├── detection.py
│   │   └── job.py
│   └── ui/
│       ├── __init__.py
│       ├── app.py
│       ├── main_window.py
│       ├── tabs/
│       │   ├── __init__.py
│       │   ├── dashboard_tab.py
│       │   ├── debug_tab.py
│       │   ├── config_tab.py
│       │   ├── stats_tab.py
│       │   └── discord_tab.py
│       └── widgets/
│           ├── __init__.py
│           ├── preview_widget.py
│           └── stats_panel.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_pathfinding.py
│   ├── test_state_machine.py
│   ├── test_vision.py              # updated — 3 détecteurs en isolation
│   └── test_auto_calibration.py   # NEW
├── scripts/
│   ├── install.ps1
│   ├── run.ps1
│   └── train_yolo.py              # NEW — scaffold entraînement YOLOv8n
├── docs/
│   └── ML_PIPELINE.md             # NEW
├── data/                           # gitignored — captures, DB
│   ├── calibration/               # NEW — ui_regions.json, templates/
│   ├── models/                    # NEW — dofus_yolo.pt (après entraînement)
│   ├── yolo_dataset/              # NEW — images + labels YOLO format
│   └── .gitkeep
├── logs/                           # gitignored
│   └── .gitkeep
├── screenshots/                    # gitignored — debug captures
│   └── .gitkeep
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── README.md
└── ROADMAP.md
```

---

## 7. Flows de données

### Capture → Décision → Action (pipeline zéro-template)
```
mss grab  ──▶  numpy ndarray
                    │
         ┌──────────┼───────────────┐
         ▼          ▼               ▼
    YoloDetector  ColorShape    TemplateMatching
    (optional)    (candidats)   (fallback si templates)
         │          │               │
         └──────────┴───────────────┘
                    │
              list[DetectedObject]  (unified DTO)
                    │
         ┌──────────┴──────────────┐
         ▼ (si scan_interactables) │
  TooltipOCRDetector               │
  hover + OCR tooltip              │
  → label + level                  │
         │                         │
         └──────────────────────────┘
                    │
              list[DetectedObject]  (classified)
                    │
              BotStateMachine
                    │
              JobRunner / CombatRunner
                    │
              InputService.click(x, y)  ──▶  pyautogui  ──▶  Dofus window
```

### Event → Persistence → UI
```
JobRunner emits "harvest" event
        │
        ▼
PersistenceService.record_event(session_id, 'harvest', {...})
        │
        ▼                                  ▼
SQLite                           Qt signal → StatsPanel.refresh()
```

---

## 8. Dependency Injection

DI manuelle dans `main.py` :

```python
settings = Settings()
persistence = PersistenceService(db_path=settings.db_path)
vision = MssVisionService(tesseract_path=settings.tesseract_path)
input_svc = PyAutoGuiInputService()
pathfinder = PathfindingService.load_from_yaml(settings.maps_graph_path)
state_machine = BotStateMachine(vision, input_svc, pathfinder, persistence)
app = QApplication(sys.argv)
window = MainWindow(state_machine, persistence)
window.show()
sys.exit(app.exec())
```

Remplaçable par `dependency-injector` si le graph grossit, pas nécessaire MVP.

---

## 9. Concurrence

- Thread principal : Qt event loop (UI).
- Thread bot : `BotRunnerThread(QThread)` qui pilote la state machine.
- Thread Discord : asyncio event loop dans un thread dédié (via `asyncio.run_coroutine_threadsafe`).
- Communication bot → UI : **signaux Qt uniquement**.
- Communication Discord → bot : queue thread-safe (`queue.Queue`), consommée par le thread bot à chaque tick.

---

## 10. Hot-reload de configs

- Watcher `watchdog` optionnel sur `config/*.yaml`.
- À la détection d'un changement, le bot charge la nouvelle config si en état `IDLE` ou émet un warning sinon.
- MVP : reload manuel via bouton UI.

---

## 11. Stratégie de gestion d'erreurs

| Type | Recovery |
|---|---|
| Template not found (timeout scan) | Retry 3x puis change de map |
| Click sur cell inattendue | Revert + re-scan |
| Fenêtre Dofus perdue | Pause + tentative refocus |
| Crash Dofus.exe | Relance + reconnect |
| Exception Python non gérée | catch top-level → log + screenshot + notify Discord → ERROR state |
| DB lock | retry exponential backoff |

Toutes les erreurs passent par un `ErrorHandler` central qui screenshote et persiste dans `errors`.

---

## 12. Extensibilité future

- Nouveau métier : ajouter `XxxRunner(JobRunner)` + entrée `jobs.yaml` + templates dans `data/templates/xxx/`.
- Nouvelle zone : ajouter au `maps_graph.json` + entrée `zones.yaml`.
- Nouveau comportement combat : ajouter une classe `SpellRotation` YAML-driven.
- Nouveau canal de contrôle (Telegram, API HTTP) : implémenter le Protocol `RemoteControl`.
