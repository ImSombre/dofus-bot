"""Générateur de combat_rules depuis un replay de session utilisateur.

Lit un fichier replay JSONL (produit par replay_recorder) et analyse les
patterns d'actions pour générer un profil `combat_rules` :

  1. Découpe le replay en "tours de combat" (détection de transitions :
     F1 pressé = fin de tour, pause de 10s = tour ennemi, etc.)
  2. Pour chaque tour, associe les touches/clics aux frames précédentes
  3. Génère des règles du type :
     - "si PA = X et enemy_at_range 5 >= 1 → cast slot Y sur nearest_enemy"
  4. Agrège les règles identiques, augmente leur priorité = fréquence

Le profil généré peut être sauvegardé comme CombatProfile puis chargé dans
le bot pour jouer automatiquement comme l'utilisateur.

Limites :
  - Ne capture que les actions AU NIVEAU DES TOUCHES/CLICS. Si l'user prend
    une décision complexe (ex: attirer mob pour qu'un autre mob soit CaC),
    la règle générée sera approximative.
  - Nécessite au moins 5-10 combats enregistrés pour des règles stables.
  - Les clics de déplacement sont traduits en click_xy avec coords fixes,
    ce qui n'est pas idéal — v2 pourra intégrer direction symbolique.

Usage :
    from src.services.rule_generator import generate_profile_from_replay
    profile = generate_profile_from_replay("data/replays/session_xxx.jsonl",
                                           class_name="pandawa",
                                           spell_shortcuts={...})
    profile.save()
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from src.services.combat_profiles import CombatProfile


# Mapping AZERTY slot -> nom touche (inverse de _AZERTY_SLOT_KEYS dans worker)
_KEY_TO_SLOT = {
    "&": 1, "é": 2, '"': 3, "'": 4, "(": 5,
    "-": 6, "è": 7, "_": 8, "ç": 9, "à": 0,
}


def _load_events(path: str | Path) -> list[dict]:
    """Charge tous les events JSONL du replay."""
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("Load replay échec : {}", exc)
    return events


def _extract_turns(events: list[dict]) -> list[list[dict]]:
    """Découpe les events en tours (séparés par F1/escape ou pauses longues)."""
    turns: list[list[dict]] = []
    current: list[dict] = []
    last_event_t = 0.0
    for ev in events:
        t = float(ev.get("t", 0.0))
        is_turn_end = False
        # Fin de tour = F1 pressé
        if ev.get("type") == "key":
            key = str(ev.get("key", "")).lower()
            if key in ("key.f1", "f1"):
                is_turn_end = True
        # Pause longue (>8s) = probable tour ennemi
        if current and (t - last_event_t) > 8.0:
            if current:
                turns.append(current)
                current = []
        current.append(ev)
        last_event_t = t
        if is_turn_end:
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _classify_frame_state(frame: dict) -> dict:
    """Retourne une signature simplifiée d'une frame :
    enemies_count, nearest_enemy_dist_cases (approx), hp_pct_self, pa_visible."""
    out = {
        "enemies_count": len(frame.get("enemies", [])),
        "hp_pct_self": frame.get("hp_pct_self"),
        "pa_visible": frame.get("pa_visible"),
    }
    perso = frame.get("perso_xy")
    enemies = frame.get("enemies", [])
    if perso and enemies:
        # Distance iso approx : max(|dx|/86, |dy|/43)
        dists = []
        for e in enemies:
            d = max(
                abs(e[0] - perso[0]) / 86,
                abs(e[1] - perso[1]) / 43,
            )
            dists.append(d)
        out["nearest_dist_cases"] = round(min(dists), 1)
    return out


def _find_last_frame_before(events: list[dict], idx: int) -> dict | None:
    """Retourne le dernier event type=frame avant l'index idx."""
    for j in range(idx - 1, -1, -1):
        if events[j].get("type") == "frame":
            return events[j]
    return None


def _infer_action_from_event(event: dict) -> dict | None:
    """Convertit un event key/click en action combat_rules compatible."""
    etype = event.get("type")
    if etype == "key":
        key = str(event.get("key", "")).strip("'\"")
        # Mapping touche AZERTY → slot
        if key in _KEY_TO_SLOT:
            slot = _KEY_TO_SLOT[key]
            return {"type": "cast_spell", "slot": slot, "target": "nearest_enemy"}
        if key.lower() in ("f1", "key.f1"):
            return {"type": "end_turn"}
    if etype == "click":
        # Clic gauche = soit cast (après touche), soit déplacement
        # Note : on ne sait pas sans contexte précédent. v1 = on ignore les clics
        # isolés ; v2 = on analyse la séquence clé-clic pour capter casts.
        return None
    return None


def generate_profile_from_replay(
    replay_path: str | Path,
    class_name: str = "ecaflip",
    spell_shortcuts: dict[int, str] | None = None,
    profile_name: str | None = None,
) -> CombatProfile | None:
    """Analyse un replay et génère un profil combat_rules.

    Args:
        replay_path: fichier JSONL produit par ReplayRecorder.
        class_name: classe du perso (pour le profil résultat).
        spell_shortcuts: {slot: nom_sort} pour renseigner le profil.
        profile_name: nom du profil généré. Défaut = basé sur filename.
    """
    events = _load_events(replay_path)
    if not events:
        logger.warning("Replay vide ou introuvable : {}", replay_path)
        return None

    turns = _extract_turns(events)
    logger.info("Replay {} : {} events, {} tours détectés",
                Path(replay_path).name, len(events), len(turns))

    # Comptage {(situation, action) : count} pour agrégation
    pattern_counts: Counter[tuple] = Counter()
    # Pour chaque touche détectée, associer à la frame précédente
    for turn_events in turns:
        for idx, ev in enumerate(turn_events):
            if ev.get("type") != "key":
                continue
            key = str(ev.get("key", "")).strip("'\"")
            if key not in _KEY_TO_SLOT and key.lower() not in ("f1", "key.f1"):
                continue

            # Frame précédente
            prev_frame = _find_last_frame_before(turn_events, idx)
            if not prev_frame:
                continue
            state = _classify_frame_state(prev_frame)

            # Signature simplifiée (buckets pour agrégation)
            pa_bucket = _bucket_pa(state.get("pa_visible"))
            dist_bucket = _bucket_dist(state.get("nearest_dist_cases"))
            enemies_bucket = min(state.get("enemies_count", 0), 5)

            action = _infer_action_from_event(ev)
            if not action:
                continue
            slot = action.get("slot", 0)
            action_type = action.get("type", "")

            signature = (pa_bucket, dist_bucket, enemies_bucket, action_type, slot)
            pattern_counts[signature] += 1

    if not pattern_counts:
        logger.warning("Aucun pattern détecté dans le replay")
        return None

    # Transforme les patterns en règles triées par fréquence
    rules: list[dict] = []
    for (pa_b, dist_b, enemies_b, atype, slot), count in pattern_counts.most_common():
        conditions = []
        if pa_b is not None:
            conditions.append({"type": "pa_remaining", "op": ">=", "value": pa_b})
        if dist_b is not None:
            conditions.append({"type": "enemy_at_range", "op": ">=", "value": 1, "range": dist_b})
        if enemies_b > 0:
            conditions.append({"type": "enemy_count", "op": ">=", "value": 1})

        rule: dict[str, Any] = {
            "name": f"Pattern observé {count}x",
            "priority": min(100, 10 + count * 5),  # plus fréquent = priorité plus haute
            "conditions": conditions,
        }
        if atype == "cast_spell":
            rule["action"] = {
                "type": "cast_spell",
                "slot": slot,
                "target": "nearest_enemy",
            }
        elif atype == "end_turn":
            rule["action"] = {"type": "end_turn"}
        else:
            continue
        rules.append(rule)

    name = profile_name or f"Learned from {Path(replay_path).stem}"
    profile = CombatProfile(
        name=name,
        class_name=class_name,
        spell_shortcuts={str(k): v for k, v in (spell_shortcuts or {}).items()},
        rules=rules,
        config={
            "starting_pa": 10,
            "starting_pm": 5,
            "decision_mode": "hybrid",
        },
        description=(
            f"Profil généré automatiquement depuis {Path(replay_path).name}. "
            f"{len(events)} events, {len(turns)} tours, {len(rules)} règles extraites."
        ),
        author="rule_generator",
    )
    logger.info("Profil généré : {} règles (top: {})",
                len(rules), rules[0]["name"] if rules else "-")
    return profile


def _bucket_pa(pa: int | None) -> int | None:
    """Bucket PA pour agrégation (3 = 'PA >= 3', 6 = 'PA >= 6')."""
    if pa is None:
        return None
    if pa >= 6:
        return 6
    if pa >= 4:
        return 4
    if pa >= 3:
        return 3
    if pa >= 2:
        return 2
    return None


def _bucket_dist(dist: float | None) -> int | None:
    """Bucket distance pour enemy_at_range (1, 3, 5, 10 cases)."""
    if dist is None:
        return None
    if dist <= 1.5:
        return 1
    if dist <= 3:
        return 3
    if dist <= 5:
        return 5
    return 10
