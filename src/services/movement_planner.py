"""Planificateur de mouvement combat — inspiré BlueSheep CanUseSpell.

Au lieu d'approcher "à l'aveugle" en direction du mob, ce module :
  1. Détecte les cases de PM vraiment accessibles (via pm_cell_detector)
  2. Pour chaque case candidate, calcule :
     - Si on peut cast le sort voulu DEPUIS cette case (LoS + portée)
     - La distance finale au mob (préférence selon stratégie)
  3. Retourne la MEILLEURE case selon la stratégie :
     - "cast_from_here"   : cast sans bouger, sinon bouge vers cast possible
     - "keep_distance"    : reste loin du mob (sort distance)
     - "engage_melee"     : au contact (sort CaC)
     - "flee"             : s'éloigner au maximum

Inspiration :
  BlueSheep FightData.CanUseSpell — algo de choix de case optimal pour cast.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from loguru import logger

from src.services.los_detector import check_line_of_sight
from src.services.pm_cell_detector import PmCell, detect_pm_cells


CELL_PX_X = 86
CELL_PX_Y = 43


@dataclass
class MovementPlan:
    """Plan de mouvement concret."""
    action: str
    """'cast_no_move', 'move_then_cast', 'move_approach', 'move_flee',
    'end_turn', 'no_pm_cells'"""

    move_target_xy: tuple[int, int] | None = None
    """Pixel où cliquer pour bouger (None si pas de mouvement)."""

    cast_after_move: bool = False
    """True si on cast après avoir bougé (info pour le caller)."""

    reason: str = ""


def _dist_cases(a_xy: tuple[int, int], b_xy: tuple[int, int]) -> float:
    dx = abs(a_xy[0] - b_xy[0])
    dy = abs(a_xy[1] - b_xy[1])
    return max(dx / CELL_PX_X, dy / CELL_PX_Y)


def plan_movement(
    frame_bgr: np.ndarray,
    perso_xy: tuple[int, int],
    target_xy: tuple[int, int],
    *,
    spell_po_min: int = 1,
    spell_po_max: int = 5,
    spell_needs_los: bool = True,
    strategy: str = "cast_from_here",
    use_pixel_los: bool = True,
) -> MovementPlan:
    """Décide d'une stratégie de mouvement optimale.

    Args:
        frame_bgr: Capture écran.
        perso_xy: Position perso actuelle.
        target_xy: Position du mob visé.
        spell_po_min / spell_po_max: Portée du sort voulu (en cases).
        spell_needs_los: Ligne de vue requise ?
        strategy: Stratégie désirée :
            - "cast_from_here"  : cast maintenant si possible, sinon chercher
              case pour cast
            - "keep_distance"   : trouver case la plus ÉLOIGNÉE du mob d'où on
              peut cast (BlueSheep-style)
            - "engage_melee"    : au contact du mob
            - "flee"            : le plus loin possible du mob
        use_pixel_los: Utiliser raycasting pixel pour checker LoS ?

    Returns:
        MovementPlan avec action recommandée.
    """
    # Position actuelle : check si déjà dans la bonne config (cast possible)
    current_dist = _dist_cases(perso_xy, target_xy)

    def can_cast_from(pos: tuple[int, int]) -> bool:
        """Depuis `pos`, peut-on cast le sort sur target ?"""
        d = _dist_cases(pos, target_xy)
        if not (spell_po_min <= d <= spell_po_max):
            return False
        if spell_needs_los and use_pixel_los:
            los = check_line_of_sight(frame_bgr, pos, target_xy)
            if not los.is_clear:
                return False
        return True

    # Cas 1 : on peut cast depuis notre position actuelle
    if strategy == "cast_from_here" and can_cast_from(perso_xy):
        return MovementPlan(
            action="cast_no_move",
            move_target_xy=None,
            reason=f"déjà à {current_dist:.0f}c + LoS ok → cast direct",
        )

    # Détection des cases de PM disponibles
    pm_cells = detect_pm_cells(frame_bgr)
    if not pm_cells:
        # Pas de cases détectées → on n'est peut-être pas en mon_tour
        # ou le rendering n'a pas de highlight. Fallback : approche linéaire.
        if strategy in ("flee",):
            # Pas de cases visibles → on peut pas fuir proprement
            return MovementPlan(
                action="end_turn",
                reason="pas de cases PM détectées, impossible de fuir",
            )
        # Fallback : cible un point en direction du mob (ou opposé)
        dx = target_xy[0] - perso_xy[0]
        dy = target_xy[1] - perso_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        factor = 1 if strategy != "flee" else -1
        step_px = 2 * CELL_PX_X * 0.9  # ~2 cases
        fallback_xy = (
            int(perso_xy[0] + factor * (dx / length) * step_px),
            int(perso_xy[1] + factor * (dy / length) * step_px),
        )
        return MovementPlan(
            action="move_approach" if strategy != "flee" else "move_flee",
            move_target_xy=fallback_xy,
            reason="pas de cases PM HSV → déplacement linéaire",
        )

    # On a des cases candidates. Filtrer selon stratégie.
    if strategy == "flee":
        # Case la plus éloignée du mob (parmi celles détectées)
        best = max(
            pm_cells,
            key=lambda c: (c.x - target_xy[0]) ** 2 + (c.y - target_xy[1]) ** 2,
        )
        return MovementPlan(
            action="move_flee",
            move_target_xy=(best.x, best.y),
            reason=f"fuite vers case la plus loin ({best.area}px²)",
        )

    if strategy == "engage_melee":
        # Case la plus proche du mob
        best = min(
            pm_cells,
            key=lambda c: (c.x - target_xy[0]) ** 2 + (c.y - target_xy[1]) ** 2,
        )
        return MovementPlan(
            action="move_approach",
            move_target_xy=(best.x, best.y),
            reason="engagement CaC : case la plus proche du mob",
        )

    # Stratégies "cast_from_here" ou "keep_distance" : parmi les cases d'où
    # on peut cast, choisir celle qui maximise la distance au mob (BlueSheep-style).
    cast_candidates = [c for c in pm_cells if can_cast_from((c.x, c.y))]

    if not cast_candidates:
        # Aucune case permet de cast → approche vers le mob (la plus proche)
        best = min(
            pm_cells,
            key=lambda c: (c.x - target_xy[0]) ** 2 + (c.y - target_xy[1]) ** 2,
        )
        return MovementPlan(
            action="move_approach",
            move_target_xy=(best.x, best.y),
            reason="aucune case ne permet de cast → approche",
        )

    # Sélection de la meilleure case (plus loin du mob = moins de CaC)
    if strategy == "keep_distance":
        best = max(
            cast_candidates,
            key=lambda c: (c.x - target_xy[0]) ** 2 + (c.y - target_xy[1]) ** 2,
        )
        reason_suffix = "la plus éloignée du mob"
    else:  # cast_from_here
        # Préfère la plus PROCHE pour minimiser le mouvement (économise PM)
        best = min(
            cast_candidates,
            key=lambda c: (c.x - perso_xy[0]) ** 2 + (c.y - perso_xy[1]) ** 2,
        )
        reason_suffix = "la plus proche de soi (économie PM)"

    return MovementPlan(
        action="move_then_cast",
        move_target_xy=(best.x, best.y),
        cast_after_move=True,
        reason=f"case cast-ready choisie : {reason_suffix}",
    )
