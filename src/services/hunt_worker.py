"""Worker chasseur : scan du monde ouvert → attaque mobs → délègue au combat runner.

Flow :
    1. Capture écran + détection cercles bleus (mobs ennemis en world)
    2. Calcule le mob le plus proche du perso (cercle rouge)
    3. Clic gauche sur le mob → lance le combat
    4. Attend que le CombatDetector confirme "in_combat = True"
    5. Laisse le CombatRunnerWorker jouer le combat
    6. À la fin du combat (retour world), reprend le scan

Signal `engagement_started` émis dès qu'un combat est lancé — le dashboard peut
coupler ça pour démarrer le combat runner.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.combat_detector import CombatDetector
from src.services.combat_state_reader import CombatStateReader, EntityDetection
from src.services.input_service import InputService
from src.services.vision import MssVisionService


@dataclass
class HuntConfig:
    # Timings
    scan_interval_sec: float = 1.2
    post_click_wait_sec: float = 1.5       # attente fenêtre "Prêt au combat"
    post_ready_wait_sec: float = 2.0       # attente après clic sur "Prêt"
    combat_detection_timeout_sec: float = 6.0
    post_combat_cooldown_sec: float = 2.0

    # Filtres
    min_enemy_distance_px: int = 60       # ignore mobs déjà collés (engage direct)
    max_enemy_distance_px: int = 900       # ignore mobs trop loin (hors écran)

    # Ratio du bouton "Prêt au combat" (apparaît après avoir cliqué sur un mob)
    # Typiquement au centre-bas de l'écran, bouton vert
    ready_button_ratio: tuple[float, float] = (0.50, 0.78)   # (x, y) relatif à la frame


@dataclass
class HuntStats:
    scans: int = 0
    mobs_detected: int = 0
    engages_attempted: int = 0
    combats_entered: int = 0
    combats_finished: int = 0


class HuntWorker(QThread):
    """Scanne le monde ouvert et déclenche un combat sur le mob le plus proche."""

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)                 # "scanning" / "engaging" / "in_combat" / "stopped"
    engagement_started = pyqtSignal()               # mob cliqué, combat en démarrage
    combat_started = pyqtSignal()                   # combat officiellement détecté
    combat_finished = pyqtSignal()                  # retour en world
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: HuntConfig | None = None,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config or HuntConfig()
        self._state_reader = CombatStateReader(vision)
        self._detector = CombatDetector(vision)
        self._stats = HuntStats()
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        self.log_event.emit("🏹 HuntWorker démarré — scan du monde ouvert", "info")
        self.state_changed.emit("scanning")

        while not self._stop_requested:
            try:
                self._tick()
            except Exception as exc:
                self.log_event.emit(f"⚠ Erreur hunt tick : {exc}", "error")

            self.stats_updated.emit(self._stats)
            if not self._stop_requested:
                self.msleep(int(self._config.scan_interval_sec * 1000))

        self.log_event.emit("⏹ HuntWorker arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    # ---------- Tick principal ----------

    def _tick(self) -> None:
        # Scan cercles bleus en world
        self._stats.scans += 1
        state = self._state_reader.read()
        enemies = state.ennemis
        self._stats.mobs_detected += len(enemies)

        if not enemies:
            self.state_changed.emit("scanning")
            return

        # Trouve le mob le plus proche du perso (fallback : le plus proche du centre)
        target = self._choose_target(state.perso, enemies)
        if target is None:
            return

        self.log_event.emit(
            f"🎯 Mob repéré à ({target.x}, {target.y}) — engage",
            "info",
        )
        self.state_changed.emit("engaging")
        self._engage(target)

    def _choose_target(
        self,
        perso: EntityDetection | None,
        enemies: list[EntityDetection],
    ) -> EntityDetection | None:
        if not enemies:
            return None

        # Reference point : perso si dispo, sinon centre écran
        if perso is not None:
            ref_x, ref_y = perso.x, perso.y
        else:
            try:
                frame = self._vision.capture()
                h, w = frame.shape[:2]
                ref_x, ref_y = w // 2, h // 2
            except Exception:
                return enemies[0]

        # Filtre min/max distance
        candidates = []
        for e in enemies:
            dist = float(np.hypot(e.x - ref_x, e.y - ref_y))
            if dist < self._config.min_enemy_distance_px:
                continue
            if dist > self._config.max_enemy_distance_px:
                continue
            candidates.append((dist, e))

        if not candidates:
            # Fallback : le plus proche même si hors seuils
            return min(enemies, key=lambda e: (e.x - ref_x) ** 2 + (e.y - ref_y) ** 2)

        candidates.sort(key=lambda t: t[0])
        return candidates[0][1]

    def _engage(self, target: EntityDetection) -> None:
        """Clique sur le mob, clique 'Prêt', puis cède la main au CombatRunner."""
        self._stats.engages_attempted += 1
        try:
            self._input.click(target.x, target.y, button="left")
        except Exception as exc:
            self.log_event.emit(f"⚠ Clic mob échoué : {exc}", "error")
            return

        self.engagement_started.emit()
        self.msleep(int(self._config.post_click_wait_sec * 1000))

        # Clique sur "Prêt au combat" (bouton vert qui peut apparaître)
        self._click_ready_button()
        self.msleep(int(self._config.post_ready_wait_sec * 1000))

        # Assume qu'on est en combat après clic + ready (le serveur privé peut ne pas
        # rendre le bouton TERMINER distinguable entre monde ouvert et combat).
        self.log_event.emit("⚔ Engagement lancé — CombatRunner prend la main", "info")
        self._stats.combats_entered += 1
        self.combat_started.emit()
        self.state_changed.emit("in_combat")
        self._wait_for_combat_end()

    def _click_ready_button(self) -> None:
        """Clique sur le bouton 'Prêt au combat' (position ratio)."""
        try:
            frame = self._vision.capture()
            h, w = frame.shape[:2]
            region = getattr(self._vision, "last_capture_region", None)
            rx = int(w * self._config.ready_button_ratio[0])
            ry = int(h * self._config.ready_button_ratio[1])
            if region is not None:
                rx += region.x
                ry += region.y
            self._input.click(rx, ry, button="left")
        except Exception:
            pass  # non-bloquant

    def _wait_for_combat_end(self) -> None:
        """Bloque tant qu'on voit des ennemis À PORTÉE DE COMBAT du perso.

        Clé : on filtre les ennemis par proximité au perso. En combat, les mobs
        sont proches (même grille). Hors combat, on peut voir des mobs lointains
        sur la map mais ils ne signifient pas qu'on est en combat.

        Fin de combat = aucun ennemi dans `COMBAT_RADIUS_PX` du perso pendant
        `no_enemy_grace_sec` sec continues.
        """
        COMBAT_RADIUS_PX = 600     # rayon autour du perso où un ennemi = "en combat"
        no_enemy_grace_sec = 5.0
        combat_max_duration_sec = 180.0
        t_start = time.time()
        t_last_enemy_seen = time.time()

        while not self._stop_requested:
            state = self._state_reader.read()
            now = time.time()

            # Compte ennemis PROCHES du perso (pas ceux éparpillés sur la map)
            nearby_enemies = 0
            if state.perso is not None:
                px, py = state.perso.x, state.perso.y
                for e in state.ennemis:
                    dist = float(np.hypot(e.x - px, e.y - py))
                    if dist <= COMBAT_RADIUS_PX:
                        nearby_enemies += 1
            else:
                # Sans perso détecté, on suppose que les ennemis visibles comptent tous
                nearby_enemies = len(state.ennemis)

            if nearby_enemies > 0:
                t_last_enemy_seen = now
            else:
                # Plus d'ennemi proche : grace period avant de conclure "fin"
                elapsed_no_enemy = now - t_last_enemy_seen
                if elapsed_no_enemy >= no_enemy_grace_sec:
                    self.log_event.emit(
                        f"✓ Combat terminé ({now - t_start:.1f}s total, "
                        f"{elapsed_no_enemy:.1f}s sans ennemi proche)",
                        "info",
                    )
                    # Tente de fermer un éventuel popup "Résultats de combat"
                    self._close_combat_result_popup()
                    self._stats.combats_finished += 1
                    self.combat_finished.emit()
                    self.state_changed.emit("scanning")
                    self.msleep(int(self._config.post_combat_cooldown_sec * 1000))
                    return

            if (now - t_start) > combat_max_duration_sec:
                self.log_event.emit(
                    f"⚠ Combat trop long ({combat_max_duration_sec}s) — timeout forcé",
                    "warn",
                )
                self._close_combat_result_popup()
                self._stats.combats_finished += 1
                self.combat_finished.emit()
                self.state_changed.emit("scanning")
                return

            self.msleep(800)

    def _close_combat_result_popup(self) -> None:
        """Appuie sur Échap pour fermer le popup de fin de combat s'il existe."""
        try:
            self._input.press_key("escape")
            self.msleep(300)
            # Au cas où le popup résiste, on tente un 2e Escape
            self._input.press_key("escape")
        except Exception:
            pass  # non bloquant
