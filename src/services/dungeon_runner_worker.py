"""Worker Donjon Runner — enchaîne les salles d'un donjon Dofus.

Pipeline :
  1. Détecte la sortie de la salle (pierres de passage, bord d'écran)
  2. Scan mobs ennemis (HSV bleu)
  3. Si mob présent → lance le combat (via VisionCombatWorker)
  4. Si combat fini → avance à la salle suivante
  5. Détecte le boss final (via template matching ou HSV spécifique)
  6. Fight boss → fin donjon

Inspiration :
  - Mathis-L dofus-bot : monitoring "screen black" pour détecter transitions
  - Dindo-Bot : pathfinder entre maps
  - Dofus Wiki : layouts donjons Incarnam, Bouftous, Champs Pâtures

Configuration utilisateur :
  - Nom du donjon (ex "Incarnam", "Bouftous", "Champs")
  - Layout : nb salles, position des transitions
  - Classe du perso + sorts configurés (pour VisionCombatWorker)

Note : ce module gère la NAVIGATION. Les combats sont délégués au
VisionCombatWorker qui tourne en sous-mode. Le dungeon worker :
  - lance le combat quand mob détecté
  - attend que le combat finisse (signal stopped)
  - avance à la salle suivante

Layouts prédéfinis dans data/knowledge/dungeons/*.json :
  {
    "id": "incarnam",
    "nom": "Donjon d'Incarnam",
    "niveau_min": 1,
    "niveau_max": 15,
    "nb_rooms": 4,
    "transitions": [
      { "from_room": 1, "to_room": 2, "direction": "east" },
      ...
    ],
    "boss": { "name": "Milimilou", "room": 4 }
  }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.combat_state_reader import CombatStateReader
from src.services.input_service import InputService
from src.services.vision import MssVisionService


DUNGEONS_DIR = Path("data/knowledge/dungeons")


@dataclass
class DungeonTransition:
    """Transition entre salles."""
    from_room: int
    to_room: int
    direction: str  # "north", "south", "east", "west"
    click_xy: tuple[int, int] | None = None
    """Pixel fixe où cliquer pour passer la porte (si connu)."""


@dataclass
class DungeonConfig:
    """Configuration d'un donjon."""
    id: str
    nom: str
    niveau_min: int
    niveau_max: int
    nb_rooms: int
    transitions: list[DungeonTransition] = field(default_factory=list)
    boss_room: int = 0
    boss_name: str = ""

    @classmethod
    def from_file(cls, path: Path | str) -> DungeonConfig | None:
        path = Path(path)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                id=data.get("id", ""),
                nom=data.get("nom", ""),
                niveau_min=data.get("niveau_min", 1),
                niveau_max=data.get("niveau_max", 200),
                nb_rooms=data.get("nb_rooms", 1),
                transitions=[
                    DungeonTransition(**t) for t in data.get("transitions", [])
                ],
                boss_room=data.get("boss", {}).get("room", 0),
                boss_name=data.get("boss", {}).get("name", ""),
            )
        except Exception as exc:
            logger.warning("Load dungeon config échec {} : {}", path, exc)
            return None


@dataclass
class DungeonRunnerConfig:
    """Config du worker runner."""
    dungeon: DungeonConfig
    combat_worker_factory: callable = None
    """Fonction qui construit un VisionCombatWorker (avec la classe du perso)."""

    scan_interval_sec: float = 2.0
    combat_timeout_sec: float = 600.0  # 10 min max par combat
    inter_room_delay_sec: float = 2.0


@dataclass
class DungeonStats:
    rooms_cleared: int = 0
    combats_won: int = 0
    combats_lost: int = 0
    dungeons_completed: int = 0


class DungeonRunnerWorker(QThread):
    """Worker Donjon : enchaîne salles + combats."""

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: DungeonRunnerConfig,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._state_reader = CombatStateReader(vision)
        self._stats = DungeonStats()
        self._stop_requested = False
        self._current_room = 1
        self._combat_worker_active = None

    def request_stop(self) -> None:
        self._stop_requested = True
        if self._combat_worker_active:
            try:
                self._combat_worker_active.request_stop()
            except Exception:
                pass

    def run(self) -> None:
        dungeon = self._config.dungeon
        self.log_event.emit(
            f"🏰 Donjon '{dungeon.nom}' démarré (niveau {dungeon.niveau_min}-{dungeon.niveau_max}, "
            f"{dungeon.nb_rooms} salles)",
            "info",
        )
        self.state_changed.emit("running")

        while not self._stop_requested and self._current_room <= dungeon.nb_rooms:
            try:
                self._process_current_room()
            except Exception as exc:
                logger.exception("Dungeon tick erreur")
                self.log_event.emit(f"⚠ Erreur : {exc}", "error")
                self.msleep(2000)

            self.stats_updated.emit(self._stats)

        if self._current_room > dungeon.nb_rooms:
            self._stats.dungeons_completed += 1
            self.log_event.emit(
                f"🏆 Donjon terminé ! {self._stats.rooms_cleared} salles clean",
                "info",
            )

        self.state_changed.emit("stopped")
        self.stopped.emit()

    def _process_current_room(self) -> None:
        """Traite la salle courante : check mobs → combat → transition."""
        self.log_event.emit(
            f"📍 Salle {self._current_room}/{self._config.dungeon.nb_rooms}",
            "info",
        )

        # 1. Scan mobs dans la salle
        frame = self._vision.capture()
        snap = self._state_reader.read()

        if snap.ennemis:
            self.log_event.emit(
                f"⚔ {len(snap.ennemis)} mob(s) détecté(s) → engage combat",
                "info",
            )
            self._engage_combat(snap)
            self._stats.combats_won += 1
            self._stats.rooms_cleared += 1
        else:
            self.log_event.emit(
                "✓ Pas de mob → transition salle suivante",
                "info",
            )

        # 2. Transition vers salle suivante
        self.msleep(int(self._config.inter_room_delay_sec * 1000))
        if not self._go_to_next_room():
            self.log_event.emit(
                "⚠ Impossible de changer de salle → stop",
                "warn",
            )
            self._stop_requested = True

    def _engage_combat(self, snap) -> None:
        """Lance un combat et attend sa fin."""
        if not self._config.combat_worker_factory:
            # Pas de factory → on click sur le 1er mob détecté et on attend
            if snap.ennemis:
                target = snap.ennemis[0]
                self._input.click(target.x, target.y, button="left")
            self.msleep(5000)
            return

        # Avec factory : on lance un VisionCombatWorker dédié, on attend
        worker = self._config.combat_worker_factory()
        self._combat_worker_active = worker
        worker.start()

        # Attend le signal stopped (avec timeout)
        t_start = self._elapsed_ms()
        while worker.isRunning() and not self._stop_requested:
            if (self._elapsed_ms() - t_start) > self._config.combat_timeout_sec * 1000:
                self.log_event.emit("⏱ Timeout combat → arrêt forcé", "warn")
                worker.request_stop()
                break
            self.msleep(500)

        self._combat_worker_active = None

    def _go_to_next_room(self) -> bool:
        """Effectue la transition vers la salle suivante via click fixe."""
        dungeon = self._config.dungeon
        transitions = [
            t for t in dungeon.transitions
            if t.from_room == self._current_room
        ]
        if not transitions:
            return False
        transition = transitions[0]
        if transition.click_xy:
            x, y = transition.click_xy
            self.log_event.emit(
                f"→ Transition salle {self._current_room} → {transition.to_room} "
                f"(click {transition.direction} {x},{y})",
                "info",
            )
            self._input.click(x, y, button="left")
        else:
            # Sinon : click approximatif selon direction (bord d'écran)
            self._click_edge_direction(transition.direction)

        self._current_room = transition.to_room
        self.msleep(3000)  # Laisse le temps du chargement
        return True

    def _click_edge_direction(self, direction: str) -> None:
        """Click un bord d'écran (nord/sud/est/ouest) pour changer de map."""
        frame = self._vision.capture()
        h, w = frame.shape[:2]
        targets = {
            "north": (w // 2, 20),
            "south": (w // 2, h - 20),
            "east": (w - 20, h // 2),
            "west": (20, h // 2),
        }
        if direction in targets:
            x, y = targets[direction]
            self._input.click(x, y, button="left")

    def _elapsed_ms(self) -> float:
        import time  # noqa: PLC0415
        return time.time() * 1000


def list_available_dungeons() -> list[DungeonConfig]:
    """Retourne tous les donjons connus."""
    if not DUNGEONS_DIR.exists():
        return []
    out = []
    for path in DUNGEONS_DIR.glob("*.json"):
        cfg = DungeonConfig.from_file(path)
        if cfg:
            out.append(cfg)
    return out
