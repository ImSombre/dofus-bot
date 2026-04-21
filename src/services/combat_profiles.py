"""Profils de combat exportables / importables (inspiré Snowbot).

Un profil contient :
  - classe du perso
  - spell_shortcuts (slot → nom du sort)
  - règles combat custom (liste de RuleContext)
  - PA/PM/PO bonus
  - modes préférés (hybrid / rules / llm)
  - humanize_input, use_pixel_los

Format JSON partageable entre joueurs :
  {
    "name": "Pandawa Burst PvM",
    "class": "pandawa",
    "spell_shortcuts": {
      "2": "gueule_de_bois",
      "3": "poing_enflamme",
      "5": "picole"
    },
    "rules": [
      {"name": "Buff T1", "priority": 100, ...}
    ],
    "config": {
      "starting_pa": 10,
      "starting_pm": 5,
      "po_bonus": 0,
      "decision_mode": "hybrid",
      "use_pixel_los": true,
      "humanize_input": true
    },
    "description": "Build Chance/Force. Pic + Vulné + Gueule de Bois focus plus faible"
  }

Dossier : data/profiles/*.json
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


PROFILES_DIR = Path("data/profiles")


@dataclass
class CombatProfile:
    """Un profil de combat prêt à l'usage."""
    name: str = ""
    class_name: str = "ecaflip"
    spell_shortcuts: dict[str, str] = field(default_factory=dict)
    """Clés en str pour JSON."""

    rules: list[dict] = field(default_factory=list)
    """Règles custom combat_rules."""

    config: dict[str, Any] = field(default_factory=dict)
    """{starting_pa, starting_pm, po_bonus, decision_mode, use_pixel_los, humanize_input}"""

    description: str = ""
    author: str = ""
    version: str = "1.0"

    def spell_shortcuts_as_ints(self) -> dict[int, str]:
        """Convertit clés string → int (utilisable par le worker)."""
        out = {}
        for k, v in self.spell_shortcuts.items():
            try:
                out[int(k)] = v
            except (ValueError, TypeError):
                pass
        return out

    @classmethod
    def from_dict(cls, data: dict) -> CombatProfile:
        return cls(
            name=str(data.get("name", "")),
            class_name=str(data.get("class", data.get("class_name", "ecaflip"))),
            spell_shortcuts={str(k): str(v) for k, v in data.get("spell_shortcuts", {}).items()},
            rules=list(data.get("rules", [])),
            config=dict(data.get("config", {})),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            version=str(data.get("version", "1.0")),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> CombatProfile | None:
        path = Path(path)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as exc:
            logger.warning("Load profile échec {} : {}", path, exc)
            return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "class": self.class_name,
            "spell_shortcuts": self.spell_shortcuts,
            "rules": self.rules,
            "config": self.config,
            "description": self.description,
            "author": self.author,
            "version": self.version,
        }

    def save(self, path: str | Path | None = None) -> Path:
        if path is None:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = self.name.lower().replace(" ", "_").replace("/", "_") or "profile"
            path = PROFILES_DIR / f"{safe_name}.json"
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path


def list_available_profiles() -> list[CombatProfile]:
    """Retourne tous les profils JSON chargeables depuis data/profiles/."""
    if not PROFILES_DIR.exists():
        return []
    out = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        p = CombatProfile.from_file(path)
        if p:
            out.append(p)
    return out
