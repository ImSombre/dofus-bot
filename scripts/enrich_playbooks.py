"""Enrichit chaque classe avec un playbook `tour_type_pve_solo` détaillé :
séquence optimale de sorts à cast selon le niveau (débutant, moyen, expert).

Ces playbooks guident le moteur quand plusieurs sorts sont dispo à portée :
au lieu de choisir sur coût PA seul, on respecte l'ordre de priorité défini.

Format ajouté à chaque JSON :
  "tour_type_pve_solo": {
    "tour_1": ["buff1", "vulnerabilite", "sort_offensif"],
    "tour_n": ["sort_offensif_principal", "sort_offensif_secondaire"],
    "priority_order": ["id_sort_1", "id_sort_2", ...],
    "combos": [
      {
        "nom": "Porter-Lancer",
        "etapes": ["karcham", "chamrak"],
        "condition": "cible_ivre_et_alliés_proches"
      }
    ]
  }
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CLASSES_DIR = HERE / "data" / "knowledge" / "classes"


# Playbooks par classe (inspirés des guides Millenium, DofHub, forum Dofus)
PLAYBOOKS: dict[str, dict] = {
    "pandawa": {
        "tour_1": [
            "picole",  # +ivresse self (bonus PA/dégâts)
            "vulnerabilite_aqueuse",  # débuff cible principale
            "gueule_de_bois",  # sort feu gros dégâts
        ],
        "tour_n": [
            "gueule_de_bois",
            "poing_enflamme",
            "souffle_alcoolise",  # AoE si >1 mob groupé
        ],
        "priority_order": [
            "gueule_de_bois",
            "souffle_alcoolise",
            "poing_enflamme",
            "vulnerabilite_aqueuse",
            "picole",
        ],
        "combos": [
            {
                "nom": "Porter-Lancer",
                "etapes": ["karcham", "chamrak"],
                "condition": "cible_ivre + mob_adjacent_pour_lancer",
                "note": "Très efficace PvP, moins en PvE solo",
            },
        ],
        "conseils": [
            "Monte l'ivresse avant de Gueule de Bois (dégâts ↑)",
            "Picole tour 1 = bonus force + ivresse self",
            "Évite de tacle un mob Pandawa (il te porte)",
        ],
    },
    "iop": {
        "tour_1": [
            "compulsion",  # buff +dégâts
            "bond",  # engagement rapide
            "epee_celeste",  # gros burst
        ],
        "tour_n": [
            "epee_celeste",
            "pression",
            "intimidation",
        ],
        "priority_order": [
            "epee_celeste",
            "pression",
            "intimidation",
            "bond",
            "compulsion",
        ],
        "combos": [
            {
                "nom": "Bond+Épée",
                "etapes": ["bond", "epee_celeste"],
                "condition": "mob_hors_portee_CaC",
                "note": "Engagement brutal burst 1 tour",
            },
        ],
        "conseils": [
            "Iop = burst CaC. Engage vite, tape fort.",
            "Compulsion en début = +30% dégâts tout le combat",
            "Bond = PM+déplacement = 1 PA",
        ],
    },
    "cra": {
        "tour_1": [
            "fleche_destructrice",  # gros burst distance
            "fleche_enflammee",  # sort feu 2e tour
            "tir_eloigne",  # buff portée (si dispo)
        ],
        "tour_n": [
            "fleche_destructrice",
            "fleche_de_recul",  # repousse si trop proche
            "fleche_persecutrice",
        ],
        "priority_order": [
            "fleche_destructrice",
            "fleche_persecutrice",
            "fleche_enflammee",
            "fleche_de_recul",
        ],
        "combos": [],
        "conseils": [
            "Cra = tourelle. Reste à distance MAX, spam flèches.",
            "Fleche de Recul si mob t'approche (tacle risque)",
            "Garde >5 cases avec les mobs si possible",
        ],
    },
    "sacrieur": {
        "tour_1": [
            "chatiment_spirituel",  # buff
            "assaut",  # engagement rapide
            "souffrance",  # sort dégâts + vol de vie
        ],
        "tour_n": [
            "souffrance",
            "sacrifice",  # switch HP avec un allié (si en groupe)
            "attirance",  # attire mob vers soi
        ],
        "priority_order": [
            "souffrance",
            "assaut",
            "chatiment_spirituel",
        ],
        "combos": [
            {
                "nom": "Chatiment+Souffrance",
                "etapes": ["chatiment_spirituel", "souffrance"],
                "condition": "tour_1",
                "note": "Buff + gros burst démarrage",
            },
        ],
        "conseils": [
            "Sacrieur = tank. Prends les coups et rend-les.",
            "Souffrance = vol de vie, bon sustain",
            "Attirance pour déplacer le mob vers toi",
        ],
    },
    "xelor": {
        "tour_1": [
            "momification",  # invoc si dispo
            "poussiere_temporelle",  # buff
            "vol_du_temps",  # sort PA ennemi
        ],
        "tour_n": [
            "coup_de_minuit",
            "vol_du_temps",
            "ralentissement",  # pose debuff PA
        ],
        "priority_order": [
            "coup_de_minuit",
            "vol_du_temps",
            "ralentissement",
        ],
        "combos": [],
        "conseils": [
            "Xelor = contrôle. Vol PA/PM + ralentissement.",
            "Utilise les téléportations pour positionner",
        ],
    },
    "eniripsa": {
        "tour_1": [
            "mot_stimulant",  # buff
            "mot_de_regeneration",  # soin
            "mot_blessant",  # dégâts base
        ],
        "tour_n": [
            "mot_blessant",
            "mot_de_regeneration",  # si HP bas allié
            "mot_de_silence",  # debuff
        ],
        "priority_order": [
            "mot_de_regeneration",  # priorité soin si HP bas
            "mot_blessant",
            "mot_de_silence",
        ],
        "combos": [],
        "conseils": [
            "Eni = soutien. Soin en priorité si allié <50%.",
            "Mot de régé spammable",
        ],
    },
    "ecaflip": {
        "tour_1": [
            "ruse_feline",  # buff critique
            "pile_ou_face",  # sort dégâts base
            "griffe_du_destin",  # burst CaC
        ],
        "tour_n": [
            "pile_ou_face",
            "griffe_du_destin",
            "roue_de_la_fortune",  # proc chance
        ],
        "priority_order": [
            "griffe_du_destin",
            "pile_ou_face",
            "roue_de_la_fortune",
            "ruse_feline",
        ],
        "combos": [],
        "conseils": [
            "Ecaflip = chance critique. Ruse Féline +Critiques.",
            "Gros burst CaC avec Griffe",
        ],
    },
}


def enrich_class(path: Path) -> bool:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    class_id = data.get("class_id", path.stem).lower()
    if class_id not in PLAYBOOKS:
        return False
    playbook = PLAYBOOKS[class_id]
    # Merge (ne pas écraser si déjà présent sauf si différent)
    if "tour_type_pve_solo" not in data or data["tour_type_pve_solo"] != playbook:
        data["tour_type_pve_solo"] = playbook
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    return False


def main() -> None:
    count = 0
    for path in sorted(CLASSES_DIR.glob("*.json")):
        if enrich_class(path):
            print(f"{path.name}: playbook ajouté/mis à jour")
            count += 1
    print(f"\n{count} classes enrichies.")


if __name__ == "__main__":
    main()
