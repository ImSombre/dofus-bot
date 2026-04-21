# Architecture moteur combat — v0.7.0

> Dofus Bot combat system — nuit du 2026-04-21/22

## 🎯 Philosophie

**Le LLM n'est plus le chef d'orchestre — il est le filet de sécurité.**

La majorité des décisions combat sont calculables en <1ms à partir de :
- positions perso + mobs (HSV)
- cases de PM accessibles (HSV vert)
- ligne de vue (raycasting pixels)
- portée + coût des sorts (knowledge DB)

Le LLM n'intervient que pour les cas **vraiment ambigus** (popup incertain, scène inhabituelle, multi-cible complexe avec dilemme tactique).

Inspirations et sources étudiées :
- [ArakneUtils](https://github.com/Arakne/ArakneUtils) — algo officiel LoS + A* Dofus (Java)
- [BlueSheep](https://github.com/Sadikk/BlueSheep) — `CanUseSpell` : choix case optimale pour cast (C#)
- [Inkybot](https://medium.com/@inkybot.me/building-inkybot-a-dofus-maging-bot-with-ocr-win32-api-and-a-rule-based-ai-212d4bb2611d) — rule-based AI + OCR + Win32 API
- [BezMouse](https://github.com/vincentbavitz/bezmouse) — Bézier mouse (400h+ sans détection)
- [Dofus Wiki Line of Sight](https://dofuswiki.fandom.com/wiki/Line_of_Sight) — règles officielles
- Guides classes : DofHub, Millenium, Wiki-Dofus, forum officiel

## 🧱 Architecture (8 modules)

```
┌──────────────────────────────────────────────────────────────────┐
│                   VisionCombatWorker (QThread)                   │
│ ┌────────────────────────────────────────────────────────────┐   │
│ │ capture écran → HSV → phase → règles → action → exécution  │   │
│ └────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
       │                                                │
       ▼                                                ▼
┌────────────────┐                              ┌──────────────┐
│ combat_state   │                              │ human_input  │
│ _reader        │                              │ (Bézier)     │
│ (HSV perso/mob)│                              └──────┬───────┘
└───────┬────────┘                                     │
        │                                              ▼
        ▼                                          InputService
┌────────────────────┐                            (clavier)
│ phase_detector     │
│ (bouton + popup)   │
└────────────────────┘
        │
        ▼
┌───────────────────────────────────────────┐
│     combat_decision_engine (rule-based)   │
│                                           │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │targeting │  │los       │  │movement │  │
│  │ (score)  │  │_detector │  │_planner │  │
│  └──────────┘  └──────────┘  └─────────┘  │
│                                           │
│  ┌──────────────────────────────────────┐ │
│  │     combat_knowledge (JSON)          │ │
│  │  15 classes, 290+ sorts avec role    │ │
│  │  + playbooks tour-type par classe    │ │
│  └──────────────────────────────────────┘ │
└────────────────┬──────────────────────────┘
                 │ defer_to_llm (cas rare)
                 ▼
         ┌───────────────┐
         │   llm_client  │
         │ Claude Haiku  │
         └───────────────┘
                 │
                 ▼
         ┌───────────────┐
         │ stats_tracker │
         │ (persistent)  │
         └───────────────┘
```

## 📦 Modules détaillés

### `combat_decision_engine.py` — Cœur du système

Chaîne de règles (priorité décroissante) :

1. Popup victoire/défaite → `close_popup` (via phase_detector)
2. Pas de snap / ennemi / perso → defer LLM
3. HP perso < 20% + mob au CaC → fuite (via movement_planner strategy=flee)
4. Tour 1 + buff configuré pas encore cast → cast buff self
5. PA insuffisants (< coût minimum) → end_turn
6. Pick meilleure cible (`targeting.score_targets`)
7. Mob hors portée max → mouvement via movement_planner
8. Mob à portée :
   - Check LoS pixel raycasting
   - LoS OK → meilleur sort offensif selon playbook + PA dispos
   - LoS bloquée → `find_bypass_cell` ou perpendiculaire
9. Re-cast détecté sur même cible → override mécanique
10. Fallback → defer LLM

### `targeting.py` — Priorisation cibles

Score multi-critères pondéré :
```
score = 0.35 * (1 - hp_pct)           # bas HP = priorité
      + 0.25 * (1 - dist_norm)        # proche = économie PM
      + 0.25 * is_melee_threat        # mob CaC = tacle risque
      + 0.15 * isolated_bonus         # mob seul = facile
      + 0.30 if hp_pct < 20%          # finish-kill boost
```

Trois helpers :
- `score_targets(snap)` → liste triée
- `pick_best_target(snap)` → raccourci top 1

### `los_detector.py` — Ligne de vue pixel

Algorithme :
1. `bresenham_line(source, target)` → pixels traversés
2. Exclure 15% début/fin (sprites)
3. Échantillonnage 1px tous les 4
4. Chaque pixel → HSV → check ranges obstacle (pierre claire/sombre)
5. Si ≥12% samples = obstacle → LoS BLOQUÉE
6. `find_bypass_cell()` : essaie 8 directions (priorité perpendiculaires) pour trouver case qui dégage

### `pm_cell_detector.py` — Cases de mouvement

Détection des cases vertes affichées en combat (tu peux cliquer dessus).
1. Mask HSV vert PM (H: 45-85, S: 100-255, V: 120-255)
2. Exclusion zones UI (bas + timeline + bouton terminer)
3. Morphologie (opening + closing) pour nettoyer bruit
4. Connected components → chaque composant = 1 case
5. Filtre aires (500 ≤ area ≤ 20000 px²)
6. Retourne centroids cliquables

### `movement_planner.py` — Stratégies de mouvement

Inspiration directe de `BlueSheep.FightData.CanUseSpell`.

Entrées : frame, perso_xy, target_xy, sort (po_min/po_max/LoS), stratégie.

Stratégies :
- `cast_from_here` : cast actuel si possible, sinon bouge vers case qui permet cast (plus proche de soi = économie PM)
- `keep_distance` : case la plus ÉLOIGNÉE du mob qui permet cast (évite tacle, style Cra)
- `engage_melee` : case la plus PROCHE du mob (Iop, Sacri)
- `flee` : case la plus éloignée du mob

### `phase_detector.py` — Détection phase

3 zones HSV analysées :
- Bouton "TERMINER LE TOUR" (bas-droite) : jaune-vert vif = mon_tour, gris = tour_ennemi
- Popup centrale : >30% pixels sombres = popup_victoire
- Timeline initiative (haut-droite) : variance élevée = en combat

~0.6ms/détection.

### `human_input.py` — Anti-détection mouse

- `human_mouse_path(start, end)` : Bézier quadratique + jitter ±2px + easing sinusoïdal
- `human_delay(low, high)` : distribution log-normale (variance réaliste)
- `human_click_offset(radius)` : offset gaussien ±radius px
- `human_click(input, x, y)` : orchestration complète

Cible : trajectoires souris indistinguables d'un humain, 400h+ sans détection (BezMouse track record).

### `combat_knowledge` (JSON enrichi)

Chaque classe (`data/knowledge/classes/*.json`) :
- 20-27 sorts avec `pa`, `po_min`, `po_max`, `ligne_de_vue`, `degats`, `cooldown`
- **Champ `role`** (ajouté v0.6.0) : offensif / buff / soin / debuff / deplacement / invoc / trap
- **Champ `portee_modifiable`** : sort soumis au bonus PO
- **`tour_type_pve_solo`** (ajouté v0.7.0) : playbook détaillé
  - `tour_1` : séquence idéale premier tour (buffs puis burst)
  - `tour_n` : séquence récurrente
  - `priority_order` : si plusieurs sorts dispo, lequel prioriser
  - `combos` : enchaînements multi-sorts
  - `conseils` : notes de gameplay

Classes enrichies avec playbook : pandawa, iop, cra, sacrieur, xelor, eniripsa, ecaflip (7/15).

### `combat_stats_tracker.py` — Télémétrie

Persisté dans `data/combat_stats.json` :
- Total combats / victoires / défaites / fuites
- Kills, tours joués, durée moyenne
- Nb décisions moteur vs LLM
- Latence LLM moyenne
- Win rate %

API :
- `on_combat_start(class)`, `on_cast(slot)`, `on_turn()`, `on_kill()`, `on_combat_end(outcome)`
- `on_decision("rules"|"llm", latency_ms)`
- `format_summary()` → string humaine

## 📊 Benchmarks (Windows 11 + Python 3.12)

```
decide() simple 3 mobs          0.015ms/call  (66 086/s)
decide() 20 mobs                0.143ms/call  ( 6 979/s)
score_targets() 20 mobs         0.136ms/call  ( 7 360/s)
check_line_of_sight() 800px     0.247ms/call  ( 4 045/s)
check_line_of_sight() 1500px    0.390ms/call  ( 2 564/s)
detect_phase() 1920x1080        0.602ms/call  ( 1 660/s)
human_mouse_path 500×500        ~0.5ms
```

Tick complet typique (capture + HSV + phase + règles) : **35-50ms**
→ ~20-30 décisions/seconde en mode règles

Comparaison : appel LLM Claude Haiku 4.5 = 1000-4000ms.
**~100-500× plus rapide**.

## 🧪 Tests (48/48)

| Suite | Tests | Couverture |
|-------|-------|------------|
| `test_combat_decision_engine.py` | 16 | Règles moteur + targeting + anti-boucle + fuite + PO |
| `test_los_detector.py` | 7 | Bresenham + LoS clear/bloquée + bypass |
| `test_phase_detector.py` | 4 | mon_tour / tour_ennemi / popup |
| `test_pm_cell_detector.py` | 7 | Détection cases vertes + filtrage UI + areas |
| `test_movement_planner.py` | 6 | Stratégies cast/flee/engage/fallback |
| `test_human_input.py` | 8 | Bézier + jitter + délais + offset |
| **TOTAL** | **48** | **✓ 100%** |

## 🎛️ Modes utilisateur

UI (onglet combat) :

### Mode décision
- **Hybride** ⭐ — règles + LLM fallback. Équilibre idéal (défaut).
- **Règles only** — 0 appel LLM = gratuit. ~20 dec/s. Idéal farm nocturne intensif.
- **LLM only** — comportement v0.4.x. Plus adaptatif mais 1-4s/tour, $0.1/combat.

### Toggles
- **LoS pixel** — active le raycasting pour détection murs (recommandé)
- **Humaniser souris** — Bézier + jitter + délais log-normal (recommandé)

## 🚀 Pipeline complet d'une décision

Temps moyen 35-45ms/tick :

| Étape | Latence |
|-------|---------|
| `mss` capture écran | ~25ms |
| `combat_state_reader` HSV perso/mob | ~5ms |
| `phase_detector` | 0.6ms |
| `combat_decision_engine` règles | 0.02-0.15ms |
| ↪ `score_targets` | 0.14ms |
| ↪ `los_detector` si needed | 0.25ms |
| ↪ `movement_planner` si needed | ~5ms (inclut pm_cell_detector) |
| **Total** | **~36ms** |

Si fallback LLM : +1000-3000ms (un ordre de grandeur pire).

## 🎯 Résumé v0.7.0 vs v0.4.x

| Aspect | v0.4.x (tout LLM) | v0.7.0 (hybride) |
|--------|-------------------|------------------|
| Latence décision | 1-4s | **~36ms** en mode règles |
| Coût API | ~$0.1/combat | **~$0.01/combat** (LLM rare) |
| Précision targeting | LLM hallucine parfois | **Déterministe** |
| Détection LoS | LLM voit image | **Raycasting pixel HSV** |
| Position mouvement | LLM approximatif | **Cases PM détectées + stratégie** |
| Anti-détection | Clic pixel exact | **Bézier mouse + jitter + offset** |
| Stats/télémétrie | Aucune | **Persistées** (combats/kills/win rate) |
| Tests | 0 | **48** |

## 🔬 Pour aller plus loin (v0.8+)

- **OCR Tesseract** pour PA/PM/HP (lecture directe barre de vie) — pas besoin de tracker manuellement
- **Template matching** pour bouton "TERMINER" et popups (plus robuste que HSV brut)
- **Pathfinding A* réel** sur grille détectée (plus précis que "case la plus proche")
- **Détection mobs par YOLO** pour typer le mob (Piou, Prespic, Bouftou...) et adapter stratégie
- **Combos multi-sorts** auto-détectés (Karcham+Chamrak, Bond+Épée, etc.)
- **Apprentissage patterns mob** pour prédire leur tour
