"""Système de règles combat configurables par l'utilisateur.

Inspiration : Snowbot Script Creator — éditeur de blocs conditions + actions.
On reproduit la logique de "règle personnalisable" en JSON :

  [
    {
      "name": "Buff Picole tour 1",
      "priority": 100,
      "conditions": [
        {"type": "turn_number", "op": "==", "value": 1},
        {"type": "pa_remaining", "op": ">=", "value": 2}
      ],
      "action": {
        "type": "cast_spell",
        "slot": 5,
        "target": "self"
      }
    },
    {
      "name": "Burst si 6+ PA",
      "priority": 50,
      "conditions": [
        {"type": "pa_remaining", "op": ">=", "value": 6},
        {"type": "enemy_at_range", "op": ">=", "value": 1, "range": 5}
      ],
      "action": {
        "type": "cast_spell",
        "slot": 2,
        "target": "lowest_hp"
      }
    }
  ]

Le moteur évalue les règles par priorité décroissante. La 1ère qui match
s'exécute. Si aucune règle ne match → fallback sur la logique hardcoded.

Conditions supportées :
  - turn_number              : numéro du tour courant (1, 2, 3...)
  - pa_remaining             : PA restants
  - pm_remaining             : PM restants
  - hp_pct_self              : % HP du perso (0-100)
  - enemy_count              : nb d'ennemis visibles
  - enemy_at_range           : nb d'ennemis à portée `range` cases
  - spell_ready              : sort X en cooldown ? (field "slot")
  - melee_enemy              : mob en CaC avec perso ? (0 ou 1)
  - lowest_enemy_hp_pct      : HP% du mob le plus faible (0-100)
  - nearest_enemy_dist_cases : distance du mob le plus proche
  - has_buff                 : buff X actif (pas encore implémenté)

Opérateurs : ==, !=, >, <, >=, <=

Actions supportées :
  - cast_spell  : slot + target ("self", "nearest_enemy", "lowest_hp", "highest_threat", [x, y])
  - click_xy    : target_xy
  - end_turn
  - wait
  - press_key   : key
"""
from __future__ import annotations

import json
import operator as op
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger


# Opérateurs supportés
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": op.eq,
    "!=": op.ne,
    ">": op.gt,
    "<": op.lt,
    ">=": op.ge,
    "<=": op.le,
}


@dataclass
class RuleContext:
    """Contexte d'évaluation d'une règle."""
    turn_number: int = 1
    pa_remaining: int = 0
    pm_remaining: int = 0
    hp_pct_self: float = 100.0
    enemy_count: int = 0
    nearest_enemy_dist_cases: float = 99.0
    lowest_enemy_hp_pct: float = 100.0
    melee_enemy: int = 0
    """0 ou 1 : mob à <=1.5 cases"""
    enemies_at_ranges: dict[int, int] = field(default_factory=dict)
    """{5: 2} = 2 ennemis à portée 5 cases"""
    buffs_cast: set[int] = field(default_factory=set)
    """Slots des buffs déjà cast ce combat."""

    def enemy_at_range(self, range_cases: int) -> int:
        return self.enemies_at_ranges.get(range_cases, 0)


def evaluate_condition(cond: dict, ctx: RuleContext) -> bool:
    """Évalue UNE condition contre le contexte. True si satisfaite."""
    ctype = cond.get("type", "")
    op_str = cond.get("op", "==")
    value = cond.get("value")
    op_fn = _OPS.get(op_str)
    if op_fn is None:
        logger.warning("Opérateur inconnu : {}", op_str)
        return False

    if ctype == "turn_number":
        return op_fn(ctx.turn_number, value)
    if ctype == "pa_remaining":
        return op_fn(ctx.pa_remaining, value)
    if ctype == "pm_remaining":
        return op_fn(ctx.pm_remaining, value)
    if ctype == "hp_pct_self":
        return op_fn(ctx.hp_pct_self, value)
    if ctype == "enemy_count":
        return op_fn(ctx.enemy_count, value)
    if ctype == "enemy_at_range":
        rng = int(cond.get("range", 5))
        return op_fn(ctx.enemy_at_range(rng), value)
    if ctype == "melee_enemy":
        return op_fn(ctx.melee_enemy, value)
    if ctype == "lowest_enemy_hp_pct":
        return op_fn(ctx.lowest_enemy_hp_pct, value)
    if ctype == "nearest_enemy_dist_cases":
        return op_fn(ctx.nearest_enemy_dist_cases, value)
    if ctype == "spell_ready":
        slot = int(cond.get("slot", 0))
        # Simplification v1 : considéré ready s'il n'est pas dans buffs_cast
        # (un vrai cooldown nécessite un tracker dédié)
        return op_fn(0 if slot in ctx.buffs_cast else 1, value)

    logger.debug("Condition type inconnu : {}", ctype)
    return False


def evaluate_rule(rule: dict, ctx: RuleContext) -> bool:
    """Toutes les conditions doivent être True (AND implicite)."""
    conditions = rule.get("conditions", [])
    if not conditions:
        return True  # règle sans condition = always on
    return all(evaluate_condition(c, ctx) for c in conditions)


def find_matching_rule(rules: list[dict], ctx: RuleContext) -> dict | None:
    """Trie les règles par priorité décroissante et retourne la 1ère qui match."""
    sorted_rules = sorted(rules, key=lambda r: -r.get("priority", 0))
    for rule in sorted_rules:
        if evaluate_rule(rule, ctx):
            logger.debug("Rule match : {}", rule.get("name", "?"))
            return rule
    return None


def load_rules_from_file(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "rules" in data:
            return data["rules"]
        return []
    except Exception as exc:
        logger.warning("Load rules échec : {}", exc)
        return []


def save_rules_to_file(rules: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rules": rules}, f, ensure_ascii=False, indent=2)


def rule_to_action(rule: dict, ctx: RuleContext, snap: Any) -> dict:
    """Convertit l'action d'une règle en dict exécutable par le worker.

    Résout les targets symboliques :
      - "self"           → coords du perso
      - "nearest_enemy"  → coord du mob le plus proche
      - "lowest_hp"      → coord du mob plus bas HP
    """
    action = rule.get("action", {})
    atype = action.get("type", "wait")
    resolved = {"type": atype}

    if atype == "cast_spell":
        resolved["spell_key"] = int(action.get("slot", 1))
        target_spec = action.get("target", "nearest_enemy")
        resolved["target_xy"] = _resolve_target(target_spec, snap)
        resolved["reason"] = f"rule '{rule.get('name', '?')}'"
        if "_buff_slot" in action or target_spec == "self":
            resolved["_buff_slot"] = resolved["spell_key"]
    elif atype == "click_xy":
        resolved["target_xy"] = action.get("target_xy", [0, 0])
        resolved["reason"] = f"rule '{rule.get('name', '?')}'"
    elif atype == "press_key":
        resolved["key"] = action.get("key", "")
    else:
        resolved["reason"] = f"rule '{rule.get('name', '?')}'"
    return resolved


def _resolve_target(target_spec: Any, snap: Any) -> list[int]:
    """Résout un target symbolique ('self', 'lowest_hp', etc.) vers (x, y)."""
    if isinstance(target_spec, (list, tuple)) and len(target_spec) == 2:
        return list(target_spec)
    if not snap:
        return [0, 0]
    perso = getattr(snap, "perso", None)
    enemies = getattr(snap, "ennemis", []) or []
    if target_spec == "self" and perso:
        return [perso.x, perso.y]
    if target_spec == "nearest_enemy" and perso and enemies:
        target = min(
            enemies,
            key=lambda e: (e.x - perso.x) ** 2 + (e.y - perso.y) ** 2,
        )
        return [target.x, target.y]
    if target_spec == "lowest_hp" and enemies:
        with_hp = [e for e in enemies if getattr(e, "hp_pct", None) is not None]
        if with_hp:
            target = min(with_hp, key=lambda e: e.hp_pct)
        else:
            target = enemies[0]
        return [target.x, target.y]
    if target_spec == "highest_threat" and perso and enemies:
        # Menace = mob au CaC en priorité
        target = min(
            enemies,
            key=lambda e: (e.x - perso.x) ** 2 + (e.y - perso.y) ** 2,
        )
        return [target.x, target.y]
    return [0, 0]


def context_from_snap(
    snap: Any,
    turn_number: int,
    pa_remaining: int,
    pm_remaining: int,
    buffs_cast: set[int],
) -> RuleContext:
    """Construit un RuleContext depuis un CombatStateSnapshot."""
    enemies = getattr(snap, "ennemis", []) if snap else []
    perso = getattr(snap, "perso", None) if snap else None

    hp_pct_self = 100.0
    if snap:
        raw = getattr(snap, "hp_pct", None)
        if raw is not None:
            hp_pct_self = float(raw)

    nearest_dist = 99.0
    melee = 0
    enemies_at_ranges: dict[int, int] = {}
    lowest_hp_pct = 100.0

    if perso and enemies:
        CELL_X, CELL_Y = 86, 43
        distances = []
        for e in enemies:
            dx = abs(e.x - perso.x)
            dy = abs(e.y - perso.y)
            d = max(dx / CELL_X, dy / CELL_Y)
            distances.append(d)
            hp = getattr(e, "hp_pct", None)
            if hp is not None and hp < lowest_hp_pct:
                lowest_hp_pct = float(hp)
        nearest_dist = min(distances)
        melee = 1 if nearest_dist <= 1.5 else 0
        # Compte enemis par seuil de portée
        for rng in (1, 2, 3, 4, 5, 6, 7, 8, 10):
            enemies_at_ranges[rng] = sum(1 for d in distances if d <= rng)

    return RuleContext(
        turn_number=turn_number,
        pa_remaining=pa_remaining,
        pm_remaining=pm_remaining,
        hp_pct_self=hp_pct_self,
        enemy_count=len(enemies),
        nearest_enemy_dist_cases=nearest_dist,
        melee_enemy=melee,
        enemies_at_ranges=enemies_at_ranges,
        lowest_enemy_hp_pct=lowest_hp_pct,
        buffs_cast=set(buffs_cast or ()),
    )
