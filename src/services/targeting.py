"""Priorisation de cibles pour le combat Dofus.

Stratégies implémentées (inspirées des règles des joueurs experts) :

1. **Finish kill** : mob < 20% HP → priorité absolue (ne pas le laisser vivre)
2. **Plus faible HP** : achever le plus tanké mob d'abord = moins de dégâts
   subis au tour suivant
3. **Plus proche à portée** : minimiser les déplacements
4. **Plus menaçant** : mob en corps-à-corps avec toi = priorité (tacle risque)
5. **Isolé** : mob sans alliés autour = plus facile (moins d'AoE adverses)

Plusieurs heuristiques combinées via un score pondéré :
    score = w_hp * (1 - hp_pct) + w_dist * (1 - dist_norm) + w_threat * threat
Plus le score est élevé, plus le mob est prioritaire.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.services.combat_state_reader import CombatStateSnapshot, EntityDetection


# Poids des critères de scoring (somme = 1.0 pour lisibilité)
SCORE_WEIGHTS = {
    "hp_low": 0.35,       # HP bas = priorité (achever)
    "distance": 0.25,     # Proche = économie PM
    "threat_melee": 0.25, # Mob au CaC = risque de tacle
    "isolated": 0.15,     # Isolé = plus facile à tuer sans AoE
}

# Constantes Dofus iso (cohérent avec combat_decision_engine)
CELL_PX_X = 86
CELL_PX_Y = 43
MELEE_THRESHOLD_CASES = 1.5  # si dist < 1.5 cases, mob est au CaC


@dataclass
class TargetScore:
    """Score détaillé d'une cible potentielle."""
    entity: EntityDetection
    score: float
    distance_cases: float
    is_finish_kill: bool
    is_melee_threat: bool
    reasoning: str


def _dist_cases(a_xy: tuple[int, int], b_xy: tuple[int, int]) -> float:
    dx = abs(a_xy[0] - b_xy[0])
    dy = abs(a_xy[1] - b_xy[1])
    return max(dx / CELL_PX_X, dy / CELL_PX_Y)


def score_targets(
    snap: CombatStateSnapshot,
    *,
    max_relevant_distance: float = 10.0,
) -> list[TargetScore]:
    """Calcule un score pour chaque ennemi détecté.

    Args:
        snap: Snapshot combat (doit contenir perso et ennemis).
        max_relevant_distance: Distance max (en cases) pour normaliser la
            composante distance. Un mob à 10+ cases est considéré "loin".

    Returns:
        Liste de TargetScore triés par score décroissant (meilleur en tête).
    """
    if not snap.perso or not snap.ennemis:
        return []

    perso_xy = (snap.perso.x, snap.perso.y)
    enemies = snap.ennemis
    scores: list[TargetScore] = []

    for enemy in enemies:
        enemy_xy = (enemy.x, enemy.y)
        dist = _dist_cases(perso_xy, enemy_xy)

        # Composante 1 : HP bas (achever en priorité)
        hp_pct = enemy.hp_pct / 100.0 if enemy.hp_pct is not None else 0.5
        # hp_pct inconnu → on suppose 50%
        hp_component = 1.0 - hp_pct
        is_finish_kill = hp_pct < 0.20

        # Composante 2 : distance (normalisée)
        dist_norm = min(dist / max_relevant_distance, 1.0)
        dist_component = 1.0 - dist_norm

        # Composante 3 : menace CaC (si mob à <1.5 cases)
        is_melee = dist <= MELEE_THRESHOLD_CASES
        threat_component = 1.0 if is_melee else 0.0

        # Composante 4 : mob isolé (pas d'autre ennemi à moins de 2 cases)
        nearby = sum(
            1 for other in enemies
            if other is not enemy and _dist_cases(enemy_xy, (other.x, other.y)) < 2.0
        )
        isolated_component = 1.0 if nearby == 0 else 1.0 / (1 + nearby)

        total = (
            SCORE_WEIGHTS["hp_low"] * hp_component
            + SCORE_WEIGHTS["distance"] * dist_component
            + SCORE_WEIGHTS["threat_melee"] * threat_component
            + SCORE_WEIGHTS["isolated"] * isolated_component
        )

        # Boost finish kill (override partiel du score)
        if is_finish_kill:
            total += 0.3  # gros bonus pour achever

        reasons = []
        if is_finish_kill:
            reasons.append(f"FINISH {int(hp_pct * 100)}%HP")
        if is_melee:
            reasons.append("CaC")
        reasons.append(f"dist={dist:.1f}c")
        if hp_pct < 1.0:
            reasons.append(f"hp={int(hp_pct * 100)}%")
        if nearby == 0:
            reasons.append("isolé")

        scores.append(TargetScore(
            entity=enemy,
            score=total,
            distance_cases=dist,
            is_finish_kill=is_finish_kill,
            is_melee_threat=is_melee,
            reasoning=", ".join(reasons),
        ))

    scores.sort(key=lambda s: -s.score)
    return scores


def pick_best_target(snap: CombatStateSnapshot) -> TargetScore | None:
    """Raccourci : retourne la meilleure cible ou None."""
    scores = score_targets(snap)
    return scores[0] if scores else None
