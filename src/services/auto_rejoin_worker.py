"""Worker qui surveille la map courante et joue une macro quand coords cible atteintes.

Usage type : après chaque fin de donjon, le perso est TP sur une map connue
(ex: entrée du donjon). Le bot détecte ce coord et joue automatiquement la
macro "rejoindre_dj" pour parler au NPC et se faire réinviter.

Flow :
    - Loop : OCR coords toutes les N secondes
    - Si coords == trigger_coords et on n'a pas déjà joué récemment :
        → joue la macro
        → cooldown X secondes (évite de spam)
    - Si coords != trigger_coords :
        → reset le "dernier déclenchement"
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.input_service import InputService
from src.services.macro_service import Macro, MacroPlayer
from src.services.map_locator import MapLocator
from src.services.vision import MssVisionService


@dataclass
class AutoRejoinConfig:
    trigger_coords: tuple[int, int]   # map où on déclenche la macro
    macro: Macro                       # la macro à jouer
    scan_interval_sec: float = 2.0     # fréquence de vérification
    cooldown_sec: float = 15.0         # délai min entre 2 déclenchements
    dofus_window_title: str | None = None


class AutoRejoinWorker(QThread):
    """Thread qui surveille les coords et joue la macro au bon moment."""

    log_event = pyqtSignal(str, str)     # (message, level)
    state_changed = pyqtSignal(str)       # "idle" / "scanning" / "playing"
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: AutoRejoinConfig,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._stop_requested = False
        self._last_trigger_time: float = 0.0

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        cfg = self._config
        self.log_event.emit(
            f"🔁 Auto-rejoin DJ démarré : trigger=({cfg.trigger_coords[0]},{cfg.trigger_coords[1]}), "
            f"macro='{cfg.macro.name}' ({len(cfg.macro.steps)} étapes)",
            "info",
        )
        self.state_changed.emit("scanning")

        locator = MapLocator(self._vision, log_callback=self.log_event.emit)
        player = MacroPlayer(
            self._input,
            log_callback=self.log_event.emit,
            stop_flag=lambda: self._stop_requested,
        )

        while not self._stop_requested:
            try:
                self._tick(locator, player)
            except Exception as exc:
                logger.exception("Erreur auto-rejoin")
                self.log_event.emit(f"⚠ Erreur auto-rejoin : {exc}", "error")
            if not self._stop_requested:
                self.msleep(int(cfg.scan_interval_sec * 1000))

        self.log_event.emit("⏹ Auto-rejoin arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    def _tick(self, locator: MapLocator, player: MacroPlayer) -> None:
        info = locator.locate()
        if info is None or not info.is_valid:
            # OCR raté → on essaiera au prochain tick
            return

        now = time.time()
        if info.coords == self._config.trigger_coords:
            # On est sur la map trigger
            since_last = now - self._last_trigger_time
            if since_last < self._config.cooldown_sec:
                # Encore en cooldown → ne relance pas
                return
            # Déclenche !
            self.log_event.emit(
                f"🎯 Map trigger atteinte ({info.coords}) — lance macro",
                "info",
            )
            self.state_changed.emit("playing")
            player.play(self._config.macro)
            self._last_trigger_time = time.time()
            self.state_changed.emit("scanning")
        else:
            # Pas sur la map trigger → reset cooldown pour pouvoir re-trigger
            # dès qu'on reviendra sur la map trigger
            if self._last_trigger_time > 0 and now - self._last_trigger_time > self._config.cooldown_sec:
                self._last_trigger_time = 0.0
