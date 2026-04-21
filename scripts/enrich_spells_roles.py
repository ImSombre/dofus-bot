"""Enrichit tous les fichiers data/knowledge/classes/*.json avec un champ
`role` par sort, pour que le moteur de décision sache distinguer :

  - offensif : sort qui fait des dégâts à un ennemi
  - buff     : sort de soutien self ou allié (boost stats, état positif)
  - soin     : sort qui restaure HP
  - deplacement : sort qui bouge le perso (bond, transposition)
  - debuff   : sort qui débuff les ennemis (vulnérabilité, affaibli)
  - invoc    : sort qui invoque une créature
  - trap     : sort de piège (sram, xelor, etc.)

Règles inférées :
  - type "self_buff" / "ally_buff" → buff
  - type "soin" / "buff_soin" → soin
  - type "invocation" → invoc
  - type "piege" → trap
  - nom contient "vulné|affaibl|poison" → debuff
  - nom contient "bond|transposition|saut" → deplacement
  - sinon → offensif
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CLASSES_DIR = HERE / "data" / "knowledge" / "classes"


OFFENSIVE_TYPES = {"mono-cible", "aoe_carre", "aoe_ligne", "aoe_croix", "aoe_zone"}
BUFF_TYPES = {"self_buff", "ally_buff", "buff", "soutien"}
HEAL_TYPES = {"soin", "soin_aoe"}
INVOC_TYPES = {"invocation"}
TRAP_TYPES = {"piege", "trap"}

BUFF_KEYWORDS = re.compile(
    r"(compulsion|ruse féline|ruse feline|chance|puissance|maraudeur|châtiment|chatiment|"
    r"tortue|cage|garde féca|armure|bouclier|rappel|picole|lien|boisson|bond)",
    re.IGNORECASE,
)
DEBUFF_KEYWORDS = re.compile(
    r"(vulnérabilité|vulnerabilite|affaibli|poison|maladie|aveugl|maléfice|malefice|peste)",
    re.IGNORECASE,
)
HEAL_KEYWORDS = re.compile(
    r"(soin|mot de régénération|mot de regeneration|mot de jouvence|mot curatif|"
    r"mot altruiste|bougie soignante|lumen|absorption|vol de vie)",
    re.IGNORECASE,
)
MOVE_KEYWORDS = re.compile(
    r"(bond|saut|transposition|téléport|teleport|dévoré|devoré|attraction|"
    r"éloignement|eloignement|repoussement|appât|appat)",
    re.IGNORECASE,
)


def infer_role(spell: dict) -> str:
    type_ = str(spell.get("type", "")).lower().strip()
    nom = str(spell.get("nom", ""))
    degats = str(spell.get("degats", "")).lower()

    # Par type explicite d'abord
    if type_ in TRAP_TYPES:
        return "trap"
    if type_ in INVOC_TYPES:
        return "invoc"
    if type_ in HEAL_TYPES:
        return "soin"
    if type_ in BUFF_TYPES:
        return "buff"

    # Par mots-clés du nom
    if HEAL_KEYWORDS.search(nom):
        return "soin"
    if DEBUFF_KEYWORDS.search(nom):
        return "debuff"
    if BUFF_KEYWORDS.search(nom) and degats in ("", "aucun", "faible", "faibles", "nul"):
        return "buff"
    if MOVE_KEYWORDS.search(nom):
        return "deplacement"

    # Par type (fallback)
    if type_ in OFFENSIVE_TYPES:
        return "offensif"

    # Par défaut : si le sort fait des dégâts → offensif, sinon utilitaire
    if degats in ("moyens", "moyen", "élevés", "eleves", "fort", "forts", "énormes", "enormes"):
        return "offensif"

    return "offensif"  # safe default


def enrich_class_file(path: Path) -> int:
    """Enrichit un fichier JSON de classe. Retourne le nombre de sorts modifiés."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    for spell in data.get("sorts", []):
        role = infer_role(spell)
        if spell.get("role") != role:
            spell["role"] = role
            changed += 1
        # Par défaut portée modifiable (sauf si explicitement False)
        if "portee_modifiable" not in spell:
            # Heuristique : sorts self (po_min==po_max==0) pas modifiables
            po_min = int(spell.get("po_min", 1))
            po_max = int(spell.get("po_max", 5))
            if po_min == 0 and po_max == 0:
                spell["portee_modifiable"] = False
            else:
                spell["portee_modifiable"] = True
            changed += 1

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return changed


def main() -> None:
    total = 0
    for path in sorted(CLASSES_DIR.glob("*.json")):
        n = enrich_class_file(path)
        print(f"{path.name}: +{n} champs")
        total += n
    print(f"\nTotal : {total} champs ajoutés/modifiés dans {len(list(CLASSES_DIR.glob('*.json')))} fichiers.")


if __name__ == "__main__":
    main()
