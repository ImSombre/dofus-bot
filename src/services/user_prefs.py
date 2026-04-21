"""Persistance des préférences utilisateur.

Sauvegarde les réglages entre sessions :
  - Par métier : ressources cochées, circuit de maps, rotation zaaps, cadence
  - Globaux : fenêtre cible, taille template, dernière destination zaap

Fichier : `data/user_prefs.json` — JSON simple, éditable à la main si besoin.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


DEFAULT_PATH = Path("data/user_prefs.json")


@dataclass
class FarmMetierPrefs:
    """Préférences par métier de récolte."""
    niveau: int = 1
    resources: list[str] = field(default_factory=list)       # IDs cochés
    circuit_maps: list[tuple[int, int]] = field(default_factory=list)
    zaap_rotation: list[str] = field(default_factory=list)
    animation_duration_sec: float = 1.5
    tick_interval_sec: float = 0.6

    def to_dict(self) -> dict:
        return {
            "niveau": self.niveau,
            "resources": self.resources,
            "circuit_maps": [list(c) for c in self.circuit_maps],
            "zaap_rotation": self.zaap_rotation,
            "animation_duration_sec": self.animation_duration_sec,
            "tick_interval_sec": self.tick_interval_sec,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FarmMetierPrefs":
        circuit = []
        for entry in d.get("circuit_maps", []):
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                circuit.append((int(entry[0]), int(entry[1])))
        return cls(
            niveau=int(d.get("niveau", 1)),
            resources=list(d.get("resources", [])),
            circuit_maps=circuit,
            zaap_rotation=list(d.get("zaap_rotation", [])),
            animation_duration_sec=float(d.get("animation_duration_sec", 1.5)),
            tick_interval_sec=float(d.get("tick_interval_sec", 0.6)),
        )


@dataclass
class GlobalPrefs:
    """Préférences globales (partagées entre sessions / métiers)."""
    dofus_window_title: str = ""
    template_size_px: int = 50
    last_zaap_query: str = "ingalsse"
    last_metier: str = ""   # pour pré-sélectionner au démarrage
    # Mode écran entier : force la capture sur l'écran primaire complet plutôt
    # que sur la fenêtre Dofus. Utile si Dofus est en windowed sur un grand écran.
    fullscreen_mode: bool = False
    # Calibration des positions de clic bord (ratios 0.0-1.0 relatifs à la capture)
    edge_ratios: dict | None = None  # None = ratios par défaut
    # Clés API LLM (sauvegardées pour ne pas les retaper à chaque session)
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    def to_dict(self) -> dict:
        return {
            "dofus_window_title": self.dofus_window_title,
            "template_size_px": self.template_size_px,
            "last_zaap_query": self.last_zaap_query,
            "last_metier": self.last_metier,
            "fullscreen_mode": self.fullscreen_mode,
            "edge_ratios": self.edge_ratios,
            "gemini_api_key": self.gemini_api_key,
            "anthropic_api_key": self.anthropic_api_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GlobalPrefs":
        return cls(
            dofus_window_title=str(d.get("dofus_window_title", "")),
            template_size_px=int(d.get("template_size_px", 50)),
            last_zaap_query=str(d.get("last_zaap_query", "ingalsse")),
            last_metier=str(d.get("last_metier", "")),
            fullscreen_mode=bool(d.get("fullscreen_mode", False)),
            edge_ratios=d.get("edge_ratios"),
            gemini_api_key=str(d.get("gemini_api_key", "")),
            anthropic_api_key=str(d.get("anthropic_api_key", "")),
        )


class UserPrefs:
    """Gestionnaire de préférences — load/save atomique en JSON."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_PATH
        self._global: GlobalPrefs = GlobalPrefs()
        self._farm_prefs: dict[str, FarmMetierPrefs] = {}
        self._macros: dict[str, dict] = {}  # name → Macro.to_dict()
        self.load()

    # ---------- Accès ----------

    @property
    def global_prefs(self) -> GlobalPrefs:
        return self._global

    def farm(self, metier: str) -> FarmMetierPrefs:
        """Retourne les prefs du métier (crée un defaut si inexistant)."""
        if metier not in self._farm_prefs:
            self._farm_prefs[metier] = FarmMetierPrefs()
        return self._farm_prefs[metier]

    def set_farm(self, metier: str, prefs: FarmMetierPrefs) -> None:
        self._farm_prefs[metier] = prefs

    # ---------- Macros ----------

    def all_macros(self) -> dict[str, dict]:
        return dict(self._macros)

    def get_macro(self, name: str) -> dict | None:
        return self._macros.get(name)

    def set_macro(self, name: str, macro_dict: dict) -> None:
        self._macros[name] = macro_dict

    def delete_macro(self, name: str) -> bool:
        return self._macros.pop(name, None) is not None

    # ---------- Persistance ----------

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
            if "global" in data:
                self._global = GlobalPrefs.from_dict(data["global"])
            if "farm" in data:
                for metier, d in (data["farm"] or {}).items():
                    self._farm_prefs[metier] = FarmMetierPrefs.from_dict(d)
            if "macros" in data:
                self._macros = dict(data["macros"] or {})
            logger.info(
                "UserPrefs : chargé {} (global + {} métiers + {} macros)",
                self._path, len(self._farm_prefs), len(self._macros),
            )
        except Exception as exc:
            logger.warning("UserPrefs : chargement échoué — {}", exc)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "global": self._global.to_dict(),
                "farm": {m: p.to_dict() for m, p in self._farm_prefs.items()},
                "macros": self._macros,
            }
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("UserPrefs : sauvegarde échouée — {}", exc)


# Instance singleton pour éviter les doublons de fichier
_instance: UserPrefs | None = None


def get_user_prefs() -> UserPrefs:
    """Singleton UserPrefs partagé dans l'app."""
    global _instance
    if _instance is None:
        _instance = UserPrefs()
    return _instance
