"""Visualisation debug des détections / décisions du bot.

Sauvegarde pour chaque tick une image annotée dans `data/vision_debug/`
avec :
  - Rectangle rouge autour du perso
  - Rectangles bleus autour des mobs + label "MOB{n}: HP=X% dist=Yc"
  - Cases de PM détectées (petit cercle vert)
  - Ligne de LoS tracée (vert = OK, rouge = bloquée)
  - Flèche de direction pour le mouvement choisi
  - Bande texte en haut : action + raison (ex "cast_spell slot2 sur MOB1")

Utile pour :
  - Comprendre pourquoi le bot prend une décision
  - Calibrer les HSV (voir si les cases vertes sont bien détectées)
  - Diagnostiquer les faux positifs LoS (murs fantômes)

Activé via config `save_debug_images=True` dans VisionCombatConfig.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from loguru import logger


DEBUG_DIR = Path("data/vision_debug")


@dataclass
class DebugSnapshot:
    """Tout ce qu'il faut pour produire une image debug."""
    frame_bgr: np.ndarray | None = None
    perso_xy: tuple[int, int] | None = None
    enemies: list[dict] = field(default_factory=list)
    """Liste de {x, y, hp_pct, label}."""
    pm_cells: list[tuple[int, int]] = field(default_factory=list)
    """Centres des cases PM détectées."""
    chosen_target_xy: tuple[int, int] | None = None
    """Position de la cible choisie."""
    los_trace: list[tuple[int, int]] | None = None
    """Ligne LoS tracée (liste de pixels)."""
    los_blocked: bool = False
    movement_target_xy: tuple[int, int] | None = None
    """Case où le bot va cliquer pour bouger."""
    action_type: str = ""
    """cast_spell, click_xy, end_turn..."""
    action_reason: str = ""
    """Texte explicatif."""
    turn_number: int = 0
    pa_remaining: int = 0
    phase: str = ""


# Couleurs BGR pour les annotations
COLOR_PERSO = (0, 0, 255)        # rouge
COLOR_ENEMY = (255, 80, 0)       # bleu
COLOR_TARGET = (0, 255, 255)     # jaune (cible choisie)
COLOR_PM_CELL = (0, 200, 0)      # vert
COLOR_LOS_OK = (0, 255, 0)       # vert clair
COLOR_LOS_BAD = (0, 0, 255)      # rouge
COLOR_MOVE = (255, 255, 0)       # cyan (mouvement)
COLOR_TEXT_BG = (40, 40, 40)     # fond noir
COLOR_TEXT = (255, 255, 255)     # blanc


def annotate_frame(snap: DebugSnapshot) -> np.ndarray | None:
    """Retourne une copie de la frame avec annotations. None si frame vide."""
    if snap.frame_bgr is None or snap.frame_bgr.size == 0:
        return None

    out = snap.frame_bgr.copy()
    h, w = out.shape[:2]

    # --- Cases de PM (petits cercles verts) ---
    for (cx, cy) in snap.pm_cells:
        cv2.circle(out, (cx, cy), 8, COLOR_PM_CELL, 2, cv2.LINE_AA)

    # --- Perso (gros carré rouge avec label) ---
    if snap.perso_xy:
        px, py = snap.perso_xy
        cv2.rectangle(
            out, (px - 50, py - 50), (px + 50, py + 50),
            COLOR_PERSO, 3,
        )
        label = f"PERSO PA={snap.pa_remaining} tour{snap.turn_number}"
        cv2.putText(
            out, label, (px - 70, py - 60),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_PERSO, 2, cv2.LINE_AA,
        )

    # --- Ennemis (rectangles bleus + label HP + distance) ---
    for i, e in enumerate(snap.enemies, 1):
        ex = int(e.get("x", 0))
        ey = int(e.get("y", 0))
        hp_pct = e.get("hp_pct")
        color = COLOR_TARGET if (
            snap.chosen_target_xy
            and abs(ex - snap.chosen_target_xy[0]) < 20
            and abs(ey - snap.chosen_target_xy[1]) < 20
        ) else COLOR_ENEMY
        thickness = 4 if color == COLOR_TARGET else 3
        cv2.rectangle(
            out, (ex - 60, ey - 60), (ex + 60, ey + 60),
            color, thickness,
        )
        label = f"MOB{i}"
        if hp_pct is not None:
            label += f" {int(hp_pct)}%HP"
        cv2.putText(
            out, label, (ex - 80, ey - 70),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA,
        )

    # --- Ligne de LoS ---
    if snap.perso_xy and snap.los_trace:
        los_color = COLOR_LOS_BAD if snap.los_blocked else COLOR_LOS_OK
        for pt in snap.los_trace:
            cv2.circle(out, pt, 1, los_color, -1)

    # --- Flèche de mouvement ---
    if snap.perso_xy and snap.movement_target_xy:
        cv2.arrowedLine(
            out, snap.perso_xy, snap.movement_target_xy,
            COLOR_MOVE, 3, cv2.LINE_AA, tipLength=0.05,
        )

    # --- Bande texte en haut (action + raison) ---
    if snap.action_type or snap.phase:
        banner_h = 60
        cv2.rectangle(out, (0, 0), (w, banner_h), COLOR_TEXT_BG, -1)
        line1 = f"[{snap.phase or '?'}] {snap.action_type}"
        cv2.putText(
            out, line1, (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_TEXT, 2, cv2.LINE_AA,
        )
        if snap.action_reason:
            cv2.putText(
                out, snap.action_reason[:120], (10, 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT, 1, cv2.LINE_AA,
            )

    return out


def save_debug_image(snap: DebugSnapshot, directory: Path | str = DEBUG_DIR) -> Path | None:
    """Sauve une image annotée dans `directory` avec timestamp. Retourne le chemin."""
    out = annotate_frame(snap)
    if out is None:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S_%f")[:-3]
    filename = f"tick_{ts}_{snap.action_type or 'noop'}.jpg"
    path = directory / filename
    try:
        cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return path
    except Exception as exc:
        logger.debug("save_debug_image échec : {}", exc)
        return None


def cleanup_old_debug_images(
    directory: Path | str = DEBUG_DIR,
    keep_last: int = 500,
) -> int:
    """Supprime les vieilles images debug (garde les `keep_last` plus récentes).
    Retourne le nombre supprimé.
    """
    directory = Path(directory)
    if not directory.exists():
        return 0
    files = sorted(directory.glob("tick_*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = files[keep_last:]
    count = 0
    for f in to_delete:
        try:
            f.unlink()
            count += 1
        except Exception:
            pass
    return count
