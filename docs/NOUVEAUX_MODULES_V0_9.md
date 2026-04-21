# Nouveautés v0.9.0 — Profils combat type Snowbot

## 🎯 Inspiré Snowbot

Snowbot permet de créer des IA combat en **drag-and-drop de blocs** (conditions + actions). On reproduit la logique équivalente en JSON **human-editable** : tu décris ton IA en règles priorité/conditions/action, et le moteur l'exécute.

## 📦 Profils de combat (data/profiles/*.json)

Un profil = **classe + raccourcis sorts + règles + config**. Portable, partageable.

### 3 profils pré-faits

| Profil | Classe | Style |
|--------|--------|-------|
| **Pandawa Burst PvM** | Pandawa | Picole T1 → Vulnérabilité → Gueule de Bois focus faible |
| **Iop Engage Brutal** | Iop | Compulsion T1 → Bond → Épée Céleste burst |
| **Cra Turret Distance** | Cra | Tir Éloigné → Flèche Destructrice distance, Recul si CaC |

### Comment utiliser

1. Dans l'onglet **Combattre**, panneau **"📦 Profil combat"** en haut
2. Choisis un profil dans le dropdown
3. Clique **"✅ Appliquer"** → classe + raccourcis + PA/PM/règles sont configurés
4. Lance le combat → le bot respecte les règles du profil en priorité

### Sauvegarder ton propre profil

1. Configure classe + raccourcis comme d'habitude
2. (Optionnel) Crée tes règles dans `data/profiles/mon_profil.json`
3. Clique **"💾 Sauver profil"** → renseigne un nom
4. Il apparaît dans le dropdown au prochain lancement

## 🧠 Système de règles (combat_rules.py)

Chaque règle a : **priorité + conditions + action**.
Le moteur évalue les règles par priorité décroissante, la première qui match s'exécute.

### Conditions disponibles

| Type | Description | Exemple |
|------|-------------|---------|
| `turn_number` | Numéro du tour | `{"type":"turn_number","op":"==","value":1}` |
| `pa_remaining` | PA restants | `{"type":"pa_remaining","op":">=","value":5}` |
| `pm_remaining` | PM restants | |
| `hp_pct_self` | % HP du perso | `{"type":"hp_pct_self","op":"<","value":30}` |
| `enemy_count` | Nb ennemis visibles | |
| `enemy_at_range` | Nb ennemis à portée X cases | `{"type":"enemy_at_range","op":">=","value":1,"range":5}` |
| `melee_enemy` | Mob au CaC ? (0/1) | `{"type":"melee_enemy","op":"==","value":1}` |
| `lowest_enemy_hp_pct` | HP% du mob le plus faible | |
| `nearest_enemy_dist_cases` | Distance du mob le plus proche | |

Opérateurs : `==`, `!=`, `>`, `<`, `>=`, `<=`

### Actions disponibles

```json
{"type": "cast_spell", "slot": 2, "target": "nearest_enemy"}
{"type": "cast_spell", "slot": 5, "target": "self"}
{"type": "cast_spell", "slot": 3, "target": "lowest_hp"}
{"type": "click_xy", "target_xy": [1200, 800]}
{"type": "press_key", "key": "escape"}
{"type": "end_turn"}
```

Targets symboliques : `self`, `nearest_enemy`, `lowest_hp`, `highest_threat`, `[x, y]`.

### Exemple complet

```json
{
  "name": "Pandawa Burst PvM",
  "class": "pandawa",
  "spell_shortcuts": {
    "2": "gueule_de_bois",
    "5": "picole"
  },
  "rules": [
    {
      "name": "Buff Picole T1",
      "priority": 100,
      "conditions": [
        {"type": "turn_number", "op": "==", "value": 1},
        {"type": "pa_remaining", "op": ">=", "value": 2}
      ],
      "action": {"type": "cast_spell", "slot": 5, "target": "self"}
    },
    {
      "name": "Gueule de Bois si 3+ PA",
      "priority": 50,
      "conditions": [
        {"type": "pa_remaining", "op": ">=", "value": 3},
        {"type": "enemy_at_range", "op": ">=", "value": 1, "range": 5}
      ],
      "action": {"type": "cast_spell", "slot": 2, "target": "lowest_hp"}
    }
  ],
  "config": {
    "starting_pa": 10,
    "starting_pm": 5,
    "decision_mode": "hybrid"
  }
}
```

## 🔄 Intégration dans le moteur

Ordre d'évaluation pour chaque tick combat :

1. **Règles custom** (profil user) — évaluées en 1er, priorité absolue
2. **Règles hardcoded** (chain-of-responsibility v0.7.0)
3. **LLM fallback** (v0.4.0) si mode hybride et cas ambigu

Si aucune règle custom ne match → flow hardcoded classique.

## 🧪 Tests

+18 nouveaux tests :
- 13 combat_rules (conditions + évaluation + matching + actions)
- 5 combat_profiles (dict roundtrip + save/load + liste)

**Total projet : 80/80 tests verts**.

## 📚 Pour créer tes propres règles

1. Édite `data/profiles/mon_profil.json`
2. Ajoute des règles selon ton gameplay préféré
3. Priorités élevées = testées en premier (buff T1 = 100, finish = 90, burst normal = 50, end_turn cleanup = 10)
4. Redémarre le bot, sélectionne le profil, applique, farm.

## 🆚 vs Snowbot

| Feature | Snowbot | Notre bot v0.9 |
|---------|---------|----------------|
| Architecture | SOCKET MITM | Vision HSV + LLM fallback |
| Éditeur | Drag-drop visuel | JSON éditable |
| Conditions | 50+ blocs | 9 types + 6 opérateurs |
| Actions | Sorts + targeting + positioning | cast_spell/click/key/end_turn |
| Paths | Map interactive Dofus | — (v1.0 futur) |
| FM | Intégrée | fm_worker (Tesseract) |
| Donjons | Scripts community | 3 donjons prédéfinis |
| Prix | 50€/mois | Gratuit |
| Détection | Undetectable (socket) | Vision (nécessite anti-détection human_input) |

On reproduit le **core concept** (règles configurables) en plus léger et gratuit, avec l'approche vision qui est plus sûre contre les bans majeurs.
