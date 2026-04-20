"""Service qui charge la base de connaissances Dofus et construit les prompts LLM.

Architecture :
  - `data/knowledge/dofus_2_64_rules.md` : règles génériques du jeu
  - `data/knowledge/classes/<classe>.json` : sorts + stratégie par classe

Usage :
    kb = CombatKnowledge()
    system_prompt = kb.build_system_prompt("ecaflip")
    user_prompt = kb.build_turn_prompt(class_id="ecaflip", state=combat_state)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class ClassKnowledge:
    """Connaissance d'une classe Dofus (sorts, stratégie)."""

    class_id: str
    nom_fr: str
    archetype: str
    stats_principales: list[str]
    philosophie: str
    priorites_generales: list[str]
    sorts: list[dict]
    plan_tour_type: list[str]
    gestion_hp: dict

    @classmethod
    def from_json(cls, data: dict) -> ClassKnowledge:
        return cls(
            class_id=data.get("class_id", ""),
            nom_fr=data.get("nom_fr", ""),
            archetype=data.get("archetype", ""),
            stats_principales=data.get("stats_principales", []),
            philosophie=data.get("philosophie", ""),
            priorites_generales=data.get("priorites_generales", []),
            sorts=data.get("sorts", []),
            plan_tour_type=data.get("plan_tour_type", []),
            gestion_hp=data.get("gestion_hp", {}),
        )


@dataclass
class TurnState:
    """État d'un tour pour construire le prompt LLM.

    Tout est optionnel : seules les infos connues sont injectées dans le prompt.
    """

    pa_restants: int | None = None
    pm_restants: int | None = None
    hp_perso: int | None = None
    hp_perso_max: int | None = None
    hp_pourcent: float | None = None
    position_perso: tuple[int, int] | None = None  # (col, row) sur grille
    ennemis: list[dict] = field(default_factory=list)   # [{id, pos, hp_pct, classe?}]
    allies: list[dict] = field(default_factory=list)
    distance_ennemi_proche: int | None = None
    tour_numero: int = 1
    buffs_actifs: list[str] = field(default_factory=list)
    cooldowns_sorts: dict[str, int] = field(default_factory=dict)  # {sort_id: tours_restants}
    spell_shortcuts: dict[int, str] = field(default_factory=dict)  # {1: "griffe_iop"}


class CombatKnowledge:
    """Charge le knowledge base et construit les prompts."""

    def __init__(self, knowledge_dir: Path | str | None = None) -> None:
        if knowledge_dir is None:
            here = Path(__file__).resolve().parent.parent.parent
            knowledge_dir = here / "data" / "knowledge"
        self.knowledge_dir = Path(knowledge_dir)
        self._rules_text: str | None = None
        self._classes: dict[str, ClassKnowledge] = {}
        self._load_all()

    def _load_all(self) -> None:
        rules_path = self.knowledge_dir / "dofus_2_64_rules.md"
        if rules_path.exists():
            self._rules_text = rules_path.read_text(encoding="utf-8")
            logger.info("Rules Dofus chargées ({} chars)", len(self._rules_text))
        else:
            logger.warning("Rules Dofus introuvables : {}", rules_path)
            self._rules_text = ""

        classes_dir = self.knowledge_dir / "classes"
        if classes_dir.exists():
            for f in classes_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    ck = ClassKnowledge.from_json(data)
                    self._classes[ck.class_id.lower()] = ck
                    logger.info("Classe chargée : {} ({} sorts)", ck.class_id, len(ck.sorts))
                except Exception as exc:
                    logger.warning("Classe {} invalide : {}", f.name, exc)

    def has_class(self, class_id: str) -> bool:
        return class_id.lower() in self._classes

    def get_class(self, class_id: str) -> ClassKnowledge | None:
        return self._classes.get(class_id.lower())

    # ---------- Construction des prompts ----------

    def build_system_prompt(self, class_id: str) -> str:
        """Prompt système complet : rôle + règles Dofus + stratégie classe."""
        cls = self.get_class(class_id)
        header = (
            "Tu es un joueur expert de Dofus 2.64 qui prend des décisions optimales en combat. "
            "Tu réponds TOUJOURS en JSON valide selon le schéma demandé, sans texte autour. "
            "Tu maximises les dégâts tout en préservant ton personnage.\n\n"
        )

        rules_block = ""
        if self._rules_text:
            rules_block = f"=== RÈGLES DOFUS 2.64 ===\n{self._rules_text}\n\n"

        class_block = ""
        if cls is not None:
            sorts_lines = []
            for s in cls.sorts:
                line = (
                    f"  - {s.get('id')} ({s.get('nom','?')}) : "
                    f"{s.get('pa','?')} PA, portée {s.get('po_min','?')}-{s.get('po_max','?')}, "
                    f"type={s.get('type','?')}, cooldown={s.get('cooldown',0)}"
                )
                if s.get("ligne_de_vue") is False:
                    line += ", SANS ligne de vue"
                if s.get("note"):
                    line += f". {s['note']}"
                sorts_lines.append(line)
            sorts_text = "\n".join(sorts_lines)

            prios_text = "\n".join(f"  • {p}" for p in cls.priorites_generales)
            plan_text = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(cls.plan_tour_type))

            class_block = (
                f"=== TA CLASSE : {cls.nom_fr.upper()} ({cls.class_id}) ===\n"
                f"Archétype : {cls.archetype}\n"
                f"Philosophie : {cls.philosophie}\n\n"
                f"Sorts disponibles :\n{sorts_text}\n\n"
                f"Priorités par défaut :\n{prios_text}\n\n"
                f"Plan de tour type :\n{plan_text}\n\n"
                f"Gestion HP : seuil critique = {cls.gestion_hp.get('seuil_critique','?')}%. "
                f"Si critique : {', '.join(cls.gestion_hp.get('actions_si_critique', []))}\n\n"
            )

        instructions = (
            "=== INSTRUCTIONS DE RÉPONSE ===\n"
            "Tu reçois l'état du combat (PA/PM/HP, ennemis, position). "
            "Tu retournes un JSON de ce format :\n"
            "{\n"
            '  "reasoning": "<1 phrase expliquant ta stratégie de tour>",\n'
            '  "actions": [\n'
            '    {"type": "spell", "spell_id": "<id>", "target": "enemy_nearest|enemy_weakest|self|ally_id"},\n'
            '    {"type": "move", "direction": "toward_enemy|away_enemy|toward_ally"},\n'
            '    {"type": "wait"}\n'
            "  ]\n"
            "}\n"
            "Règles :\n"
            "- Vérifie toujours que tu as assez de PA pour un sort.\n"
            "- Ne répète pas un sort en cooldown.\n"
            "- Si ton HP < seuil critique, privilégie sustain (vol de vie, fuite vers soigneur).\n"
            "- Si cible à <25% HP : finis-la avec le sort le moins cher possible.\n"
            "- Maximum 4 actions par tour.\n"
        )

        return header + rules_block + class_block + instructions

    def build_turn_prompt(self, class_id: str, state: TurnState) -> str:
        """Prompt utilisateur avec l'état live du combat."""
        lines = ["=== ÉTAT DU COMBAT ===", f"Tour n°{state.tour_numero}"]
        if state.pa_restants is not None:
            lines.append(f"PA restants : {state.pa_restants}")
        if state.pm_restants is not None:
            lines.append(f"PM restants : {state.pm_restants}")
        if state.hp_perso is not None and state.hp_perso_max:
            pct = int(100 * state.hp_perso / max(state.hp_perso_max, 1))
            lines.append(f"HP perso : {state.hp_perso}/{state.hp_perso_max} ({pct}%)")
        elif state.hp_pourcent is not None:
            lines.append(f"HP perso : {int(state.hp_pourcent)}%")
        if state.position_perso:
            lines.append(f"Position perso : case {state.position_perso}")
        if state.distance_ennemi_proche is not None:
            lines.append(f"Distance ennemi le plus proche : {state.distance_ennemi_proche} case(s)")
        if state.ennemis:
            lines.append(f"Ennemis visibles ({len(state.ennemis)}) :")
            for i, e in enumerate(state.ennemis, 1):
                parts = [f"  #{i}"]
                if "classe" in e: parts.append(f"classe={e['classe']}")
                if "pos" in e: parts.append(f"pos={e['pos']}")
                if "hp_pct" in e: parts.append(f"HP={e['hp_pct']}%")
                if "distance" in e: parts.append(f"dist={e['distance']}")
                lines.append(" ".join(parts))
        if state.allies:
            lines.append(f"Alliés : {len(state.allies)}")
        if state.buffs_actifs:
            lines.append(f"Buffs actifs : {', '.join(state.buffs_actifs)}")
        if state.cooldowns_sorts:
            cds = ", ".join(f"{s}={t}t" for s, t in state.cooldowns_sorts.items() if t > 0)
            if cds:
                lines.append(f"Cooldowns en cours : {cds}")
        if state.spell_shortcuts:
            shortcuts = ", ".join(f"touche {k}={v}" for k, v in sorted(state.spell_shortcuts.items()))
            lines.append(f"Raccourcis clavier : {shortcuts}")

        lines.append("")
        lines.append("Décide des actions de ce tour en JSON.")
        return "\n".join(lines)

    # ---------- Helpers diagnostics ----------

    def available_classes(self) -> list[str]:
        return sorted(self._classes.keys())
