"""Moteur de décision combat Dofus 2.64 — rule-based, testé, rapide.

v0.6.0 — Refonte intelligente :
  - LoS réelle par raycasting pixels (los_detector) au lieu de heuristique
  - Priorisation cible multi-critères (targeting.py) : finish-kill, CaC, HP bas
  - Choix de sort optimal : favorise gros dégâts tant que PA permet
  - Anti-boucle : détection cible immobile après cast = mur confirmé
  - Buffs en début de tour (si configurés dans knowledge)
  - Gestion HP basse (fuite si <20%)

Inspirations :
  - ArakneUtils (algo LoS Dofus officiel)
  - BlueSheep (Pathfinding MapPoint / formule cellId)
  - Inkybot (chain-of-responsibility rule-based)
  - Guides classes Dofus 2.64 (DofHub, Wiki-Dofus, Millenium)

Le moteur retourne un dict d'action OU {"type": "defer_to_llm"} quand il ne
peut pas décider seul (cas ambigus : popup incertain, phase inconnue…).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from loguru import logger

from src.services.combat_knowledge import CombatKnowledge
from src.services.combat_rules import (
    context_from_snap, find_matching_rule, rule_to_action,
)
from src.services.combat_state_reader import CombatStateSnapshot
from src.services.los_detector import check_line_of_sight, find_bypass_cell
from src.services.movement_planner import plan_movement
from src.services.targeting import TargetScore, score_targets


# Constantes Dofus iso
CELL_PX_X = 86
CELL_PX_Y = 43


@dataclass
class EngineConfig:
    class_name: str = "ecaflip"
    spell_shortcuts: dict[int, str] = field(default_factory=dict)
    starting_pa: int = 6
    starting_pm: int = 3
    po_bonus: int = 0
    low_hp_flee_threshold: float = 0.20
    """Sous ce % HP, on priorise la fuite."""
    use_pixel_los: bool = True
    """Activer le raycasting pixel pour LoS (False = distance-only)."""
    custom_rules: list[dict] = field(default_factory=list)
    """Règles user (profil). Évaluées en PREMIER, avant la logique hardcoded."""


def dist_cases(a_xy: tuple[int, int], b_xy: tuple[int, int]) -> float:
    """Distance Dofus iso (86/43 px/case)."""
    dx = abs(a_xy[0] - b_xy[0])
    dy = abs(a_xy[1] - b_xy[1])
    return max(dx / CELL_PX_X, dy / CELL_PX_Y)


@dataclass
class DecisionContext:
    """Contexte d'une décision (état + historique)."""
    snap: CombatStateSnapshot | None
    pa_remaining: int
    cast_history: list[tuple[str, int, int]]
    stuck_overrides: int = 0
    turn_number: int = 1
    frame_bgr: np.ndarray | None = None
    buffs_cast_this_fight: set[int] = field(default_factory=set)


class CombatDecisionEngine:
    """Moteur rule-based pour combat Dofus.

    Usage :
        engine = CombatDecisionEngine(config, knowledge)
        action = engine.decide(context)
        if action["type"] == "defer_to_llm":
            # fallback LLM
        else:
            execute(action)
    """

    def __init__(self, config: EngineConfig, knowledge: CombatKnowledge) -> None:
        self.cfg = config
        self.kb = knowledge
        self._spell_info_cache: dict[int, dict] = {}

    # ---------- Résolution des sorts (via knowledge DB) ----------

    def _get_spell_info(self, slot: int) -> dict:
        """Résout infos d'un sort depuis son slot, via spell_shortcuts + kb."""
        if slot in self._spell_info_cache:
            return self._spell_info_cache[slot]
        info: dict[str, Any] = {}
        try:
            spell_ref = str(self.cfg.spell_shortcuts.get(slot, "")).strip().lower()
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
                                "type": str(s.get("type", "")).lower(),
                                "ligne_de_vue": bool(s.get("ligne_de_vue", True)),
                                "degats": s.get("degats", ""),
                                "role": str(s.get("role", "offensif")).lower(),
                                # Sort "modifiable" = portée étendue par bonus PO
                                "portee_modifiable": bool(
                                    s.get("portee_modifiable", True),
                                ),
                            }
                            break
        except Exception as exc:
            logger.debug("get_spell_info échec slot={} : {}", slot, exc)
        self._spell_info_cache[slot] = info
        return info

    def _effective_max_range(self, spell: dict) -> int:
        base = spell.get("po_max", 5)
        if spell.get("portee_modifiable", True):
            return base + max(0, self.cfg.po_bonus)
        return base

    def _all_spells(self) -> list[tuple[int, dict]]:
        """Liste des sorts configurés avec infos résolues."""
        out = []
        for slot in sorted(self.cfg.spell_shortcuts.keys()):
            info = self._get_spell_info(slot)
            if info:
                out.append((slot, info))
        return out

    def _min_spell_cost(self) -> int:
        spells = self._all_spells()
        return min((s[1].get("pa", 99) for s in spells), default=99)

    def _max_spell_range(self) -> int:
        spells = self._all_spells()
        return max((self._effective_max_range(s[1]) for s in spells), default=0)

    # ---------- Choix de sort ----------

    def _best_offensive_spell(
        self, dist: float, pa_available: int,
    ) -> tuple[int, dict] | None:
        """Meilleur sort offensif qui :
          - coûte ≤ pa_available
          - portée compatible avec dist
          - rôle offensif (pas buff/soin)

        Priorité : score = coût PA (proxy dégâts) + bonus portée_min basse.
        """
        candidates: list[tuple[int, dict, int]] = []
        for slot, info in self._all_spells():
            if info.get("role", "offensif") not in ("offensif", "degats", ""):
                continue
            cost = info.get("pa", 99)
            if cost > pa_available:
                continue
            max_r = self._effective_max_range(info)
            min_r = info.get("po_min", 1)
            if not (min_r <= dist <= max_r):
                continue
            score = cost * 10 + (5 - min_r)
            candidates.append((slot, info, score))
        if not candidates:
            return None
        candidates.sort(key=lambda c: -c[2])
        slot, info, _ = candidates[0]
        return (slot, info)

    def _buff_spells_pending(self, already_cast: set[int]) -> list[tuple[int, dict]]:
        """Retourne les buffs configurés pas encore cast ce combat."""
        out = []
        for slot, info in self._all_spells():
            role = info.get("role", "")
            if role in ("buff", "soutien") and slot not in already_cast:
                out.append((slot, info))
        # Priorise les buffs "début de combat" (coût faible d'abord)
        out.sort(key=lambda x: x[1].get("pa", 99))
        return out

    # ---------- Utilitaires position ----------

    def _recently_cast_same_target(
        self,
        slot: int,
        target_xy: tuple[int, int],
        cast_history: list[tuple[str, int, int]],
        tolerance: int = 50,
    ) -> bool:
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
        dx = target_xy[0] - perso_xy[0]
        dy = target_xy[1] - perso_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        step_px = distance_cases * CELL_PX_X * 0.9
        nx = dx / length
        ny = dy / length
        return (
            int(perso_xy[0] + nx * step_px),
            int(perso_xy[1] + ny * step_px),
        )

    def _flee_from_target(
        self,
        perso_xy: tuple[int, int],
        threat_xy: tuple[int, int],
        distance_cases: int = 3,
    ) -> tuple[int, int]:
        """Position opposée à la menace (pour HP critique)."""
        dx = perso_xy[0] - threat_xy[0]
        dy = perso_xy[1] - threat_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        step_px = distance_cases * CELL_PX_X * 0.9
        return (
            int(perso_xy[0] + (dx / length) * step_px),
            int(perso_xy[1] + (dy / length) * step_px),
        )

    # ---------- Main decision loop ----------

    def decide(self, ctx: DecisionContext) -> dict[str, Any]:
        """Décision principale. Chaîne de règles.

        Ordre (priorité décroissante) :
          0. Popup / phase incertaine → defer LLM (il voit l'écran)
          1. Pas d'ennemi → defer LLM
          2. HP perso critique + menace au CaC → FUITE
          3. Buff de début de combat pas encore cast → cast le buff
          4. PA insuffisants → end_turn
          5. Pick best target (scoring multi-critères)
          6. Mob hors portée max → approche
          7. Mob à portée :
             a. LoS check (pixel raycasting si use_pixel_los=True)
             b. LoS OK → meilleur sort offensif
             c. LoS bloquée → bypass via find_bypass_cell
          8. Re-cast boucle anti → override mécanique
          9. Aucune règle → defer LLM
        """
        # --- Règle 0/1 : défaut LLM si pas d'info ---
        if ctx.snap is None:
            return {"type": "defer_to_llm", "reason": "pas de snapshot HSV"}
        if not ctx.snap.ennemis:
            return {"type": "defer_to_llm", "reason": "aucun ennemi HSV"}
        if not ctx.snap.perso:
            return {"type": "defer_to_llm", "reason": "perso HSV non détecté"}

        # --- Règles USER (profil) : évaluées AVANT les règles hardcoded ---
        # Permet au joueur de customiser son IA via un profil JSON.
        if self.cfg.custom_rules:
            rule_ctx = context_from_snap(
                snap=ctx.snap,
                turn_number=ctx.turn_number,
                pa_remaining=ctx.pa_remaining,
                pm_remaining=self.cfg.starting_pm,  # approximation : pas tracké actuellement
                buffs_cast=ctx.buffs_cast_this_fight,
            )
            rule = find_matching_rule(self.cfg.custom_rules, rule_ctx)
            if rule:
                action = rule_to_action(rule, rule_ctx, ctx.snap)
                logger.debug("Custom rule matched : {}", rule.get("name"))
                return action

        perso_xy = (ctx.snap.perso.x, ctx.snap.perso.y)

        # --- Règle 5 : meilleure cible (score multi-critères) ---
        target_scores = score_targets(ctx.snap)
        if not target_scores:
            return {"type": "defer_to_llm", "reason": "aucun target scoré"}
        best_target = target_scores[0]
        target_xy = (best_target.entity.x, best_target.entity.y)
        dist = best_target.distance_cases

        # --- Règle 2 : HP perso critique → fuite ---
        hp_pct = ctx.snap.hp_pct
        if (hp_pct is not None and hp_pct < self.cfg.low_hp_flee_threshold * 100
                and best_target.is_melee_threat):
            flee_xy = self._flee_from_target(perso_xy, target_xy, distance_cases=3)
            return {
                "type": "click_xy",
                "target_xy": list(flee_xy),
                "reason": f"HP critique {hp_pct:.0f}% + mob CaC → FUITE",
            }

        # --- Règle 3 : buffs début de combat (tour 1, pas encore cast) ---
        if ctx.turn_number == 1:
            buffs = self._buff_spells_pending(ctx.buffs_cast_this_fight)
            for slot, buff_info in buffs:
                if buff_info.get("pa", 99) <= ctx.pa_remaining:
                    # Buff target : souvent soi-même → target_xy = perso_xy
                    return {
                        "type": "cast_spell",
                        "spell_key": slot,
                        "target_xy": list(perso_xy),
                        "reason": f"Buff tour 1 : {buff_info.get('nom', '?')}",
                        "_buff_slot": slot,  # pour tracking
                    }

        # --- Règle 4 : PA insuffisants ---
        min_cost = self._min_spell_cost()
        if ctx.pa_remaining < min_cost:
            return {
                "type": "end_turn",
                "reason": f"PA={ctx.pa_remaining} < min_coût={min_cost}",
            }

        # --- Règle 6 : mob hors portée max → approche via movement_planner ---
        max_range = self._max_spell_range()
        if dist > max_range:
            if ctx.frame_bgr is not None:
                mp = plan_movement(
                    frame_bgr=ctx.frame_bgr,
                    perso_xy=perso_xy,
                    target_xy=target_xy,
                    spell_po_min=1,
                    spell_po_max=max_range,
                    spell_needs_los=True,
                    strategy="cast_from_here",
                    use_pixel_los=self.cfg.use_pixel_los,
                )
                if mp.move_target_xy:
                    return {
                        "type": "click_xy",
                        "target_xy": list(mp.move_target_xy),
                        "reason": (
                            f"Mob '{best_target.reasoning}' hors portée "
                            f"({dist:.0f}c) → {mp.reason}"
                        ),
                    }
            # Fallback : approche linéaire
            approach_xy = self._approach_target(perso_xy, target_xy, distance_cases=3)
            return {
                "type": "click_xy",
                "target_xy": list(approach_xy),
                "reason": (
                    f"Mob '{best_target.reasoning}' hors portée "
                    f"({dist:.0f}c > {max_range}c) → approche lin\u00e9aire"
                ),
            }

        # --- Règle 7 : mob à portée → check LoS puis cast ---
        spell_choice = self._best_offensive_spell(dist, ctx.pa_remaining)

        if spell_choice is None:
            # Aucun sort compatible avec la distance actuelle.
            # Détermine s'il faut s'approcher (sorts CaC disponibles) ou s'éloigner
            # (sorts distance nécessitent po_min supérieur à dist actuelle).
            all_spells = self._all_spells()
            min_po_min = min(
                (s[1].get("po_min", 1) for s in all_spells
                 if s[1].get("pa", 99) <= ctx.pa_remaining),
                default=1,
            )
            max_po_max = max(
                (self._effective_max_range(s[1]) for s in all_spells
                 if s[1].get("pa", 99) <= ctx.pa_remaining),
                default=5,
            )
            # Si dist < min_po_min → on est TROP PRÈS (sorts distance) → ÉLOIGNE
            # Si dist > max_po_max → on est TROP LOIN → APPROCHE
            if dist < min_po_min:
                # Recul d'1 case vers direction opposée au mob
                flee_xy = self._flee_from_target(perso_xy, target_xy, distance_cases=1)
                return {
                    "type": "click_xy",
                    "target_xy": list(flee_xy),
                    "reason": (
                        f"trop proche (dist {dist:.1f}c < po_min {min_po_min}c) → recule 1c"
                    ),
                }
            # Sinon on approche (cas standard)
            approach_xy = self._approach_target(perso_xy, target_xy, distance_cases=1)
            return {
                "type": "click_xy",
                "target_xy": list(approach_xy),
                "reason": (
                    f"hors portée sorts dispo (dist {dist:.1f}c, max_range={max_po_max}c) → approche 1c"
                ),
            }

        slot, spell_info = spell_choice

        # Check LoS si requise par le sort et si activé dans la config
        needs_los = spell_info.get("ligne_de_vue", True)
        if needs_los and self.cfg.use_pixel_los and ctx.frame_bgr is not None:
            los = check_line_of_sight(ctx.frame_bgr, perso_xy, target_xy)
            if not los.is_clear:
                # LoS bloquée → cherche une case qui débloque
                bypass = find_bypass_cell(ctx.frame_bgr, perso_xy, target_xy)
                if bypass:
                    return {
                        "type": "click_xy",
                        "target_xy": list(bypass),
                        "reason": (
                            f"LoS BLOQUÉE vers ({target_xy[0]},{target_xy[1]}) "
                            f"({los.obstacle_ratio:.0%}) → bypass"
                        ),
                    }
                # Sinon, essaie juste une perpendiculaire
                approach_xy = self._approach_target(
                    perso_xy, target_xy, distance_cases=2,
                )
                return {
                    "type": "click_xy",
                    "target_xy": list(approach_xy),
                    "reason": f"LoS bloquée ({los.obstacle_ratio:.0%}) → déplace",
                }

        # --- Règle 8 : anti-boucle — déjà cast ce slot sur cette cible ? ---
        if self._recently_cast_same_target(slot, target_xy, ctx.cast_history):
            # Si ça n'a pas marché, c'est que la LoS pixel ne détecte pas un
            # petit obstacle. Force un déplacement de contournement.
            if ctx.stuck_overrides >= 2:
                return {
                    "type": "end_turn",
                    "reason": f"3e stuck sur même cible → end_turn",
                }
            if dist > 3:
                next_xy = self._approach_target(perso_xy, target_xy, distance_cases=2)
            else:
                # proche mais cast rate = mur → perpendiculaire
                import math
                dx = target_xy[0] - perso_xy[0]
                dy = target_xy[1] - perso_xy[1]
                length = max(1.0, (dx * dx + dy * dy) ** 0.5)
                perp_x = -dy / length
                perp_y = dx / length
                next_xy = (
                    int(perso_xy[0] + perp_x * 140 + (dx / length) * 60),
                    int(perso_xy[1] + perp_y * 140 + (dy / length) * 60),
                )
            return {
                "type": "click_xy",
                "target_xy": list(next_xy),
                "reason": (
                    f"déjà cast slot{slot} sur cible immobile à {dist:.0f}c "
                    f"(stuck {ctx.stuck_overrides}) → bypass"
                ),
            }

        # --- Règle finale : CAST ! ---
        return {
            "type": "cast_spell",
            "spell_key": slot,
            "target_xy": list(target_xy),
            "reason": (
                f"cast slot{slot} '{spell_info.get('nom','?')}' sur "
                f"{best_target.reasoning}"
            ),
        }
