"""Template pattern commun à tous les runners de métier.

Chaque sous-classe implémente :
  - `metier` (identifiant)
  - `scan_targets(frame)` pour détecter les cibles exploitables
  - `interact_with(target)` pour cliquer/récolter/crafter
  - `should_check_inventory()` optionnel

Le `tick()` orchestre : scan → sélection → action → gestion inventaire.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.models.detection import DetectedObject, Frame
    from src.services.input_service import InputService
    from src.services.vision import MssVisionService


@dataclass
class JobContext:
    """Contexte partagé entre tick()."""
    metier: str
    niveau_personnage: int = 1
    xp_session: int = 0
    actions_session: int = 0
    cible_actuelle: "DetectedObject | None" = None


class BaseJobRunner(ABC):
    """Classe abstraite pour tous les runners de métier."""

    metier: str = ""  # override dans les sous-classes

    def __init__(
        self,
        vision: "MssVisionService",
        input_svc: "InputService",
        ctx: JobContext | None = None,
    ) -> None:
        self.vision = vision
        self.input = input_svc
        self.ctx = ctx or JobContext(metier=self.metier)

    # ---------- API abstraite ----------

    @abstractmethod
    def scan_targets(self, frame: "Frame") -> list["DetectedObject"]:
        """Retourne les cibles détectées dans la frame (arbres, minerais, etc.)."""

    @abstractmethod
    def interact_with(self, target: "DetectedObject") -> bool:
        """Déclenche l'action (clic récolte, ouverture craft, etc.).

        Retourne True si l'action a été lancée avec succès.
        """

    # ---------- Hooks optionnels ----------

    def should_check_inventory(self) -> bool:
        return self.ctx.actions_session > 0 and self.ctx.actions_session % 20 == 0

    def select_best_target(self, candidates: list["DetectedObject"]) -> "DetectedObject | None":
        """Priorise la plus proche du centre de l'écran par défaut."""
        if not candidates:
            return None
        try:
            frame = self.vision.capture()
            cx, cy = frame.shape[1] // 2, frame.shape[0] // 2
        except Exception:
            return candidates[0]

        def dist(obj):
            obj_x = obj.box.x + obj.box.w // 2 if obj.box else 0
            obj_y = obj.box.y + obj.box.h // 2 if obj.box else 0
            return (obj_x - cx) ** 2 + (obj_y - cy) ** 2

        return min(candidates, key=dist)

    # ---------- Tick principal ----------

    def tick(self) -> str:
        """Une itération. Retourne un statut lisible pour la UI."""
        try:
            frame = self.vision.capture()
        except Exception as exc:
            logger.warning("Échec capture : {}", exc)
            return "capture_error"

        if self.should_check_inventory():
            return "inventory_check_requested"

        candidates = self.scan_targets(frame)
        if not candidates:
            return "no_target"

        target = self.select_best_target(candidates)
        if target is None:
            return "no_valid_target"

        self.ctx.cible_actuelle = target
        ok = self.interact_with(target)
        if ok:
            self.ctx.actions_session += 1
            return f"action_ok:{getattr(target, 'label', '?')}"
        return "action_failed"
