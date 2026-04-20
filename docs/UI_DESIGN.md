# UI Design — Dofus Bot Desktop GUI

**Framework retenu** : **PyQt6**
**Auteur** : ux-ui-designer (orchestrator)
**Date** : 2026-04-17

---

## 1. Justification du choix PyQt6 vs Tkinter

| Critère | PyQt6 | Tkinter |
|---|---|---|
| Widget image temps réel (preview OpenCV) | `QLabel.setPixmap` + `QImage.fromData`, natif, 30 fps easy | PIL + Canvas, pénible, tearing |
| Threads workers (bot tourne en arrière-plan) | `QThread` + signaux/slots | threading + `root.after`, risque race conditions |
| Dark mode / thème moderne | qdarkstyle ou QSS natif | bricolage ttk |
| Graphes temps réel (XP/h) | `pyqtgraph` intégrable | matplotlib canvas lourd |
| Licence | GPL v3 ou commerciale | Python std |
| Taille install | ~60 Mo | 0 (stdlib) |

**Verdict** : PyQt6 pour la qualité du rendu temps réel et la gestion thread-safe des signaux. Coût install acceptable pour un outil desktop mono-utilisateur.

---

## 2. Arborescence de la fenêtre principale

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Dofus Bot — v0.1.0                                [_] [□] [×]            │
├──────────────────────────────────────────────────────────────────────────┤
│ [File] [Bot] [View] [Help]                                               │
├──────────────────────────────────────────────────────────────────────────┤
│ ┌─── Control Panel ───┐ ┌────────── Live View ──────────────────────────┐│
│ │                     │ │                                               ││
│ │ Mode:               │ │   [ Screenshot preview + bounding boxes ]     ││
│ │  (•) Farm           │ │                                               ││
│ │  ( ) Combat         │ │          1280 × 720 area zoomed               ││
│ │  ( ) Idle           │ │                                               ││
│ │                     │ │                                               ││
│ │ Job: [Lumberjack▼]  │ │                                               ││
│ │ Zone:[Bonta Fst S▼] │ │                                               ││
│ │                     │ │                                               ││
│ │ Stop Loss (min):    │ │                                               ││
│ │ XP/h below: [500]   │ │                                               ││
│ │                     │ │                                               ││
│ │ ┌─────────────────┐ │ │                                               ││
│ │ │  ▶  START       │ │ │                                               ││
│ │ └─────────────────┘ │ │                                               ││
│ │ ┌─────────────────┐ │ │                                               ││
│ │ │  ■  STOP        │ │ │                                               ││
│ │ └─────────────────┘ │ │                                               ││
│ │ ┌─────────────────┐ │ │                                               ││
│ │ │  ⏸  PAUSE       │ │ │                                               ││
│ │ └─────────────────┘ │ │                                               ││
│ │                     │ │                                               ││
│ └─────────────────────┘ └───────────────────────────────────────────────┘│
│ ┌─── Stats ───────────────────────────────────────────────────────────┐  │
│ │ State: FARMING        Runtime: 02:17:34      Errors: 0              │  │
│ │ XP gained: 142 350    XP/h: 61 800           Actions: 2 417         │  │
│ │ Kamas est./h: 18 400  Inv: 87/100            Last bank: 00:23:12 ago│  │
│ └─────────────────────────────────────────────────────────────────────┘  │
│ ┌─── Logs (last 50) ──────────────────────────────────────────────────┐  │
│ │ 14:22:18 INFO  Tree detected at (523, 411) — clicking              │  │
│ │ 14:22:21 INFO  Harvest complete +42 XP                             │  │
│ │ 14:22:23 DEBUG Scanning map bonta_forest_sud_7_3                   │  │
│ │ ...                                                                 │  │
│ └─────────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────┤
│ ● Connected to game | Discord: linked | Pathfinder: 47 maps loaded      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Onglets de l'application

### Onglet 1 — `Dashboard` (défaut)
- Control Panel (gauche, 25% largeur).
- Live View (centre, 55%).
- Stats + Logs (bas, pleine largeur).

### Onglet 2 — `Debug`
- Grille 2x2 :
  - Capture brute.
  - Capture + overlay detections (bounding boxes, labels, confidence).
  - Image de référence du template courant.
  - Heatmap du matching (visualisation OpenCV `matchTemplate` result).
- Slider threshold matching.
- Checkbox "enregistrer frames avec détections <70%" (pour retraining templates).

### Onglet 3 — `Configuration`
- Arbre de config éditable (dictionnaire recursif).
- Sections : `game.window_title`, `paths.tesseract_exe`, `jobs.lumberjack.zones`, `combat.spells_rotation`, `discord.enabled`, `discord.allowed_user_ids`.
- Bouton "Reload from disk" / "Save to disk" / "Export as YAML".

### Onglet 4 — `Stats historiques`
- Graph pyqtgraph : XP/h par session (dernières 20 sessions).
- Table sessions : date, durée, métier, XP gagné, errors.
- Export CSV.

### Onglet 5 — `Discord`
- Status bot Discord (connecté / offline).
- Liste whitelist user IDs (ajout/suppression).
- Log 20 dernières commandes reçues.
- Bouton "Test notification".

---

## 4. États visuels de l'indicateur principal

| État interne | Label | Couleur | Icône |
|---|---|---|---|
| `IDLE` | Idle | gris `#888` | ⊘ |
| `MOVING` | Moving | bleu `#3b82f6` | ↗ |
| `SCANNING` | Scanning | violet `#8b5cf6` | 👁 |
| `ACTING` | Acting | vert `#10b981` | ⚡ |
| `COMBAT` | In combat | orange `#f97316` | ⚔ |
| `BANKING` | Banking | cyan `#06b6d4` | 🏦 |
| `PAUSED` | Paused | jaune `#eab308` | ⏸ |
| `ERROR` | Error | rouge `#ef4444` | ⚠ |
| `RECONNECTING` | Reconnecting | rouge-orangé `#f43f5e` | ↻ |

(Emojis uniquement en interne/design — pas dans le code de production, remplacés par icônes vectorielles Qt.)

---

## 5. Interactions & raccourcis clavier

| Action | Raccourci |
|---|---|
| Start | F5 |
| Stop (full stop) | F6 |
| Pause/Resume | F7 |
| Panic button (urgence, stop + capture) | Ctrl+Shift+P |
| Refresh preview | F9 |
| Switch onglet Dashboard/Debug | Ctrl+1/2/3/4/5 |

**Panic button** : STOP immédiat du bot, screenshot pleine résolution, log dump, notif Discord. Conçu pour "je vois quelque chose qui cloche, j'arrête tout".

---

## 6. Accessibilité

- Contrastes AA minimum (WCAG).
- Tous les contrôles accessibles au clavier (tab order défini).
- Tooltips explicatifs sur chaque bouton.
- Taille de police réglable (option dans View menu).
- Pas de dépendance couleur seule pour communiquer l'état (toujours label + icône + couleur).

---

## 7. Comportements thread-safe

- Bot runner vit dans un `QThread` dédié.
- Communication runner → UI via **signaux Qt** uniquement (jamais accès direct aux widgets depuis le thread bot).
- Signaux définis :
  - `state_changed(str)` → MainWindow.on_state_changed
  - `stats_updated(dict)` → MainWindow.on_stats_updated
  - `log_emitted(str, str)` → MainWindow.on_log (level, message)
  - `preview_ready(QImage, list_detections)` → DebugTab.on_preview
  - `error_occurred(str, QImage)` → MainWindow.on_error

---

## 8. Wireframe onglet Debug (détaillé)

```
┌─── Debug ─────────────────────────────────────────────────────────────┐
│ Threshold: [0.75] ──●────────  [✔] Save low-conf frames               │
├──────────────────────────────────┬────────────────────────────────────┤
│ Raw capture                      │ Detections overlay                 │
│                                  │                                    │
│  [ screenshot 600x340 ]          │  [ screenshot + bboxes ]           │
│                                  │     3 trees, 1 player              │
│                                  │                                    │
├──────────────────────────────────┼────────────────────────────────────┤
│ Active template                  │ Heatmap                            │
│                                  │                                    │
│  [ template tree_oak.png ]       │  [ OpenCV matchTemplate result ]   │
│                                  │     max @ (523, 411) = 0.91        │
│                                  │                                    │
└──────────────────────────────────┴────────────────────────────────────┘
```

---

## 9. Mode sombre par défaut

- Thème `Fusion` + palette custom sombre (`#1e1e1e` background, `#e0e0e0` text).
- Switch clair/sombre dans menu View.

---

## 10. Messages d'erreur utilisateur (exemples)

- Tesseract introuvable : modal avec chemin attendu + bouton "Ouvrir dossier installation".
- Fenêtre Dofus introuvable : banner jaune en haut "Dofus window not detected — start the game first".
- Template non calibré pour cette résolution : modal "Current resolution is 2560x1440 but templates are calibrated for 1920x1080. Re-capture templates or switch resolution."

---

## 11. Livrable code (Phase 2)

- `src/ui/main_window.py` : MainWindow + QTabWidget.
- `src/ui/tabs/dashboard_tab.py`
- `src/ui/tabs/debug_tab.py`
- `src/ui/tabs/config_tab.py`
- `src/ui/tabs/stats_tab.py`
- `src/ui/tabs/discord_tab.py`
- `src/ui/widgets/preview_widget.py` : QLabel spécialisé avec overlay.
- `src/ui/widgets/stats_panel.py`
- `src/ui/resources/dark.qss` : stylesheet.
- `src/ui/resources/icons/` : SVG icons (state indicators).
