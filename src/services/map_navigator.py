"""Navigation map-par-map en cliquant sur les bords selon les coords Dofus.

Système de coords Dofus :
    haut  = y - 1
    bas   = y + 1
    gauche = x - 1
    droite = x + 1

Usage :
    nav = MapNavigator(vision, input_svc, map_locator, dofus_window_title="...")
    result = nav.go_to((9, 8))
    if result.success:
        print("Arrivé à", result.final_pos)

Algorithme :
    Boucle :
      1. OCR coords courantes
      2. Calcule dx, dy
      3. Si dx != 0 : clique bord droite/gauche
      4. Sinon si dy != 0 : clique bord haut/bas
      5. Attend le map change (OCR coords jusqu'au changement ou timeout)
      6. Goto 1
    Stop quand arrivé OU max_hops atteint OU échec changement map.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum

import numpy as np
from loguru import logger

from src.services.input_service import InputService
from src.services.map_locator import MapInfo, MapLocator
from src.services.vision import MssVisionService


class NavOutcome(str, Enum):
    SUCCESS = "success"
    FAIL_NO_WINDOW = "no_window"
    FAIL_NO_CURRENT_POS = "no_current_pos"
    FAIL_MAX_HOPS = "max_hops"
    FAIL_STUCK = "stuck"  # coord n'a pas changé après un clic bord
    FAIL_EXCEPTION = "exception"


@dataclass
class NavResult:
    outcome: NavOutcome
    message: str = ""
    start_pos: tuple[int, int] | None = None
    final_pos: tuple[int, int] | None = None
    target: tuple[int, int] | None = None
    hops: int = 0

    @property
    def success(self) -> bool:
        return self.outcome == NavOutcome.SUCCESS


@dataclass
class EdgeRatios:
    """Positions des zones de clic sur chaque bord, en ratios de la fenêtre Dofus.

    Calibrés pour cliquer JUSTE sur le bord de la zone jouable où Dofus
    déclenche la flèche de transition. Surchargeable depuis les prefs user
    via la calibration visuelle (bouton "Calibrer les bords").
    """
    # Bord haut (y -= 1) : centre horizontal, juste sous la barre de coords
    top_x: float = 0.50
    top_y: float = 0.04
    # Bord bas (y += 1) : au niveau du bord bas de la zone jouable (avant UI)
    bottom_x: float = 0.50
    bottom_y: float = 0.82
    # Bord gauche (x -= 1)
    left_x: float = 0.015
    left_y: float = 0.45
    # Bord droit (x += 1)
    right_x: float = 0.985
    right_y: float = 0.45

    def to_dict(self) -> dict:
        return {
            "top_x": self.top_x, "top_y": self.top_y,
            "bottom_x": self.bottom_x, "bottom_y": self.bottom_y,
            "left_x": self.left_x, "left_y": self.left_y,
            "right_x": self.right_x, "right_y": self.right_y,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EdgeRatios":
        return cls(
            top_x=float(d.get("top_x", 0.50)), top_y=float(d.get("top_y", 0.04)),
            bottom_x=float(d.get("bottom_x", 0.50)), bottom_y=float(d.get("bottom_y", 0.82)),
            left_x=float(d.get("left_x", 0.015)), left_y=float(d.get("left_y", 0.45)),
            right_x=float(d.get("right_x", 0.985)), right_y=float(d.get("right_y", 0.45)),
        )


class MapNavigator:
    """Déplace le personnage map-par-map via clics sur les bords."""

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        map_locator: MapLocator,
        window_title: str | None = None,
        edge_ratios: EdgeRatios | None = None,
        max_hops: int = 30,
        transition_timeout_sec: float = 8.0,
        transition_poll_sec: float = 0.5,
        post_arrival_wait_sec: float = 0.8,
        log_callback=None,   # fn(msg, level) pour remonter dans l'UI
    ) -> None:
        self._vision = vision
        self._input = input_svc
        self._locator = map_locator
        self._window_title = window_title
        self._ratios = edge_ratios or EdgeRatios()
        self._max_hops = max_hops
        self._transition_timeout = transition_timeout_sec
        self._transition_poll = transition_poll_sec
        self._post_arrival_wait = post_arrival_wait_sec
        self._log_cb = log_callback

    def _log(self, msg: str, level: str = "info") -> None:
        """Log double : loguru + callback UI si fourni."""
        if level == "error":
            logger.error(msg)
        elif level == "warn":
            logger.warning(msg)
        else:
            logger.info(msg)
        if self._log_cb is not None:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def go_to(
        self,
        target: tuple[int, int],
        prefer_horizontal_first: bool = True,
        start_pos: tuple[int, int] | None = None,
    ) -> NavResult:
        """Navigue vers les coords cibles en cliquant les bords.

        Args:
            target: (x, y) coords Dofus.
            prefer_horizontal_first: si True, épuise dx avant dy (plus humain).
            start_pos: position de départ CONNUE (optionnel). Si fourni, on skip
                l'OCR initial — utile quand le FarmWorker sait déjà où on est
                (ex: dernière position après nav précédente, ou position manuelle
                assumée).
        """
        try:
            return self._do_navigate(target, prefer_horizontal_first, start_pos)
        except Exception as exc:
            logger.exception("MapNavigator : exception")
            return NavResult(outcome=NavOutcome.FAIL_EXCEPTION, message=str(exc), target=target)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _do_navigate(
        self,
        target: tuple[int, int],
        horizontal_first: bool,
        start_pos_hint: tuple[int, int] | None = None,
    ) -> NavResult:
        start_pos: tuple[int, int]
        if start_pos_hint is not None:
            # Position connue fournie par l'appelant → on skip l'OCR fragile
            start_pos = start_pos_hint
            self._log(f"🧭 Navigation {start_pos} → {target} (start_pos connu)")
        else:
            current_info = self._locate_with_retries(retries=3)
            if current_info is None or not current_info.is_valid:
                return NavResult(
                    outcome=NavOutcome.FAIL_NO_CURRENT_POS,
                    message="OCR des coords initiales échoué",
                    target=target,
                )
            start_pos = current_info.coords
            self._log(f"🧭 Navigation {start_pos} → {target}")

        win = self._get_window_rect()
        if win is None:
            return NavResult(
                outcome=NavOutcome.FAIL_NO_WINDOW,
                message="Fenêtre Dofus introuvable",
                start_pos=start_pos,
                target=target,
            )

        hops = 0
        last_pos = start_pos
        while last_pos != target and hops < self._max_hops:
            dx = target[0] - last_pos[0]
            dy = target[1] - last_pos[1]

            # Choix de direction : horizontal d'abord, puis vertical
            if horizontal_first and dx != 0:
                direction = "droite" if dx > 0 else "gauche"
            elif dy != 0:
                direction = "bas" if dy > 0 else "haut"
            elif dx != 0:
                direction = "droite" if dx > 0 else "gauche"
            else:
                break  # déjà arrivé (shouldn't happen due to while condition)

            self._log(f"  hop {hops + 1} : {last_pos} [{direction}] (dx={dx}, dy={dy})")
            # Calcule le coord attendu après ce clic (last_pos ± 1 dans la direction)
            expected = self._expected_after(last_pos, direction)
            # Capture frame avant clic pour détection visuelle si OCR foire
            try:
                frame_before = self._vision.capture()
            except Exception:
                frame_before = None
            self._click_edge(direction, win)
            hops += 1

            # Attend le changement (validation OCR + fallback visuel)
            new_info = self._wait_for_coord_change(last_pos, expected=expected, frame_before=frame_before)
            if new_info is None or not new_info.is_valid:
                self._log(
                    f"⚠️ Pas de changement après clic '{direction}' — le bord ne réagit pas",
                    "error",
                )
                return NavResult(
                    outcome=NavOutcome.FAIL_STUCK,
                    message=f"Pas de changement de coords après clic '{direction}' (hop {hops})",
                    start_pos=start_pos,
                    final_pos=last_pos,
                    target=target,
                    hops=hops,
                )
            if new_info.coords != expected:
                self._log(
                    f"  ⚠ OCR lu {new_info.coords} mais attendu {expected} — on force la valeur attendue",
                    "warn",
                )
                # On fait confiance à l'expected (les bords Dofus changent toujours de ±1)
                last_pos = expected
            else:
                self._log(f"  ✓ arrivé {new_info.coords}")
                last_pos = new_info.coords
            # Pause post-arrivée pour laisser la map se stabiliser
            time.sleep(self._post_arrival_wait)

        if last_pos == target:
            return NavResult(
                outcome=NavOutcome.SUCCESS,
                message=f"Arrivé en {hops} hops",
                start_pos=start_pos,
                final_pos=last_pos,
                target=target,
                hops=hops,
            )
        return NavResult(
            outcome=NavOutcome.FAIL_MAX_HOPS,
            message=f"Max hops {self._max_hops} atteint sans arriver",
            start_pos=start_pos,
            final_pos=last_pos,
            target=target,
            hops=hops,
        )

    def _click_edge(self, direction: str, win: tuple[int, int, int, int]) -> None:
        """Clique sur la zone de bord correspondante.

        Utilise les dims de la **capture** (zone client du jeu) plutôt que les dims
        de la fenêtre outer — ces dernières incluent la barre de titre et les
        bordures Windows qui faussent les ratios (clic tombe hors zone de jeu).
        """
        r = self._ratios
        if direction == "haut":
            rx, ry = r.top_x, r.top_y
        elif direction == "bas":
            rx, ry = r.bottom_x, r.bottom_y
        elif direction == "gauche":
            rx, ry = r.left_x, r.left_y
        elif direction == "droite":
            rx, ry = r.right_x, r.right_y
        else:
            logger.warning("MapNavigator : direction inconnue '{}'", direction)
            return

        # Essaie d'utiliser la dernière capture (plus fiable que window dims)
        region = getattr(self._vision, "last_capture_region", None)
        if region is None:
            # Fallback : capture maintenant pour récupérer la région
            try:
                self._vision.capture()
                region = getattr(self._vision, "last_capture_region", None)
            except Exception:
                region = None

        if region is not None:
            # Utilise les dims de la capture (= zone de jeu réelle)
            cx = int(region.x) + int(int(region.w) * rx) + random.randint(-15, 15)
            cy = int(region.y) + int(int(region.h) * ry) + random.randint(-10, 10)
        else:
            # Fallback : window dims (moins précis)
            wx, wy, ww, wh = win
            cx = wx + int(ww * rx) + random.randint(-15, 15)
            cy = wy + int(wh * ry) + random.randint(-10, 10)

        self._log(f"    → clic bord '{direction}' écran=({cx},{cy})")
        self._input.click(cx, cy, button="left", jitter=False)

    def _wait_for_coord_change(
        self,
        last_pos: tuple[int, int],
        expected: tuple[int, int] | None = None,
        frame_before: np.ndarray | None = None,
    ) -> MapInfo | None:
        """Attend qu'OCR détecte de nouvelles coords.

        Stratégie :
          1. Si l'OCR donne `expected` → return direct
          2. Si l'OCR donne un coord ≠ last_pos → fallback
          3. Si l'image a VISUELLEMENT changé mais OCR reste bloqué sur last_pos
             → on suppose que la map a changé et on retourne expected (car OCR peu fiable)
        """
        import cv2  # noqa: PLC0415
        start = time.time()
        time.sleep(0.6)
        fallback_info: MapInfo | None = None
        while time.time() - start < self._transition_timeout:
            info = self._locator.locate()
            if info is not None and info.is_valid and info.coords != last_pos:
                if expected is None or info.coords == expected:
                    return info
                if fallback_info is None:
                    fallback_info = info
            time.sleep(self._transition_poll)

        # OCR bloqué → tente détection visuelle
        if expected is not None and frame_before is not None:
            try:
                frame_after = self._vision.capture()
                if frame_after is not None and frame_after.shape == frame_before.shape:
                    gray_a = cv2.cvtColor(frame_before, cv2.COLOR_BGR2GRAY)
                    gray_b = cv2.cvtColor(frame_after, cv2.COLOR_BGR2GRAY)
                    diff = cv2.absdiff(gray_a, gray_b)
                    pct = 100.0 * (diff > 30).sum() / max(diff.size, 1)
                    if pct >= 15.0:
                        # L'image a clairement changé → on force le coord attendu
                        self._log(
                            f"  📷 Map visuellement changée ({pct:.0f}%) — OCR bloqué, on force {expected}",
                            "info",
                        )
                        return MapInfo(x=expected[0], y=expected[1], raw_ocr="(visual-forced)")
            except Exception:
                pass

        return fallback_info

    @staticmethod
    def _expected_after(last_pos: tuple[int, int], direction: str) -> tuple[int, int]:
        """Coord attendu après un clic sur le bord `direction` (±1 sur un axe)."""
        dx = {"gauche": -1, "droite": 1}.get(direction, 0)
        dy = {"haut": -1, "bas": 1}.get(direction, 0)
        return (last_pos[0] + dx, last_pos[1] + dy)

    def _locate_with_retries(self, retries: int = 3) -> MapInfo | None:
        """OCR avec retries (pour rattraper un moment où la bannière clignote)."""
        for _ in range(retries):
            info = self._locator.locate()
            if info is not None and info.is_valid:
                return info
            time.sleep(0.3)
        return info  # dernière tentative (peut-être invalide)

    def _get_window_rect(self) -> tuple[int, int, int, int] | None:
        if not self._window_title:
            return None
        try:
            import pygetwindow as gw  # noqa: PLC0415
            matches = gw.getWindowsWithTitle(self._window_title)
            if not matches:
                return None
            w = matches[0]
            if w.isMinimized:
                w.restore()
            try:
                w.activate()
            except Exception:
                pass
            return (int(w.left), int(w.top), int(w.width), int(w.height))
        except Exception as exc:
            logger.warning("MapNavigator : get window rect échoué — {}", exc)
            return None
