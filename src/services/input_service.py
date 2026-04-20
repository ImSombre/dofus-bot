"""Service de simulation clavier/souris.

Stratégie double :
  - pydirectinput (SendInput API bas-niveau) en priorité : marche avec les jeux
    qui rejettent les clics pyautogui (Dofus, DirectX, etc.)
  - pyautogui en fallback
  - Humanisation : jitter ±N px, délais variables, mouvement avec durée
"""

from __future__ import annotations

import ctypes
import math
import random
import time
from typing import Protocol

from loguru import logger


# ---------------------------------------------------------------------------
# Win32 low-level fallback (mouse_event legacy API)
# ---------------------------------------------------------------------------
# Certains jeux (Dofus inclus) filtrent les SendInput ayant le flag
# LLMHF_INJECTED. L'API legacy mouse_event écrit plus bas dans la pile
# d'événements et n'a pas ce flag → souvent acceptée là où pydirectinput
# et pyautogui (tous deux basés sur SendInput) sont rejetés.

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040


def _win32_click(x: int, y: int, button: str = "left") -> bool:
    """Effectue un clic via mouse_event (API legacy, pré-SendInput).

    Retourne True si succès. Move puis click, tout via ctypes.
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.04)
        if button == "right":
            user32.mouse_event(_MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(random.uniform(0.04, 0.09))
            user32.mouse_event(_MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        elif button == "middle":
            user32.mouse_event(_MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
            time.sleep(random.uniform(0.04, 0.09))
            user32.mouse_event(_MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
        else:
            user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(random.uniform(0.04, 0.09))
            user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True
    except Exception as exc:
        logger.debug("win32 mouse_event échec : {}", exc)
        return False


def _win32_set_cursor(x: int, y: int) -> bool:
    try:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        return True
    except Exception:
        return False


def _win32_get_cursor() -> tuple[int, int] | None:
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        p = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y
    except Exception:
        return None


def get_active_window_title() -> str:
    """Titre de la fenêtre actuellement au premier plan (pour diagnostic)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


class InputService(Protocol):
    def move_mouse(self, x: int, y: int, duration_ms: int | None = None) -> None: ...
    def click(self, x: int, y: int, button: str = "left", jitter: bool = True) -> None: ...
    def double_click(self, x: int, y: int, button: str = "left", jitter: bool = True) -> None: ...
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None: ...
    def press_key(self, key: str) -> None: ...
    def type_text(self, text: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...


class PyAutoGuiInputService:
    """Implémentation combinant pydirectinput (prioritaire) + pyautogui (fallback)."""

    def __init__(self, humanize: bool = True, jitter_px: int = 4) -> None:
        self._humanize = humanize
        self._jitter_px = jitter_px
        self._pyautogui = None
        self._pydirectinput = None

    def _get_pyautogui(self):
        if self._pyautogui is None:
            import pyautogui  # noqa: PLC0415
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0.0
            self._pyautogui = pyautogui
        return self._pyautogui

    def _get_pydirectinput(self):
        if self._pydirectinput is None:
            try:
                import pydirectinput  # noqa: PLC0415
                pydirectinput.FAILSAFE = False
                pydirectinput.PAUSE = 0.0
                self._pydirectinput = pydirectinput
            except ImportError:
                logger.warning("pydirectinput absent → fallback pyautogui (moins fiable sur Dofus)")
                self._pydirectinput = False  # marqueur "indisponible"
        return self._pydirectinput if self._pydirectinput is not False else None

    def _jitter(self, x: int, y: int) -> tuple[int, int]:
        if not self._humanize:
            return x, y
        return (
            x + random.randint(-self._jitter_px, self._jitter_px),
            y + random.randint(-self._jitter_px, self._jitter_px),
        )

    def _random_duration_sec(self, lo_ms: int = 120, hi_ms: int = 260) -> float:
        if not self._humanize:
            return 0.02
        return random.uniform(lo_ms, hi_ms) / 1000.0

    def _post_action_pause(self) -> None:
        if self._humanize:
            time.sleep(random.uniform(0.05, 0.15))

    # ---------- Mouvement humanisé (courbe de Bézier) ----------

    def _human_move(self, target_x: int, target_y: int) -> None:
        """Déplace la souris en courbe de Bézier avec vitesse variable.

        - Durée proportionnelle à la distance (300 ms à 1200 ms)
        - Courbe quadratique avec point de contrôle perpendiculaire (aléatoire)
        - Easing : démarrage lent, milieu rapide, fin lente (comme un humain)
        """
        pg = self._get_pyautogui()
        pdi = self._get_pydirectinput()

        # Position actuelle
        sx, sy = pg.position()
        dx, dy = target_x - sx, target_y - sy
        distance = math.hypot(dx, dy)

        if distance < 3:
            # Déjà pile dessus — évite le micro-jitter
            return

        # Durée scaled sur la distance (un mouvement de 1000 px ≈ 700 ms)
        duration = min(1.2, max(0.25, distance / 1500))
        if not self._humanize:
            duration = min(duration, 0.15)

        # Nombre de steps (~60 FPS)
        steps = max(8, int(duration * 60))

        # Point de contrôle Bézier : offset perpendiculaire aléatoire (courbure naturelle)
        mid_x = (sx + target_x) / 2
        mid_y = (sy + target_y) / 2
        if self._humanize and distance > 50:
            perp_x = -dy / distance
            perp_y = dx / distance
            # Offset entre ±15% de la distance
            offset_ratio = random.uniform(-0.15, 0.15)
            cx = mid_x + perp_x * distance * offset_ratio
            cy = mid_y + perp_y * distance * offset_ratio
        else:
            cx, cy = mid_x, mid_y

        sleep_per_step = duration / steps

        def ease(t: float) -> float:
            """Ease-in-out (sinusoïdal) : vitesse variable, plus naturelle."""
            return 0.5 - 0.5 * math.cos(math.pi * t)

        last_pt = (int(sx), int(sy))
        for i in range(1, steps + 1):
            raw_t = i / steps
            t = ease(raw_t)
            # Quadratic Bezier B(t) = (1-t)^2 P0 + 2(1-t)t P1 + t^2 P2
            omt = 1 - t
            bx = omt * omt * sx + 2 * omt * t * cx + t * t * target_x
            by = omt * omt * sy + 2 * omt * t * cy + t * t * target_y
            nx, ny = int(bx), int(by)
            # Évite les doublons (même pixel)
            if (nx, ny) != last_pt:
                # Priorité win32 SetCursorPos (non-SendInput, plus fiable avec jeux)
                if not _win32_set_cursor(nx, ny):
                    # Fallback pydirectinput puis pyautogui
                    try:
                        if pdi is not None:
                            pdi.moveTo(nx, ny)
                        else:
                            pg.moveTo(nx, ny, _pause=False)
                    except Exception:
                        try:
                            pg.moveTo(nx, ny, _pause=False)
                        except Exception:
                            pass
                last_pt = (nx, ny)
            time.sleep(sleep_per_step)

    # ---------- API publique ----------

    def move_mouse(self, x: int, y: int, duration_ms: int | None = None) -> None:
        tx, ty = self._jitter(x, y)
        self._human_move(tx, ty)

    def click(self, x: int, y: int, button: str = "left", jitter: bool = True) -> None:
        if jitter:
            tx, ty = self._jitter(x, y)
        else:
            tx, ty = x, y

        # 1) Mouvement humanisé (Bézier + ease-in-out)
        self._human_move(tx, ty)

        # 2) Petite pause "humaine" avant de cliquer (40-140 ms)
        time.sleep(random.uniform(0.04, 0.14))

        # 3) Log diagnostique : position réelle + fenêtre active
        pos_before = _win32_get_cursor()
        active = get_active_window_title()
        logger.info(
            "Clic {} demandé ({},{}) | pos réelle {} | fenêtre active : '{}'",
            button, tx, ty, pos_before, active[:60],
        )

        # 4) Triple fallback : win32 legacy → pydirectinput → pyautogui
        if _win32_click(tx, ty, button):
            logger.debug("✓ Clic {} via win32 mouse_event (legacy)", button)
            self._post_action_pause()
            return

        pdi = self._get_pydirectinput()
        if pdi is not None:
            try:
                pdi.click(tx, ty, button=button)
                logger.debug("✓ Clic {} via pydirectinput", button)
                self._post_action_pause()
                return
            except Exception as exc:
                logger.debug("pydirectinput.click échec : {}", exc)

        pg = self._get_pyautogui()
        try:
            pg.click(tx, ty, button=button)
            logger.debug("✓ Clic {} via pyautogui (fallback final)", button)
        except Exception as exc:
            raise exc
        self._post_action_pause()

    def double_click(self, x: int, y: int, button: str = "left", jitter: bool = True) -> None:
        """Double-clic rapide : mouvement humanisé + deux clics win32 dans la fenêtre de détection (<500ms).

        Bypass les pauses humanisées entre les deux clics pour rester sous le seuil Windows.
        """
        if jitter:
            tx, ty = self._jitter(x, y)
        else:
            tx, ty = x, y

        # 1. Mouvement humanisé
        self._human_move(tx, ty)
        time.sleep(random.uniform(0.04, 0.10))

        logger.info("Double-clic {} demandé ({},{})", button, tx, ty)

        # 2. Deux clics win32 legacy rapprochés (~100 ms entre les deux mouseDown)
        _win32_click(tx, ty, button)
        time.sleep(random.uniform(0.06, 0.11))
        _win32_click(tx, ty, button)
        self._post_action_pause()

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        pg = self._get_pyautogui()
        sx, sy = self._jitter(x1, y1)
        ex, ey = self._jitter(x2, y2)
        pg.moveTo(sx, sy, duration=self._random_duration_sec())
        pg.mouseDown()
        pg.moveTo(ex, ey, duration=self._random_duration_sec(200, 500))
        pg.mouseUp()
        self._post_action_pause()

    # ---------- Keyboard ----------

    def press_key(self, key: str) -> None:
        pdi = self._get_pydirectinput()
        if pdi is not None:
            try:
                pdi.press(key)
                self._post_action_pause()
                return
            except Exception:
                pass
        self._get_pyautogui().press(key)
        self._post_action_pause()

    def type_text(self, text: str) -> None:
        pg = self._get_pyautogui()
        interval = 0.04 if self._humanize else 0.0
        pg.typewrite(text, interval=interval)
        self._post_action_pause()

    def hotkey(self, *keys: str) -> None:
        self._get_pyautogui().hotkey(*keys)
        self._post_action_pause()
