# Nouveautés v0.8.0

## 🎨 Calibrator HSV (pour ton jeu à toi)

**Pourquoi** : les ranges de couleurs par défaut (cases PM vertes, murs en pierre, etc.) sont calibrées "à l'aveugle". Sur Retrozia, ton rendu peut être légèrement différent (contrast, resolution, mod client).

**Comment** :
1. Dans le bot, clique sur **"🎨 Calibrer HSV"** dans le footer
2. Une capture live de Dofus s'ouvre dans une fenêtre dédiée
3. Sélectionne la catégorie à calibrer (ex "pm_cell")
4. Clique sur plusieurs pixels représentatifs de cette couleur
5. Répète pour chaque catégorie (obstacle_stone_light, enemy_circle, etc.)
6. **"💾 Sauvegarder"** → les ranges sont persistées dans `data/knowledge/hsv_calibration.json`
7. Les modules `pm_cell_detector` et `los_detector` chargent automatiquement ces ranges au prochain combat.

Catégories disponibles :
- `pm_cell` : cases vertes de déplacement
- `obstacle_stone_light` : murs beige/pierre claire
- `obstacle_stone_dark` : colonnes sombres
- `enemy_circle` : cercle bleu sous les mobs
- `player_circle` : cercle rouge sous ton perso
- `end_turn_button_active` : bouton TERMINER LE TOUR jaune-vert vif

## 🎯 Debug Visualizer

**Pourquoi** : voir ce que le bot voit et décide à chaque tick.

**Comment** :
1. Dans l'onglet combat, active **"💾 Sauvegarder captures debug"**
2. Lance un combat normalement
3. Les images annotées vont dans `data/vision_debug/tick_{HHMMSS}_{action}.jpg`

Chaque image contient :
- Rectangle rouge autour de ton perso + label `PERSO PA=X tour N`
- Rectangle bleu autour des mobs + HP %
- Rectangle **jaune** autour de la cible choisie
- Petits cercles verts sur les cases PM détectées
- Flèche cyan si un mouvement est prévu
- Bandeau texte en haut : action type + raison

Ces images te permettent de voir si :
- La détection HSV fonctionne (si les rectangles ne matchent pas les mobs → HSV à recalibrer)
- Le moteur choisit la bonne cible
- Les cases PM sont bien détectées
- La LoS trace correspond à la réalité

## ⚒️ Forgemagie Worker

**Pourquoi** : mage des items en masse. Le bot lit les stats actuelles via OCR (Tesseract) et applique les runes selon tes objectifs.

**Usage actuel** (v0.8.0 — basique, à étendre) :
```python
from src.services.fm_worker import FmWorker, FmConfig, StatObjective

config = FmConfig(
    objectives=[
        StatObjective(name="Force", min_value=30, max_value=50, priority=10),
        StatObjective(name="Vitalité", min_value=40, max_value=60, priority=8),
    ],
    item_stats_region=(100, 200, 800, 800),  # zone de l'UI où lire les stats
    rune_apply_position=(500, 700),  # bouton "Mager"
)
worker = FmWorker(vision, input_svc, config)
worker.start()
```

**Prérequis** : Tesseract installé + dans le PATH. Sinon le worker échoue avec message clair.
Install Windows : https://github.com/UB-Mannheim/tesseract/wiki

## 🏰 Dungeon Runner

**Pourquoi** : enchaîner les salles d'un donjon en automatique (scan mob → fight → transition salle suivante → boss).

**Usage actuel** (v0.8.0 — basique) :
```python
from src.services.dungeon_runner_worker import (
    DungeonRunnerWorker, DungeonRunnerConfig, DungeonConfig,
)

dungeon = DungeonConfig.from_file("data/knowledge/dungeons/incarnam.json")
config = DungeonRunnerConfig(
    dungeon=dungeon,
    combat_worker_factory=lambda: build_combat_worker_for_my_class(...),
)
worker = DungeonRunnerWorker(vision, input_svc, config)
worker.start()
```

**Donjons prédéfinis** :
- `incarnam` — niveau 1-15, 4 salles, boss Milimilou
- `bouftous` — niveau 15-40, 5 salles, boss Bouftou Royal
- `champs_patures` — niveau 20-50, 5 salles

Ajoute ton propre donjon : crée `data/knowledge/dungeons/ton_donjon.json` avec le même format.

## 📊 Récap technique

**Nouveaux modules** (v0.8.0) :
| Module | Rôle | Tests |
|--------|------|-------|
| `debug_visualizer` | Frames annotées pour diag | 4 |
| `hsv_calibrator` | GUI calibration couleurs | - |
| `fm_ocr` | OCR Tesseract pour stats item | 7 |
| `fm_worker` | Worker forgemagie rule-based | - |
| `dungeon_runner_worker` | Enchaîne salles donjons | 3 |

**Total tests projet : 62/62 verts** sur 9 suites :
- 16 combat_decision_engine
- 7 los_detector
- 4 phase_detector
- 7 pm_cell_detector
- 6 movement_planner
- 8 human_input
- 4 debug_visualizer ✨
- 7 fm_ocr ✨
- 3 dungeon_runner ✨

## 🛠 Prochaines étapes (v0.9+)

- UI dédiée pour FM (slots stats, recap items magés)
- UI dédiée pour donjons (sélection donjon + lancement)
- Plus de donjons prédéfinis (Abraknyde, Kanigrou, Saroupial…)
- Tesseract pour lire PA/PM/HP du perso en combat (actuellement tracké manuellement)
- Template matching pour bouton TERMINER (plus robuste que HSV)
