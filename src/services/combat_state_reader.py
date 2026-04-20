"""Lecture de l'état d'un combat Dofus via vision (HSV + OCR).

Extrait à chaque tour :
  - Position du perso (cercle rouge sous le perso)
  - Positions des ennemis (cercles bleus)
  - PA/PM restants (OCR, zone HUD bas)
  - HP perso (OCR, barre rouge centre-bas)

Les positions sont en coordonnées écran ABSOLUES (pas dans la grille Dofus).
Pour une grille logique, il faudrait la calibrer par case — ici on reste en pixels
écran, ce qui suffit pour cliquer sur une cible et estimer les distances relatives.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from src.models.detection import Region
from src.services.vision import MssVisionService


@dataclass
class EntityDetection:
    """Une entité détectée (perso ou ennemi)."""
    x: int              # centre X en pixels écran
    y: int              # centre Y en pixels écran
    radius: int = 30    # rayon approximatif du cercle
    team: str = "enemy" # "self" | "ally" | "enemy"
    hp_pct: int | None = None

    @property
    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)


@dataclass
class CombatStateSnapshot:
    """État visuel complet d'un combat."""
    pa_restants: int | None = None
    pm_restants: int | None = None
    hp_perso: int | None = None
    hp_perso_max: int | None = None
    perso: EntityDetection | None = None
    ennemis: list[EntityDetection] = field(default_factory=list)
    allies: list[EntityDetection] = field(default_factory=list)
    raw_frame_shape: tuple[int, int] = (0, 0)
    # True si on a détecté une zone de portée de sort (trop de cercles bleus)
    suspected_spell_overlay: bool = False

    @property
    def hp_pct(self) -> float | None:
        if self.hp_perso is None or not self.hp_perso_max:
            return None
        return 100.0 * self.hp_perso / max(self.hp_perso_max, 1)

    @property
    def distance_ennemi_proche(self) -> int | None:
        if not self.perso or not self.ennemis:
            return None
        px, py = self.perso.x, self.perso.y
        dists = [int(np.hypot(e.x - px, e.y - py)) for e in self.ennemis]
        return min(dists) if dists else None

    def enemy_nearest(self) -> EntityDetection | None:
        if not self.perso or not self.ennemis:
            return self.ennemis[0] if self.ennemis else None
        px, py = self.perso.x, self.perso.y
        return min(self.ennemis, key=lambda e: (e.x - px) ** 2 + (e.y - py) ** 2)

    def enemy_weakest(self) -> EntityDetection | None:
        if not self.ennemis:
            return None
        with_hp = [e for e in self.ennemis if e.hp_pct is not None]
        if with_hp:
            return min(with_hp, key=lambda e: e.hp_pct)
        return self.enemy_nearest()


class CombatStateReader:
    """Lit l'état du combat depuis une frame.

    Zones configurables (ratio x, y, w, h de la frame) :
      - PA/PM : barre HUD en bas au milieu
      - HP perso : barre centrale rouge
      - Combat : toute la frame sauf le HUD bas
    """

    # Ratios zones HUD (bas du screen)
    PA_REGION_RATIO = (0.45, 0.91, 0.04, 0.07)    # petit carré bleu avec chiffre
    PM_REGION_RATIO = (0.50, 0.91, 0.04, 0.07)    # petit carré vert avec chiffre
    HP_REGION_RATIO = (0.35, 0.88, 0.13, 0.05)    # barre rouge HP
    COMBAT_ZONE_RATIO = (0.06, 0.04, 0.86, 0.80)  # plateau de combat (hors bords UI)

    # Zones UI à exclure de la détection entités (évite faux positifs sur les portraits/HUD)
    # Chaque zone = (x_ratio, y_ratio, w_ratio, h_ratio) en coords de la frame complète
    UI_EXCLUSION_ZONES = [
        (0.00, 0.00, 0.10, 0.65),   # menu gauche (icônes combat + chat portraits stream)
        (0.83, 0.00, 0.17, 0.20),   # portraits combat haut-droite (timeline + icônes)
        (0.62, 0.75, 0.38, 0.25),   # HUD + portraits groupe bas-droite (large)
        (0.00, 0.80, 0.62, 0.20),   # chat + HUD bas-gauche
    ]

    # Détecter les alliés ? Désactivé par défaut : en solo on n'en a pas, et les
    # cases vertes de placement en phase début-combat génèrent des centaines de faux
    # positifs via le pattern damier.
    DETECT_ALLIES = False

    # Si on détecte plus d'ennemis que ce seuil, c'est impossible dans un combat
    # normal (max ~6-8 mobs). C'est une ZONE DE PORTÉE (bleue) affichée par le jeu
    # quand un sort est sélectionné. On invalide toutes les détections ennemies
    # pour signaler "état incohérent".
    MAX_PLAUSIBLE_ENEMIES = 12

    # HSV ranges cercles sous les entités
    # Plus permissifs pour tenir compte de la variance des shaders serveur privé
    # Cercle rouge (perso) : hue 0-12 ou 165-180
    RED_HSV_LO_1 = np.array([0, 100, 80])
    RED_HSV_HI_1 = np.array([12, 255, 255])
    RED_HSV_LO_2 = np.array([165, 100, 80])
    RED_HSV_HI_2 = np.array([180, 255, 255])

    # Cercle bleu (ennemi) : hue 95-135 (plus large)
    BLUE_HSV_LO = np.array([95, 80, 80])
    BLUE_HSV_HI = np.array([135, 255, 255])

    # Cercle vert (allié) : hue 35-85
    GREEN_HSV_LO = np.array([35, 80, 80])
    GREEN_HSV_HI = np.array([85, 255, 255])

    # Taille des cercles attendus (anneaux fins vus en isométrique)
    # Anneau fin = peu de pixels colorés, on descend le seuil
    MIN_CIRCLE_AREA = 80      # filtre bruit (anneau fin = 100-500 px colorés)
    MAX_CIRCLE_AREA = 15000   # filtre faux positifs
    MIN_CIRCULARITY = 0.25    # anneau isométrique = ellipse très aplatie
    DILATE_KERNEL_SIZE = 7    # remplit l'anneau avant détection contour

    def __init__(self, vision: MssVisionService) -> None:
        self._vision = vision

    # ---------- Lecture principale ----------

    def read(self) -> CombatStateSnapshot:
        snap = CombatStateSnapshot()
        try:
            frame = self._vision.capture()
        except Exception as exc:
            logger.debug("CombatStateReader capture échouée : {}", exc)
            return snap

        h, w = frame.shape[:2]
        snap.raw_frame_shape = (h, w)

        # 1. Entités (cercles colorés) dans la zone de combat
        cx, cy, cw, ch = self.COMBAT_ZONE_RATIO
        zone_x0 = int(w * cx)
        zone_y0 = int(h * cy)
        combat_zone = frame[zone_y0:int(h * (cy + ch)),
                            zone_x0:int(w * (cx + cw))]
        raw_entities = self._detect_entities(
            combat_zone, offset_x=zone_x0, offset_y=zone_y0,
        )
        # Filtre par zones d'exclusion UI (coords écran)
        entities = [e for e in raw_entities if not self._in_ui_exclusion(e, w, h)]

        for e in entities:
            if e.team == "self":
                # Prend le plus gros cercle rouge trouvé (éviter les artefacts)
                if snap.perso is None or e.radius > snap.perso.radius:
                    snap.perso = e
            elif e.team == "enemy":
                snap.ennemis.append(e)
            elif e.team == "ally":
                snap.allies.append(e)

        # Garde-fou : si trop d'ennemis détectés, c'est une zone de portée affichée
        # (sort sélectionné) → on vide la liste + on flag pour que le runner presse Escape
        if len(snap.ennemis) > self.MAX_PLAUSIBLE_ENEMIES:
            logger.warning(
                "{} ennemis détectés > {} : zone de portée sort probable → Escape conseillé",
                len(snap.ennemis), self.MAX_PLAUSIBLE_ENEMIES,
            )
            snap.ennemis = []
            snap.suspected_spell_overlay = True

        # 2. PA / PM / HP via OCR (optionnel, peut échouer sans planter)
        snap.pa_restants = self._ocr_int(frame, self.PA_REGION_RATIO, allow_digits_only=True)
        snap.pm_restants = self._ocr_int(frame, self.PM_REGION_RATIO, allow_digits_only=True)
        hp_cur, hp_max = self._ocr_hp(frame)
        snap.hp_perso = hp_cur
        snap.hp_perso_max = hp_max

        return snap

    # ---------- Détection entités (HSV) ----------

    def _in_ui_exclusion(self, e: EntityDetection, frame_w: int, frame_h: int) -> bool:
        """True si l'entité tombe dans une zone UI exclue."""
        for zx, zy, zw, zh in self.UI_EXCLUSION_ZONES:
            x0 = int(frame_w * zx)
            y0 = int(frame_h * zy)
            x1 = x0 + int(frame_w * zw)
            y1 = y0 + int(frame_h * zh)
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                return True
        return False

    def _detect_entities(
        self,
        zone: np.ndarray,
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> list[EntityDetection]:
        if zone.size == 0:
            return []
        try:
            hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
        except Exception:
            return []

        entities: list[EntityDetection] = []

        mask_red = cv2.inRange(hsv, self.RED_HSV_LO_1, self.RED_HSV_HI_1)
        mask_red |= cv2.inRange(hsv, self.RED_HSV_LO_2, self.RED_HSV_HI_2)
        entities += self._contours_to_entities(
            mask_red, team="self", offset_x=offset_x, offset_y=offset_y,
        )

        mask_blue = cv2.inRange(hsv, self.BLUE_HSV_LO, self.BLUE_HSV_HI)
        entities += self._contours_to_entities(
            mask_blue, team="enemy", offset_x=offset_x, offset_y=offset_y,
        )

        if self.DETECT_ALLIES:
            mask_green = cv2.inRange(hsv, self.GREEN_HSV_LO, self.GREEN_HSV_HI)
            entities += self._contours_to_entities(
                mask_green, team="ally", offset_x=offset_x, offset_y=offset_y,
            )

        return entities

    def _contours_to_entities(
        self,
        mask: np.ndarray,
        team: str,
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> list[EntityDetection]:
        """Détecte les anneaux colorés sous les entités.

        Approche : contours directs du mask (pas de dilation, on veut l'anneau fin)
        + `fitEllipse` pour trouver le centre exact de chaque anneau isométrique.
        Le centre de l'ellipse = position précise de la case du mob en Dofus.
        """
        out: list[EntityDetection] = []

        # Légère fermeture morpho pour connecter les pixels de l'anneau
        # (l'anneau est fin et peut être brisé par le sprite au centre)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        expected_width_px = 55  # largeur typique d'un anneau mob en isométrique

        for c in contours:
            area = cv2.contourArea(c)
            if area < self.MIN_CIRCLE_AREA or area > self.MAX_CIRCLE_AREA:
                continue

            # Bounding box pour filtres de forme
            x, y, bw, bh = cv2.boundingRect(c)
            if bw < 18 or bh < 10:
                continue
            aspect = max(bw, bh) / max(min(bw, bh), 1.0)
            if aspect > 3.5:   # trop allongé = pas un anneau
                continue

            # Centre exact via fitEllipse si assez de points, sinon moments
            center_x, center_y = None, None
            ellipse_w, ellipse_h = bw, bh
            if len(c) >= 5:
                try:
                    (ex, ey), (ea, eb), _ang = cv2.fitEllipse(c)
                    center_x, center_y = ex, ey
                    ellipse_w, ellipse_h = ea, eb
                except cv2.error:
                    pass

            if center_x is None:
                M = cv2.moments(c)
                if M["m00"] == 0:
                    continue
                center_x = M["m10"] / M["m00"]
                center_y = M["m01"] / M["m00"]

            # Si le blob est anormalement large, il contient plusieurs mobs → split
            if bw > expected_width_px * 1.7:
                n_splits = max(2, int(round(bw / expected_width_px)))
                for i in range(n_splits):
                    sub_x = x + (i + 0.5) * (bw / n_splits)
                    out.append(EntityDetection(
                        x=int(sub_x) + offset_x,
                        y=int(center_y) + offset_y,
                        radius=int(max(bw / n_splits, bh) / 2),
                        team=team,
                    ))
            else:
                radius = int(max(ellipse_w, ellipse_h) / 2)
                out.append(EntityDetection(
                    x=int(center_x) + offset_x,
                    y=int(center_y) + offset_y,
                    radius=radius,
                    team=team,
                ))
        return out

    # ---------- OCR helpers ----------

    def _ocr_int(
        self,
        frame: np.ndarray,
        region_ratio: tuple[float, float, float, float],
        allow_digits_only: bool = False,
    ) -> int | None:
        h, w = frame.shape[:2]
        x, y, rw, rh = region_ratio
        region = Region(x=int(w * x), y=int(h * y), w=int(w * rw), h=int(h * rh))
        try:
            text = self._vision.read_text(frame, region=region)
        except Exception:
            return None
        if not text:
            return None
        digits = re.findall(r"\d+", text.replace("O", "0").replace("o", "0"))
        if not digits:
            return None
        try:
            return int(digits[0])
        except ValueError:
            return None

    def debug_dump(self, output_dir: str | Path | None = None) -> Path | None:
        """Capture + sauvegarde image annotée avec détections pour calibration.

        Retourne le chemin de l'image sauvegardée, ou None si échec.
        """
        if output_dir is None:
            here = Path(__file__).resolve().parent.parent.parent
            output_dir = here / "data" / "ocr_debug"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            frame = self._vision.capture()
        except Exception as exc:
            logger.warning("Debug capture échouée : {}", exc)
            return None

        h, w = frame.shape[:2]
        overlay = frame.copy()

        # Zone de combat
        cx, cy, cw, ch = self.COMBAT_ZONE_RATIO
        zone_x0 = int(w * cx)
        zone_y0 = int(h * cy)
        combat_zone = frame[zone_y0:int(h * (cy + ch)),
                            zone_x0:int(w * (cx + cw))]

        # Masks HSV pour chaque couleur
        hsv = cv2.cvtColor(combat_zone, cv2.COLOR_BGR2HSV)
        mask_red = cv2.inRange(hsv, self.RED_HSV_LO_1, self.RED_HSV_HI_1)
        mask_red |= cv2.inRange(hsv, self.RED_HSV_LO_2, self.RED_HSV_HI_2)
        mask_blue = cv2.inRange(hsv, self.BLUE_HSV_LO, self.BLUE_HSV_HI)
        mask_green = cv2.inRange(hsv, self.GREEN_HSV_LO, self.GREEN_HSV_HI)

        # Détections (avec filtre zones UI)
        raw_entities = self._detect_entities(
            combat_zone, offset_x=zone_x0, offset_y=zone_y0,
        )
        entities = [e for e in raw_entities if not self._in_ui_exclusion(e, w, h)]
        excluded = [e for e in raw_entities if self._in_ui_exclusion(e, w, h)]

        # Dessine les zones d'exclusion UI en gris translucide
        for zx, zy, zw, zh in self.UI_EXCLUSION_ZONES:
            x0 = int(w * zx)
            y0 = int(h * zy)
            x1 = x0 + int(w * zw)
            y1 = y0 + int(h * zh)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), (80, 80, 80), 2)
            cv2.putText(
                overlay, "UI EXCLU", (x0 + 5, y0 + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1,
            )

        # Dessine les exclus en gris
        for e in excluded:
            cv2.circle(overlay, (e.x, e.y), e.radius + 5, (100, 100, 100), 1)

        # Annoter l'overlay
        for e in entities:
            if e.team == "self":
                color = (0, 0, 255)  # rouge BGR
                label = "PERSO"
            elif e.team == "enemy":
                color = (255, 100, 0)  # bleu BGR
                label = "ENEMY"
            else:
                color = (0, 255, 0)  # vert
                label = "ALLY"
            cv2.circle(overlay, (e.x, e.y), e.radius + 5, color, 3)
            cv2.putText(
                overlay, f"{label} r={e.radius}",
                (e.x - 40, e.y - e.radius - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2,
            )

        # Stats en haut
        summary = (
            f"Red={sum(1 for e in entities if e.team=='self')} "
            f"Blue={sum(1 for e in entities if e.team=='enemy')} "
            f"Green={sum(1 for e in entities if e.team=='ally')} "
            f"MinArea={self.MIN_CIRCLE_AREA} MinCirc={self.MIN_CIRCULARITY}"
        )
        cv2.putText(
            overlay, summary, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2,
        )

        # Compose une image 2x2 : original, overlay, mask bleu, mask rouge
        def resize_for_grid(img: np.ndarray) -> np.ndarray:
            H = 540
            scale = H / img.shape[0]
            W = int(img.shape[1] * scale)
            return cv2.resize(img, (W, H))

        try:
            top_left = resize_for_grid(frame)
            top_right = resize_for_grid(overlay)
            # Masks en couleur pour visualiser
            mask_blue_rgb = cv2.cvtColor(mask_blue, cv2.COLOR_GRAY2BGR)
            mask_blue_rgb[mask_blue > 0] = [255, 100, 0]
            mask_red_rgb = cv2.cvtColor(mask_red, cv2.COLOR_GRAY2BGR)
            mask_red_rgb[mask_red > 0] = [0, 0, 255]
            bot_left = resize_for_grid(mask_blue_rgb)
            bot_right = resize_for_grid(mask_red_rgb)
            grid = np.vstack([
                np.hstack([top_left, top_right]),
                np.hstack([bot_left, bot_right]),
            ])
        except Exception:
            grid = overlay

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"combat_detection_{ts}.png"
        cv2.imwrite(str(path), grid)
        logger.info("Debug dump sauvegardé : {}", path)
        return path

    def _ocr_hp(self, frame: np.ndarray) -> tuple[int | None, int | None]:
        """Lit la barre HP centrale et tente de parser 'current/max'."""
        h, w = frame.shape[:2]
        x, y, rw, rh = self.HP_REGION_RATIO
        region = Region(x=int(w * x), y=int(h * y), w=int(w * rw), h=int(h * rh))
        try:
            text = self._vision.read_text(frame, region=region)
        except Exception:
            return (None, None)
        if not text:
            return (None, None)
        # Format typique "6296/6296" ou "6 296 / 6 296"
        clean = text.replace(" ", "").replace("O", "0")
        m = re.search(r"(\d+)\s*/\s*(\d+)", clean)
        if m:
            try:
                return (int(m.group(1)), int(m.group(2)))
            except ValueError:
                return (None, None)
        return (None, None)
