"""Enregistreur de parties — capture screen + clavier/souris pour apprentissage.

Concept : quand l'utilisateur joue manuellement Dofus, ce recorder tourne en
arrière-plan et enregistre :
  - Screenshot + positions HSV (perso/mobs) toutes les 500ms
  - Chaque touche pressée (pynput keyboard listener)
  - Chaque clic souris (pynput mouse listener)

Format de sortie : `data/replays/session_{timestamp}.jsonl` — 1 event par ligne.

Types d'events :
  - {"t": ..., "type": "frame", "perso_xy": [x, y], "enemies": [[x, y], ...], "pa_visible": null}
  - {"t": ..., "type": "key", "key": "&", "action": "press"}
  - {"t": ..., "type": "click", "x": 1234, "y": 567, "button": "left"}

Usage :
    rec = ReplayRecorder(vision, output_path)
    rec.start()  # démarre le thread + listeners
    # ... user joue ...
    rec.stop()
    # Le fichier session_*.jsonl est prêt pour rule_generator.py

Permissions requises :
  - pynput sur Windows : marche en user normal, pas besoin admin
  - Hooks globaux : captent TOUTES les touches/clics, même hors Dofus
    → le recorder tourne uniquement pendant que l'utilisateur le veut
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.combat_state_reader import CombatStateReader
from src.services.vision import MssVisionService


REPLAY_DIR = Path("data/replays")


@dataclass
class ReplayStats:
    frames_captured: int = 0
    keys_captured: int = 0
    clicks_captured: int = 0
    duration_sec: float = 0.0


class ReplayRecorder(QThread):
    """Enregistre une session de jeu manuelle pour analyse ultérieure.

    Thread Qt pour la capture screen, + listeners pynput pour les events.
    """

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        session_name: str | None = None,
        capture_interval_sec: float = 0.5,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._state_reader = CombatStateReader(vision)
        self._capture_interval = capture_interval_sec
        self._stop_requested = False
        self._stats = ReplayStats()
        self._t0 = 0.0
        # Fichier de sortie
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        name = session_name or f"session_{int(time.time())}"
        self._output_path = REPLAY_DIR / f"{name}.jsonl"
        self._file = None
        self._write_lock = threading.Lock()
        # Listeners pynput (initialisés dans run)
        self._keyboard_listener = None
        self._mouse_listener = None

    @property
    def output_path(self) -> Path:
        return self._output_path

    def stats(self) -> ReplayStats:
        return self._stats

    def request_stop(self) -> None:
        self._stop_requested = True

    def _write_event(self, event: dict[str, Any]) -> None:
        """Écrit un event dans le fichier jsonl (thread-safe)."""
        event["t"] = time.time() - self._t0
        with self._write_lock:
            if self._file is not None:
                try:
                    self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
                    self._file.flush()
                except Exception as exc:
                    logger.debug("write_event échec : {}", exc)

    # ---------- pynput callbacks ----------

    def _on_key_press(self, key) -> None:
        try:
            try:
                name = key.char if hasattr(key, "char") and key.char else str(key)
            except Exception:
                name = str(key)
            self._stats.keys_captured += 1
            self._write_event({
                "type": "key",
                "key": name,
                "action": "press",
            })
        except Exception as exc:
            logger.debug("on_key_press échec : {}", exc)

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return  # on capture que les press
        try:
            btn_name = str(button).replace("Button.", "")
            self._stats.clicks_captured += 1
            self._write_event({
                "type": "click",
                "x": int(x),
                "y": int(y),
                "button": btn_name,
            })
        except Exception as exc:
            logger.debug("on_click échec : {}", exc)

    # ---------- Main loop ----------

    def run(self) -> None:
        self._t0 = time.time()
        self.log_event.emit(
            f"🔴 Enregistrement démarré → {self._output_path.name}",
            "info",
        )
        self.state_changed.emit("recording")

        # Ouvre le fichier en mode write (nouvelle session = fichier fresh)
        try:
            self._file = open(self._output_path, "w", encoding="utf-8", buffering=1)
        except Exception as exc:
            self.log_event.emit(f"⚠ Impossible d'ouvrir replay : {exc}", "error")
            self.stopped.emit()
            return

        # Header : meta session
        self._write_event({
            "type": "session_start",
            "capture_interval_sec": self._capture_interval,
        })

        # Lance les listeners pynput (non bloquants)
        try:
            from pynput import keyboard, mouse  # noqa: PLC0415
            self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
            self._keyboard_listener.start()
            self._mouse_listener = mouse.Listener(on_click=self._on_click)
            self._mouse_listener.start()
        except Exception as exc:
            self.log_event.emit(f"⚠ pynput listener échec : {exc}", "error")

        # Boucle de capture protégée : garantit cleanup des listeners même si crash
        try:
            while not self._stop_requested:
                try:
                    snap = self._state_reader.read()
                    event: dict[str, Any] = {"type": "frame"}
                    if snap.perso:
                        event["perso_xy"] = [snap.perso.x, snap.perso.y]
                    if snap.ennemis:
                        event["enemies"] = [[e.x, e.y] for e in snap.ennemis]
                    # Stats OCR si disponibles (champ déjà dans snap)
                    if snap.pa_restants is not None:
                        event["pa_visible"] = snap.pa_restants
                    if snap.pm_restants is not None:
                        event["pm_visible"] = snap.pm_restants
                    if snap.hp_perso is not None and snap.hp_perso_max:
                        event["hp_pct_self"] = round(
                            100 * snap.hp_perso / max(1, snap.hp_perso_max), 1,
                        )

                    self._write_event(event)
                    self._stats.frames_captured += 1
                except Exception as exc:
                    logger.debug("capture frame échec : {}", exc)

                self.stats_updated.emit(self._stats)
                self.msleep(int(self._capture_interval * 1000))
        except Exception as exc:
            logger.exception("Recorder run crash")
            self.log_event.emit(f"⚠ Recorder crash : {exc}", "error")

        # Cleanup — GARANTI même si crash dans la boucle
        self._stats.duration_sec = time.time() - self._t0
        self._write_event({
            "type": "session_end",
            "duration_sec": self._stats.duration_sec,
            "frames": self._stats.frames_captured,
            "keys": self._stats.keys_captured,
            "clicks": self._stats.clicks_captured,
        })
        try:
            if self._keyboard_listener:
                self._keyboard_listener.stop()
            if self._mouse_listener:
                self._mouse_listener.stop()
        except Exception:
            pass
        try:
            if self._file:
                self._file.close()
        except Exception:
            pass

        self.log_event.emit(
            f"⏹ Enregistrement terminé : {self._stats.frames_captured} frames, "
            f"{self._stats.keys_captured} touches, {self._stats.clicks_captured} clics, "
            f"{self._stats.duration_sec:.0f}s → {self._output_path.name}",
            "info",
        )
        self.state_changed.emit("stopped")
        self.stopped.emit()


def list_replays() -> list[Path]:
    """Liste les replays disponibles, plus récent d'abord."""
    if not REPLAY_DIR.exists():
        return []
    return sorted(
        REPLAY_DIR.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
