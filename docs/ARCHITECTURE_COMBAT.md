# Architecture moteur combat v0.6.x

> Document d'architecture — Dofus Bot combat system
> Version : v0.6.1 (2026-04-21)

## 🎯 Philosophie

**90% des décisions combat se résolvent sans IA.** Un combat Dofus est un jeu
tour par tour déterministe : si tu sais la position du perso, des mobs, les
PA/PM dispos et les sorts accessibles, tu peux calculer la meilleure action
en une poignée de microsecondes.

Ce moteur est inspiré de [Inkybot](https://medium.com/@inkybot.me/building-inkybot-a-dofus-maging-bot-with-ocr-win32-api-and-a-rule-based-ai-212d4bb2611d)
(rule-based AI), [ArakneUtils](https://github.com/Arakne/ArakneUtils)
(algo LoS Dofus officiel), et [BlueSheep](https://github.com/Sadikk/BlueSheep)
(pathfinding MapPoint).

## 🧱 Modules

| Module | Rôle | Latence typique |
|--------|------|-----------------|
| `combat_state_reader` | HSV detection perso/mobs sur capture | ~30ms |
| `phase_detector` | Détection phase (mon_tour / popup / etc.) | **0.6ms** |
| `los_detector` | Ligne de vue par raycasting pixel (Bresenham) | **0.25ms** |
| `targeting` | Priorisation cibles (HP/distance/menace/isolation) | **0.14ms** |
| `combat_decision_engine` | Moteur rule-based principal | **0.02ms** |
| `llm_client` | Fallback LLM (Claude Haiku 4.5) pour cas ambigus | 1-3s |

## 🔀 Flux de décision (mode hybrid)

```
     ┌─────────────────┐
     │  Capture écran  │  ~30ms (MSS)
     └────────┬────────┘
              │
              ▼
     ┌─────────────────┐
     │  HSV perso+mobs │  ~30ms (combat_state_reader)
     └────────┬────────┘
              │
              ▼
     ┌─────────────────┐
     │ Phase detector  │  ~0.6ms
     │ (popup ? tour ?)│
     └────────┬────────┘
              │
         ┌────┴─────┐
   popup │          │ mon_tour / tour_ennemi
         ▼          │
   close_popup      │
                    ▼
          ┌──────────────────┐
          │ Decision engine  │  0.02-0.15ms
          │ (rule-based)     │
          └────────┬─────────┘
                   │
          ┌────────┴─────────┐
   action │                  │ defer_to_llm
          ▼                  ▼
      Exécution         LLM Claude Haiku 1-3s
                            │
                            ▼
                        Exécution
```

## 🎮 Chaîne de règles (combat_decision_engine)

Priorité décroissante :

1. **Popup détectée** → `close_popup`
2. **Pas de snap / pas d'ennemi / pas de perso** → `defer_to_llm`
3. **HP perso < 20% et mob au CaC** → fuite (position opposée)
4. **Tour 1 + buff configuré pas cast** → cast le buff self
5. **PA < plus petit coût sort** → `end_turn`
6. **Pick best target** via `score_targets()` :
   - 35% HP bas (priorité finish-kill)
   - 25% distance (moins de PM à dépenser)
   - 25% menace CaC (tacle risque)
   - 15% mob isolé (moins d'AoE subies)
7. **Mob hors portée max** → approche (click_xy vers le mob)
8. **Mob à portée** :
   - Check LoS pixel raycasting
   - LoS bloquée → `find_bypass_cell` ou perpendiculaire
   - LoS OK → `_best_offensive_spell()` : meilleur sort par
     score `pa * 10 + (5 - po_min)`
9. **Re-cast détecté** (même slot, ±50px) → override mécanique
10. **Stuck 2 fois** → `end_turn`

## 🎯 Scoring cibles (targeting.py)

```python
score = 0.35 * (1 - hp_pct)         # bas HP = priorité
      + 0.25 * (1 - dist_norm)      # proche = économie PM
      + 0.25 * melee_threat         # mob CaC = tacle
      + 0.15 * isolated_bonus       # mob seul = facile

# Bonus finish-kill si HP < 20%
if hp_pct < 0.20:
    score += 0.30
```

## 🔦 Line of Sight (los_detector.py)

Algorithme :
1. `bresenham_line(source, target)` produit la liste des pixels traversés
2. On exclut le premier 15% et le dernier 15% de la ligne (sprites perso/mob)
3. Échantillonnage 1 pixel tous les 4 (ratio précision/vitesse)
4. Chaque pixel est converti en HSV et comparé aux ranges `OBSTACLE_HSV_RANGES`
5. Si ≥12% des samples sont obstacle → **LoS BLOQUÉE**

Ranges calibrées pour Dofus 2.64 :
- **Pierre claire** : H 10-30, S 25-110, V 140-215 (murs jaunâtres)
- **Pierre sombre** : H 0-30, S 0-50, V 70-150 (colonnes grises)

Si LoS bloquée → `find_bypass_cell()` cherche une case voisine (priorité
perpendiculaire à l'axe perso→cible) qui dégage la LoS.

## 🎲 Détection phase (phase_detector.py)

3 zones analysées sur la capture :

| Zone | Ratios (x1,y1,x2,y2) | Test |
|------|----------------------|------|
| Bouton "TERMINER LE TOUR" | 0.78-0.99, 0.87-0.95 | Jaune-vert vif = `mon_tour`, gris = `tour_ennemi` |
| Popup centrale | 0.30-0.70, 0.20-0.60 | >30% pixels sombres = `popup_victoire` |
| Timeline initiative | 0.80-0.99, 0.02-0.20 | Variance élevée = en combat |

## 📊 Benchmarks

Résultats sur Windows 11 + Python 3.12 (AMD Ryzen standard) :

```
decide() simple 3 mobs         0.017ms/call  (60 243/s)
decide() 20 mobs               0.147ms/call  ( 6 817/s)
score_targets() 20 mobs        0.140ms/call  ( 7 126/s)
check_line_of_sight() 800px    0.250ms/call  ( 4 001/s)
detect_phase() 1920x1080       0.601ms/call  ( 1 665/s)
```

→ Un tick complet du moteur (capture + HSV + phase + LoS + décision)
tourne en **~35-50ms**, soit **~20-30 décisions/seconde**.

À comparer : 1 appel LLM Claude Haiku 4.5 = **1000-4000ms** par tour.

## 🔧 Modes de fonctionnement

Configurable via UI (`decision_mode`) :

- **hybrid** ⭐ (défaut) — règles first, LLM uniquement pour les cas ambigus
  (popup incertain, phase inconnue). Équilibre parfait.
- **rules** — 100% règles, zéro appel LLM. Gratuit, ~20 décisions/sec.
  Idéal pour farm intensif.
- **llm** — 100% LLM (comportement v0.4.x). Plus adaptatif aux cas inhabituels
  mais 1-4s/tour et ~$0.1/combat.

## 🧪 Tests

```bash
.venv/Scripts/python.exe tests/test_combat_decision_engine.py  # 16/16
.venv/Scripts/python.exe tests/test_los_detector.py            #  7/7
.venv/Scripts/python.exe tests/test_phase_detector.py          #  4/4
.venv/Scripts/python.exe tests/bench_engine.py                 # bench
```

**27/27 tests verts** couvrant :
- Distance iso Dofus (86/43 px/case)
- Priorisation cibles (HP bas, distance, CaC, isolation)
- Anti-boucle (re-cast détecté → bypass intelligent)
- Portée + bonus PO
- Fuite HP critique
- LoS clear / bloquée / bypass
- Détection phase (mon_tour / tour_ennemi / popup)

## 📚 Enrichissement knowledge base

Chaque classe (`data/knowledge/classes/*.json`) a **15 classes × 20-27 sorts
= 290+ sorts** avec pour chacun :
- `pa` : coût
- `po_min`, `po_max` : portée
- `type` : mono-cible / AoE / self_buff / etc.
- `ligne_de_vue` : requiert LoS
- `role` : offensif / buff / soin / debuff / deplacement
- `portee_modifiable` : sort soumis au bonus PO
- `degats`, `cooldown`, `note`

Le script `scripts/enrich_spells_roles.py` peut ré-enrichir automatiquement
si on ajoute un sort.

## 🚀 Next steps (v0.7+)

- Détection des cases vertes de PM via HSV → pathfinding A* sur grille réelle
- OCR Tesseract pour lire PA/PM/HP depuis la capture
- Templates matching bouton "TERMINER" (gère tous les resolutions)
- Support des combos multi-sorts (Karcham+Chamrak Pandawa, Bond+Épée Iop...)
- Apprentissage des patterns mob pour prédire leur tour
