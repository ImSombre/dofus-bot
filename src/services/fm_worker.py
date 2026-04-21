"""Worker Forgemagie Dofus.

Automatise le processus de mage d'un item :
  1. Lit les stats actuelles (OCR ou manuel)
  2. Compare aux objectifs user (targets min/max par stat)
  3. Pour chaque stat sous-target : applique la rune correspondante
  4. Vérifie après chaque application que la rune a landed (OCR re-read)
  5. Stop si objectif atteint, ou si trop d'échecs, ou casse proche

Inspiration : Inkybot (Medium article) — tick-based AI
  https://medium.com/@inkybot.me/building-inkybot-a-dofus-maging-bot-with-ocr-win32-api-and-a-rule-based-ai-212d4bb2611d

Architecture :
  - Config : liste de StatObjective (nom, min, max, priorité)
  - Boucle : scan → choose_rune → apply → verify
  - Safety : max_attempts par rune, stop si stats divergent du plan

Le worker peut tourner indéfiniment sur un stack d'items (ex: 100 amulettes
à mage à la chaîne).

Note : utilisation compatible avec `combat_stats_tracker` pour les stats
globales de la session FM (nb items magés, taux de succès, etc.).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.fm_ocr import StatReading, is_available, read_item_stats
from src.services.input_service import InputService
from src.services.vision import MssVisionService


@dataclass
class StatObjective:
    """Objectif de mage pour UNE stat."""
    name: str
    """Nom stat ('Force', 'Vitalité'...)."""

    min_value: int
    """Minimum acceptable (hard floor)."""

    max_value: int
    """Maximum visé (target). Le bot n'essaie pas de dépasser."""

    priority: int = 5
    """1-10, plus haut = priorité d'abord."""

    rune_slot: str = ""
    """Raccourci clavier ou nom de la rune (ex: 'F' pour rune Force PA)."""

    def delta_to_target(self, current: int) -> int:
        """Retourne le nombre d'unités manquantes pour atteindre max_value."""
        return max(0, self.max_value - current)

    def is_met(self, current: int) -> bool:
        return self.min_value <= current <= self.max_value


@dataclass
class FmConfig:
    """Configuration worker FM."""
    objectives: list[StatObjective] = field(default_factory=list)
    """Liste d'objectifs stats."""

    item_stats_region: tuple[int, int, int, int] | None = None
    """(x1, y1, x2, y2) : zone de l'UI FM où lire les stats de l'item."""

    rune_apply_position: tuple[int, int] | None = None
    """Pixel où cliquer pour appliquer la rune (souvent le bouton 'Mager')."""

    delay_between_runes_sec: float = 0.8
    """Délai entre chaque application de rune (pour laisser l'animation)."""

    max_attempts_per_item: int = 50
    """Limite d'essais par item avant abandon."""

    stop_if_broken: bool = True
    """Stop si le bot détecte que l'item s'est cassé (stats disparaissent)."""


@dataclass
class FmStats:
    """Stats de session FM."""
    items_started: int = 0
    items_completed: int = 0
    items_broken: int = 0
    total_runes_applied: int = 0
    successful_runes: int = 0


class FmWorker(QThread):
    """Worker QT : boucle de forgemagie en arrière-plan."""

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: FmConfig,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._stats = FmStats()
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        self.log_event.emit(
            f"⚒️ FmWorker démarré — {len(self._config.objectives)} objectifs",
            "info",
        )
        self.state_changed.emit("running")

        if not is_available():
            self.log_event.emit(
                "⚠ Tesseract OCR non installé. Installe-le via :\n"
                "   https://github.com/UB-Mannheim/tesseract/wiki\n"
                "   puis ajoute au PATH Windows.",
                "error",
            )
            self._stop_requested = True

        if not self._config.rune_apply_position:
            self.log_event.emit(
                "⚠ Position du bouton 'Mager' non configurée — arrêt",
                "error",
            )
            self._stop_requested = True

        while not self._stop_requested:
            try:
                outcome = self._mage_one_item()
                if outcome == "stop":
                    break
            except Exception as exc:
                logger.exception("FmWorker tick erreur")
                self.log_event.emit(f"⚠ Erreur : {exc}", "error")

            self.stats_updated.emit(self._stats)
            self.msleep(500)

        self.log_event.emit("⏹ FmWorker arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    def _mage_one_item(self) -> str:
        """Mage un seul item. Retourne 'continue' / 'stop' / 'broken'.

        Boucle :
          1. Lit stats actuelles
          2. Trouve objectifs non atteints
          3. Si tous atteints → succès, item complet
          4. Sinon, choisit la stat prioritaire à bouger
          5. Applique la rune (clic bouton)
          6. Délai + re-check
        """
        self._stats.items_started += 1
        attempts = 0

        while attempts < self._config.max_attempts_per_item:
            if self._stop_requested:
                return "stop"

            frame = self._vision.capture()
            stats = read_item_stats(frame, self._config.item_stats_region)
            if not stats:
                self.log_event.emit(
                    f"⚠ Aucune stat lue (attempt {attempts + 1}) → skip",
                    "warn",
                )
                if self._config.stop_if_broken and attempts > 2:
                    self._stats.items_broken += 1
                    return "broken"
                attempts += 1
                self.msleep(500)
                continue

            current_values = {s.name: s.value for s in stats}

            # Check si tous les objectifs sont atteints
            unmet = [
                obj for obj in self._config.objectives
                if not obj.is_met(current_values.get(obj.name, 0))
            ]
            if not unmet:
                self._stats.items_completed += 1
                self.log_event.emit(
                    f"✅ Item complet ({self._stats.items_completed}) — "
                    f"stats : {current_values}",
                    "info",
                )
                return "continue"

            # Choisit l'objectif de plus haute priorité parmi unmet
            unmet.sort(key=lambda o: -o.priority)
            next_obj = unmet[0]
            current = current_values.get(next_obj.name, 0)
            delta = next_obj.delta_to_target(current)
            self.log_event.emit(
                f"⚒️ {next_obj.name}: {current} → cible [{next_obj.min_value}, {next_obj.max_value}] "
                f"(delta={delta}, prio={next_obj.priority})",
                "info",
            )

            # Applique la rune : clique sur le bouton Mager.
            # Note : le bot ne CHOISIT PAS la rune active. L'user doit la
            # sélectionner dans l'UI FM avant. Version v1 : rune fixée.
            x, y = self._config.rune_apply_position
            self._input.click(x, y, button="left")
            self._stats.total_runes_applied += 1
            attempts += 1
            self.msleep(int(self._config.delay_between_runes_sec * 1000))

            # Vérifier que la rune a changé la stat (optionnel)
            # (peut être étendu en v2)

        self.log_event.emit(
            f"⏱ Limite attempts atteinte ({self._config.max_attempts_per_item}) "
            f"→ item abandonné",
            "warn",
        )
        return "continue"
