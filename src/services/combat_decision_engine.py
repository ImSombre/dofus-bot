"""Moteur de décision combat déterministe (rule-based).

Remplace le LLM pour 90% des décisions combat simples. Inspiration : Inkybot
(rule-based AI, chain-of-responsibility, décisions <100ms).

Usage :
    engine = CombatDecisionEngine(config, knowledge)
    action = engine.decide(snapshot, pa_remaining, cast_history)
    if action.type == "defer_to_llm":
        # cas ambigu → tomber sur le LLM
        ...
    else:
        # action déterministe trouvée, exécuter
        ...

Décisions produites (format compatible vision_combat_worker) :
    {"type": "cast_spell", "spell_key": 2, "target_xy": [x, y]}
    {"type": "click_xy", "target_xy": [x, y]}
    {"type": "end_turn"}
    {"type": "wait"}
    {"type": "defer_to_llm"}  # demande au LLM de trancher

Latence typique : <5ms (pure Python, pas d'IO).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.services.combat_knowledge import CombatKnowledge
from src.services.combat_state_reader import CombatStateSnapshot


# Dofus isométrique : 1 case ≈ 86px horizontal, 43px vertical.
CELL_PX_X = 86
CELL_PX_Y = 43


@dataclass
class EngineConfig:
    class_name: str = "ecaflip"
    spell_shortcuts: dict[int, str] = None  # {slot: spell_name_or_id}
    starting_pa: int = 6
    starting_pm: int = 3
    po_bonus: int = 0
    # Rayon "proche" en cases : si mob à ≤N cases, on engage direct
    engage_range_cases: int = 6


def dist_cases(a_xy: tuple[int, int], b_xy: tuple[int, int]) -> float:
    """Distance Dofus iso entre 2 points écran."""
    dx = abs(a_xy[0] - b_xy[0])
    dy = abs(a_xy[1] - b_xy[1])
    # max(dx/86, dy/43) approxime bien une case Dofus iso
    return max(dx / CELL_PX_X, dy / CELL_PX_Y)


class CombatDecisionEngine:
    """Moteur de décision combat rule-based."""

    def __init__(self, config: EngineConfig, knowledge: CombatKnowledge) -> None:
        self.cfg = config
        self.kb = knowledge
        # Cache : {slot: {pa, po_min, po_max, nom, ...}}
        self._spell_info_cache: dict[int, dict] = {}

    def _get_spell_info(self, slot: int) -> dict:
        """Résout les infos d'un sort depuis sa position slot via knowledge DB.

        Retourne un dict {pa, po_min, po_max, nom} ou {} si introuvable.
        """
        if slot in self._spell_info_cache:
            return self._spell_info_cache[slot]
        info = {}
        try:
            spell_ref = self.cfg.spell_shortcuts.get(slot, "") if self.cfg.spell_shortcuts else ""
            spell_ref = str(spell_ref).strip().lower()
            if spell_ref:
                cls = self.kb.get_class(self.cfg.class_name)
                if cls:
                    for s in cls.sorts:
                        sname = str(s.get("nom", "")).strip().lower()
                        sid = str(s.get("id", "")).strip().lower()
                        if sname == spell_ref or sid == spell_ref:
                            info = {
                                "nom": s.get("nom", ""),
                                "pa": int(s.get("pa", 3)),
                                "po_min": int(s.get("po_min", 1)),
                                "po_max": int(s.get("po_max", 5)),
                                "type": s.get("type", ""),
                                "ligne_de_vue": bool(s.get("ligne_de_vue", True)),
                            }
                            break
        except Exception as exc:
            logger.debug("get_spell_info échec slot={} : {}", slot, exc)
        self._spell_info_cache[slot] = info
        return info

    def _effective_max_range(self, spell: dict) -> int:
        """Portée max effective = portée base + bonus PO (si sort modifiable)."""
        base = spell.get("po_max", 5)
        # Approximation : on applique toujours le bonus PO (la plupart des sorts
        # le sont modifiables). Les sorts "portée fixe" sont rares.
        return base + max(0, self.cfg.po_bonus)

    def _best_spell_for_range(
        self, dist: float, pa_available: int,
    ) -> tuple[int, dict] | None:
        """Choisit le meilleur sort à cast étant donné la distance et les PA dispo.

        Stratégie : sort avec la plus forte "priorité" parmi ceux qui :
        - Coûtent ≤ pa_available
        - Ont po_min ≤ dist ≤ po_max_effective

        Priorité = PA dépensé (un sort qui coûte 4 PA fait plus qu'un à 2 PA
        dans Dofus en général). Ordre secondaire : plus haute portée_min
        (privilégier les sorts de CaC quand on est au contact).

        Retourne (slot, spell_info) ou None.
        """
        if not self.cfg.spell_shortcuts:
            return None
        candidates: list[tuple[int, dict, int]] = []
        for slot in self.cfg.spell_shortcuts:
            info = self._get_spell_info(slot)
            if not info:
                continue
            cost = info.get("pa", 99)
            if cost > pa_available:
                continue
            max_r = self._effective_max_range(info)
            min_r = info.get("po_min", 1)
            if min_r <= dist <= max_r:
                # score = coût PA (proxy pour dégâts) + bonus portée_min basse
                score = cost * 10 + (5 - min_r)
                candidates.append((slot, info, score))
        if not candidates:
            return None
        # Plus haut score = mieux
        candidates.sort(key=lambda c: -c[2])
        slot, info, _ = candidates[0]
        return (slot, info)

    def _min_spell_cost(self) -> int:
        """Plus petit coût PA parmi les sorts configurés (pour check si on peut encore cast)."""
        costs = []
        for slot in (self.cfg.spell_shortcuts or {}):
            info = self._get_spell_info(slot)
            if info:
                costs.append(info.get("pa", 99))
        return min(costs) if costs else 99

    def _max_spell_range(self) -> int:
        """Plus grande portée parmi les sorts configurés."""
        ranges = []
        for slot in (self.cfg.spell_shortcuts or {}):
            info = self._get_spell_info(slot)
            if info:
                ranges.append(self._effective_max_range(info))
        return max(ranges) if ranges else 0

    def _recently_cast_same_target(
        self,
        slot: int,
        target_xy: tuple[int, int],
        cast_history: list[tuple[str, int, int]],
        tolerance: int = 50,
    ) -> bool:
        """True si on a déjà cast ce slot sur ±tolerance px de cette cible."""
        for s, hx, hy in cast_history:
            if str(s) == str(slot):
                if abs(hx - target_xy[0]) <= tolerance and abs(hy - target_xy[1]) <= tolerance:
                    return True
        return False

    def _approach_target(
        self,
        perso_xy: tuple[int, int],
        target_xy: tuple[int, int],
        distance_cases: int = 3,
    ) -> tuple[int, int]:
        """Calcule un point vers la cible, à `distance_cases` cases du perso.
        Utilisé pour se rapprocher avant de pouvoir cast.
        """
        dx = target_xy[0] - perso_xy[0]
        dy = target_xy[1] - perso_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        step_px = distance_cases * CELL_PX_X * 0.9  # un peu moins pour ne pas dépasser
        nx = dx / length
        ny = dy / length
        return (
            int(perso_xy[0] + nx * step_px),
            int(perso_xy[1] + ny * step_px),
        )

    def _bypass_perpendicular(
        self,
        perso_xy: tuple[int, int],
        target_xy: tuple[int, int],
    ) -> tuple[int, int]:
        """Calcule une case perpendiculaire pour contourner un obstacle LoS."""
        dx = target_xy[0] - perso_xy[0]
        dy = target_xy[1] - perso_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        # Perpendiculaire + un petit pas vers l'avant
        offset = 140
        nx, ny = -dy / length, dx / length
        return (
            int(perso_xy[0] + nx * offset + (dx / length) * 60),
            int(perso_xy[1] + ny * offset + (dy / length) * 60),
        )

    def decide(
        self,
        snap: CombatStateSnapshot | None,
        pa_remaining: int,
        cast_history: list[tuple[str, int, int]],
        stuck_overrides: int = 0,
    ) -> dict[str, Any]:
        """Décide d'une action à partir de l'état visuel.

        Ordre des règles (chain-of-responsibility) :
          1. Pas d'état → defer LLM (il voit peut-être un popup)
          2. Pas notre tour → wait
          3. Pas d'ennemi visible → defer LLM (popup fin combat ? hors combat ?)
          4. Plus de PA pour un sort → end_turn
          5. Trouve l'ennemi cible (le plus proche)
          6. Cas re-cast boucle → override mécanique (bouge)
          7. Mob hors portée max → s'approcher
          8. Mob à portée ET sort dispo ET pas déjà cast dessus → cast
          9. Aucune règle ne s'applique → defer LLM

        Returns:
            dict: action au format vision_combat_worker, ou {"type": "defer_to_llm"}
                  si le moteur n'est pas sûr.
        """
        # Règle 1 : pas de snapshot → LLM voit peut-être autre chose
        if snap is None:
            return {"type": "defer_to_llm", "reason": "pas de snapshot HSV"}

        # Règle 3 : pas d'ennemi → LLM doit analyser (popup ? hors combat ?)
        if not snap.ennemis:
            return {"type": "defer_to_llm", "reason": "aucun ennemi HSV détecté"}

        # Règle 4 : PA < plus petit coût → end_turn
        min_cost = self._min_spell_cost()
        if pa_remaining < min_cost:
            return {"type": "end_turn", "reason": f"PA={pa_remaining} < min_coût={min_cost}"}

        # Règle 5 : trouve le mob le plus proche
        if not snap.perso:
            return {"type": "defer_to_llm", "reason": "perso HSV non détecté"}
        perso_xy = (snap.perso.x, snap.perso.y)
        target = min(
            snap.ennemis,
            key=lambda e: (e.x - perso_xy[0]) ** 2 + (e.y - perso_xy[1]) ** 2,
        )
        target_xy = (target.x, target.y)
        dist = dist_cases(perso_xy, target_xy)

        # Règle 7 : mob hors portée max → s'approcher
        max_range = self._max_spell_range()
        if dist > max_range:
            approach_xy = self._approach_target(perso_xy, target_xy, distance_cases=3)
            return {
                "type": "click_xy",
                "target_xy": list(approach_xy),
                "reason": f"mob à {dist:.0f}c > portée_max {max_range}c → approche",
            }

        # Règle 8 : mob à portée → choisir un sort
        choice = self._best_spell_for_range(dist, pa_remaining)
        if not choice:
            # Aucun sort dispo à cette distance → s'approcher ou reculer
            approach_xy = self._approach_target(perso_xy, target_xy, distance_cases=2)
            return {
                "type": "click_xy",
                "target_xy": list(approach_xy),
                "reason": f"aucun sort à {dist:.0f}c avec {pa_remaining}PA → bouge",
            }
        slot, spell_info = choice

        # Règle 6 : check anti-boucle — déjà cast ce slot sur cette cible ?
        if self._recently_cast_same_target(slot, target_xy, cast_history):
            # Boucle détectée → stratégie selon distance
            if dist > 4:
                # Mob loin → avance encore
                next_xy = self._approach_target(perso_xy, target_xy, distance_cases=3)
                return {
                    "type": "click_xy",
                    "target_xy": list(next_xy),
                    "reason": f"déjà cast slot{slot} sur ({target_xy[0]},{target_xy[1]}) sans effet → approche",
                }
            # Mob proche → contournement (LoS bloquée probable)
            if stuck_overrides >= 1:
                return {"type": "end_turn", "reason": "déjà tenté bypass, end_turn"}
            bypass_xy = self._bypass_perpendicular(perso_xy, target_xy)
            return {
                "type": "click_xy",
                "target_xy": list(bypass_xy),
                "reason": f"re-cast sur cible immobile à {dist:.0f}c → contournement LoS",
            }

        # Règle 8 finale : cast !
        return {
            "type": "cast_spell",
            "spell_key": slot,
            "target_xy": list(target_xy),
            "reason": f"cast slot{slot} '{spell_info.get('nom','?')}' sur mob à {dist:.0f}c",
        }
