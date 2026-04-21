"""Détection de ligne de vue (LoS) Dofus sur pixels de capture.

Approche : Bresenham entre perso et cible, puis sampling de pixels le long
de la ligne pour détecter des obstacles visuels (murs, colonnes, rochers).

Inspiration :
  - Algorithme ArakneUtils (BattlefieldSight : iterator cellule par cellule)
  - Dofus Wiki : "draw a line from center of caster to center of target,
    check that all cells on the line are free of obstacles"
  - Bresenham classique pour tracer la ligne en pixels

Comme on ne connaît pas la grille logique de la map (pas de MITM, juste capture
écran), on travaille en pixels : la ligne pixelisée est un proxy raisonnable
de la ligne de cases.

Paramètres à calibrer selon le rendering Dofus :
  - OBSTACLE_COLORS_HSV : fourchettes HSV des murs en pierre
  - SAMPLING_STEP : 1 sample tous les N pixels (ratio précision/vitesse)
  - OBSTACLE_THRESHOLD_RATIO : % minimum de pixels "obstacle" pour bloquer
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger


# Couleurs HSV caractéristiques des obstacles bloquant la ligne de vue
# dans Dofus 2.64. À ajuster selon les captures réelles.
#
# Famille "pierre/mur" : teinte beige-gris, saturation moyenne, valeur moyenne
# Famille "rocher sombre" : plus sombre, moins saturé
# On exclut : herbe (vert), eau (bleu), sable (jaune clair trop pâle).
@dataclass(frozen=True)
class HsvRange:
    h_min: int
    h_max: int
    s_min: int
    s_max: int
    v_min: int
    v_max: int
    label: str = "obstacle"


# Ranges HSV en espace OpenCV (H: 0-179, S/V: 0-255)
OBSTACLE_HSV_RANGES: tuple[HsvRange, ...] = (
    # Pierre claire / mur : beige-gris chaud
    HsvRange(h_min=10, h_max=30, s_min=25, s_max=110, v_min=140, v_max=215,
             label="pierre_claire"),
    # Pierre sombre / colonne : gris neutre
    HsvRange(h_min=0, h_max=30, s_min=0, s_max=50, v_min=70, v_max=150,
             label="pierre_sombre"),
)


@dataclass
class LoSResult:
    """Résultat d'un test de ligne de vue."""
    is_clear: bool
    """True si la LoS est libre, False si bloquée."""

    obstacle_ratio: float
    """Ratio de pixels obstacle sur la ligne (0.0-1.0)."""

    sample_count: int
    """Nombre de pixels échantillonnés."""

    obstacle_samples: int
    """Nombre de pixels correspondant à un obstacle."""

    reason: str = ""
    """Description textuelle du résultat."""


def bresenham_line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Bresenham's line algorithm — retourne la liste de pixels (x, y) sur la
    ligne entre (x0, y0) et (x1, y1), exclus ces deux bornes.

    Pourquoi exclure les bornes ? Le pixel source = centre du perso (son sprite),
    qui lui-même est visuellement "opaque" donc false-positive. Idem pour la
    cible (sprite du mob).

    Les bornes sont incluses dans notre cas d'usage (raycasting), donc on
    garde tous les points intermédiaires SAUF le dernier tiers proche de la
    cible (le sprite du mob masquerait la LoS).

    Returns:
        Liste de tuples (x, y) ordonnée de source → cible.
    """
    points: list[tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    x, y = x0, y0
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1

    if dx > dy:
        err = dx / 2
        while x != x1:
            points.append((x, y))
            err -= dy
            if err < 0:
                y += sy
                err += dx
            x += sx
    else:
        err = dy / 2
        while y != y1:
            points.append((x, y))
            err -= dx
            if err < 0:
                x += sx
                err += dy
            y += sy
    points.append((x, y))
    return points


def _pixel_is_obstacle(hsv_pixel: np.ndarray) -> str | None:
    """Retourne le label de l'obstacle si ce pixel HSV correspond, sinon None."""
    h, s, v = int(hsv_pixel[0]), int(hsv_pixel[1]), int(hsv_pixel[2])
    for rng in OBSTACLE_HSV_RANGES:
        if (rng.h_min <= h <= rng.h_max
                and rng.s_min <= s <= rng.s_max
                and rng.v_min <= v <= rng.v_max):
            return rng.label
    return None


def check_line_of_sight(
    frame_bgr: np.ndarray,
    from_xy: tuple[int, int],
    to_xy: tuple[int, int],
    *,
    sampling_step: int = 4,
    obstacle_threshold_ratio: float = 0.12,
    exclude_end_fraction: float = 0.15,
) -> LoSResult:
    """Vérifie si la LoS entre deux points pixels est libre.

    Args:
        frame_bgr: Image BGR (capture écran Dofus).
        from_xy: Pixel source (perso).
        to_xy: Pixel cible (mob).
        sampling_step: Échantillonne 1 pixel tous les N (1 = max précision,
            4 = rapide). Un mur fait typiquement 60-100px d'épaisseur, donc
            step=4 suffit largement.
        obstacle_threshold_ratio: Proportion minimale de pixels "obstacle"
            pour considérer la LoS bloquée. 0.12 = 12% des samples (robuste
            aux bruits de pixels isolés mais catch les vrais murs).
        exclude_end_fraction: Ignore le premier X% et le dernier X% de la
            ligne (sprites du perso et du mob masquent la LoS sinon).

    Returns:
        LoSResult avec le verdict.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return LoSResult(
            is_clear=True, obstacle_ratio=0.0, sample_count=0,
            obstacle_samples=0, reason="frame vide",
        )

    h, w = frame_bgr.shape[:2]
    # Clamp coords à l'image
    x0 = max(0, min(from_xy[0], w - 1))
    y0 = max(0, min(from_xy[1], h - 1))
    x1 = max(0, min(to_xy[0], w - 1))
    y1 = max(0, min(to_xy[1], h - 1))

    line = bresenham_line(x0, y0, x1, y1)
    if len(line) < 10:
        return LoSResult(
            is_clear=True, obstacle_ratio=0.0, sample_count=0,
            obstacle_samples=0, reason="ligne trop courte",
        )

    # Exclure les bouts (sprites)
    cut_start = int(len(line) * exclude_end_fraction)
    cut_end = len(line) - int(len(line) * exclude_end_fraction)
    interior = line[cut_start:cut_end]

    # Échantillonne un pixel tous les `sampling_step`
    samples = interior[::max(1, sampling_step)]
    if not samples:
        return LoSResult(
            is_clear=True, obstacle_ratio=0.0, sample_count=0,
            obstacle_samples=0, reason="aucun sample",
        )

    # Convertit en HSV batch pour perfs
    coords = np.array(samples)  # (N, 2)
    pixels_bgr = frame_bgr[coords[:, 1], coords[:, 0]]  # (N, 3)
    pixels_hsv = cv2.cvtColor(
        pixels_bgr.reshape(1, -1, 3).astype(np.uint8),
        cv2.COLOR_BGR2HSV,
    ).reshape(-1, 3)

    obstacle_count = 0
    for px_hsv in pixels_hsv:
        if _pixel_is_obstacle(px_hsv):
            obstacle_count += 1

    ratio = obstacle_count / len(samples) if samples else 0.0
    is_clear = ratio < obstacle_threshold_ratio
    reason = (
        f"LoS {'libre' if is_clear else 'BLOQUÉE'} : "
        f"{obstacle_count}/{len(samples)} pixels obstacle ({ratio:.1%})"
    )
    logger.debug("LoS {} → {} : {}", from_xy, to_xy, reason)
    return LoSResult(
        is_clear=is_clear,
        obstacle_ratio=ratio,
        sample_count=len(samples),
        obstacle_samples=obstacle_count,
        reason=reason,
    )


def find_bypass_cell(
    frame_bgr: np.ndarray,
    perso_xy: tuple[int, int],
    target_xy: tuple[int, int],
    *,
    max_attempts: int = 8,
    step_cases: int = 2,
) -> tuple[int, int] | None:
    """Cherche une case voisine du perso qui débloque la LoS vers la cible.

    Essaie plusieurs directions autour du perso (8 points cardinaux + interm)
    à une distance de `step_cases` cases. Retourne le premier qui dégage la LoS.

    Args:
        frame_bgr: Capture écran.
        perso_xy: Position actuelle du perso.
        target_xy: Position du mob à atteindre.
        max_attempts: Nombre de directions à tester.
        step_cases: Nombre de cases à bouger dans la direction.

    Returns:
        (x, y) de la case qui débloque, ou None si rien trouvé.
    """
    import math
    cell_w = 86 * step_cases
    cell_h = 43 * step_cases
    angles = [i * (2 * math.pi / max_attempts) for i in range(max_attempts)]
    # Priorise les directions perpendiculaires à l'axe perso→cible
    dx = target_xy[0] - perso_xy[0]
    dy = target_xy[1] - perso_xy[1]
    base_angle = math.atan2(dy, dx)
    # Commence par les perpendiculaires (+/- 90°)
    sorted_angles = sorted(
        angles,
        key=lambda a: abs(abs((a - base_angle) % (2 * math.pi) - math.pi) - math.pi / 2),
    )
    for ang in sorted_angles:
        cx = int(perso_xy[0] + math.cos(ang) * cell_w)
        cy = int(perso_xy[1] + math.sin(ang) * cell_h)
        # Ne pas proposer la case actuelle ni trop loin des bords
        if abs(cx - perso_xy[0]) + abs(cy - perso_xy[1]) < 40:
            continue
        los = check_line_of_sight(frame_bgr, (cx, cy), target_xy)
        if los.is_clear:
            return (cx, cy)
    return None
