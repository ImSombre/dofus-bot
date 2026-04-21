"""Humanisation des entrées (souris + clavier) pour éviter la détection.

Inspiration :
  - BezMouse (Bézier curves, 400h+ sans détection)
  - HumanCursor (variable speed, acceleration, curvature)
  - WindMouse algorithm (simulation wind-like movement)

Fonctions principales :
  - `human_mouse_path(start, end)` : génère une liste de points (x, y) formant
    une courbe de Bézier avec jitter naturel.
  - `human_click(input_svc, x, y)` : déplace la souris en suivant la courbe
    puis clique avec micro-offset (évite clic pixel-exact).
  - `human_delay(low, high)` : délai log-normal (distribution plus réaliste
    qu'uniform — un humain n'est jamais constant).

Intégration : le worker peut appeler `human_click` au lieu de `input.click`
pour rendre les actions indistinguables d'un humain.
"""
from __future__ import annotations

import math
import random
import time

from loguru import logger


def _bezier_quadratic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """Point sur la courbe de Bézier quadratique au paramètre t ∈ [0, 1]."""
    mt = 1 - t
    x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
    return (x, y)


def human_mouse_path(
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    num_steps: int | None = None,
    curve_strength: float = 0.25,
    jitter_amplitude: float = 2.0,
) -> list[tuple[int, int]]:
    """Génère une liste de points pour déplacement souris "humain".

    Args:
        start, end: Pixels source et cible.
        num_steps: Nombre de points sur la courbe. Auto = f(distance).
        curve_strength: Amplitude de la déviation hors ligne droite (0 = droit,
            1 = forte courbe). 0.25 est un bon défaut.
        jitter_amplitude: ±pixels de bruit aléatoire par point.

    Returns:
        Liste de (x, y) ordonnée start → end.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)

    if num_steps is None:
        # ~1 step tous les 10 pixels, min 5 steps max 60
        num_steps = max(5, min(60, int(distance / 10)))

    # Point de contrôle perpendiculaire à la ligne start→end
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    # Perpendiculaire à (dx, dy) : (-dy, dx)
    perp_dx = -dy
    perp_dy = dx
    perp_length = max(1.0, math.hypot(perp_dx, perp_dy))
    offset_amount = (random.random() - 0.5) * 2 * curve_strength * distance
    ctrl_x = mid_x + (perp_dx / perp_length) * offset_amount
    ctrl_y = mid_y + (perp_dy / perp_length) * offset_amount

    points: list[tuple[int, int]] = []
    for i in range(num_steps + 1):
        t = i / num_steps
        # Easing : accélération + décélération naturelle (ease-in-out)
        t_eased = 0.5 - 0.5 * math.cos(math.pi * t)
        x, y = _bezier_quadratic(start, (ctrl_x, ctrl_y), end, t_eased)
        # Jitter aléatoire
        if jitter_amplitude > 0 and 0 < i < num_steps:
            jx = (random.random() - 0.5) * 2 * jitter_amplitude
            jy = (random.random() - 0.5) * 2 * jitter_amplitude
            x += jx
            y += jy
        points.append((int(round(x)), int(round(y))))
    return points


def human_delay(
    low_ms: float = 50,
    high_ms: float = 200,
    mu_factor: float = 0.6,
) -> float:
    """Génère un délai "humain" via distribution log-normal.

    Un humain a des temps de réaction suivant ~log-normale (la plupart
    courts, mais de temps en temps un long). Pas uniform.

    Returns:
        Délai en secondes.
    """
    mean_ms = low_ms + mu_factor * (high_ms - low_ms)
    sigma = math.log(2)  # variance modérée
    mu = math.log(mean_ms)
    ms = random.lognormvariate(mu, sigma)
    ms = max(low_ms, min(high_ms, ms))
    return ms / 1000.0


def human_click_offset(radius: int = 4) -> tuple[int, int]:
    """Retourne un offset (dx, dy) aléatoire pour varier le clic final.

    Évite de toujours cliquer au pixel exact (détection suspecte).
    """
    # Distribution gaussienne centrée sur (0, 0)
    dx = int(round(random.gauss(0, radius / 2)))
    dy = int(round(random.gauss(0, radius / 2)))
    # Clamp à ±radius
    dx = max(-radius, min(radius, dx))
    dy = max(-radius, min(radius, dy))
    return (dx, dy)


def human_click(
    input_svc,
    x: int,
    y: int,
    *,
    button: str = "left",
    move_speed_mult: float = 1.0,
    click_offset_radius: int = 3,
) -> None:
    """Effectue un clic "humain" : mouvement Bézier + offset pixel + délais.

    Args:
        input_svc: Instance InputService (méthodes `move_mouse`, `click`).
        x, y: Pixel cible.
        button: 'left', 'right', 'middle'.
        move_speed_mult: Multiplicateur vitesse (1.0 = normal, 0.5 = lent).
        click_offset_radius: Rayon max du décalage pixel final.
    """
    # Position actuelle souris
    try:
        import pyautogui  # noqa: PLC0415
        current_x, current_y = pyautogui.position()
    except Exception:
        # Fallback : clic direct sans path
        input_svc.click(x, y, button=button)
        return

    # Génère le path Bézier
    path = human_mouse_path((current_x, current_y), (x, y))
    if len(path) <= 2:
        # Trop proche → clic direct
        ox, oy = human_click_offset(click_offset_radius)
        input_svc.click(x + ox, y + oy, button=button)
        return

    # Déplace la souris le long du path
    try:
        import pyautogui  # noqa: PLC0415
        # Durée totale : ~100-400ms selon distance
        total_duration = 0.05 + len(path) * 0.008 * move_speed_mult
        step_duration = total_duration / len(path)
        for px, py in path:
            pyautogui.moveTo(px, py, duration=0)
            time.sleep(step_duration)
    except Exception as exc:
        logger.debug("Humain mouse path échec : {}", exc)

    # Micro-délai avant clic (humain ne clique pas instantanément en arrivant)
    time.sleep(human_delay(30, 120))

    # Offset final du clic
    ox, oy = human_click_offset(click_offset_radius)
    input_svc.click(x + ox, y + oy, button=button)
