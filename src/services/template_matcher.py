"""Template matching multi-templates pour la détection de ressources.

Chaque `resource_id` peut avoir **plusieurs templates** :
    data/templates/
        ble.png         → template n°1
        ble_2.png       → template n°2 (autre angle/éclairage)
        ble_3.png       → ...

La méthode `find()` teste tous les templates et fusionne les matches
via non-maximum suppression.

Flow utilisateur :
    1. Clique "Capturer template" → choisit `ble`
    2. Hover une ressource en jeu → bot sauvegarde `ble.png`
    3. Re-clique "Capturer template" → `ble_2.png` auto-incrémenté
    4. Répéter 2-5 fois sur des exemplaires variés → robuste
"""
from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import mss
import numpy as np
from loguru import logger

from src.models.detection import DetectedObject, Region


_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?:_(?P<n>\d+))?$")


@dataclass
class TemplateVariant:
    path: Path
    image: np.ndarray
    width: int
    height: int
    # Profil couleur HSV moyen du template (calculé au chargement pour post-filtrage).
    # Permet de rejeter les matches qui ont la bonne FORME mais pas la bonne COULEUR
    # (ex: template = blé jaune, match sur herbe verte qui aurait la même silhouette).
    mean_h: float = 0.0
    mean_s: float = 0.0
    mean_v: float = 0.0


@dataclass
class TemplateBundle:
    """Un resource_id peut avoir plusieurs templates (angles différents)."""
    resource_id: str
    variants: list[TemplateVariant] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.variants)


class TemplateMatcher:
    """Gère la capture et l'application de templates multi-variants.

    ✨ Pour gagner du temps : à partir d'UN seul template capturé, on génère
    automatiquement des variantes (plusieurs échelles + rotations ±10°) au
    chargement. Ça permet de matcher des ressources de tailles/angles
    différents sans capturer chaque exemplaire.
    """

    DEFAULT_MATCH_THRESHOLD = 0.62  # TM_CCOEFF_NORMED : strict pour éviter les faux positifs (herbe, terre)

    # Variantes auto-générées : 5 échelles × 3 rotations = 15 variantes (moins de bruit)
    AUTO_SCALES = (0.85, 0.95, 1.00, 1.05, 1.15)
    AUTO_ROTATIONS_DEG = (-8, 0, 8)

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._dir = templates_dir or Path("data/templates")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._bundles: dict[str, TemplateBundle] = {}
        self._load_all()

    # ---------- Capture ----------

    def capture_template_around_cursor(
        self,
        resource_id: str,
        size_px: int = 50,
    ) -> TemplateVariant | None:
        """Capture un crop centré sur le curseur et l'ajoute aux templates du resource_id.

        Si `ble.png` existe déjà → sauve en `ble_2.png`, puis `ble_3.png`, etc.
        """
        cx, cy = self._get_cursor_pos()
        if cx is None:
            return None
        half = size_px // 2
        try:
            with mss.mss() as sct:
                raw = sct.grab({
                    "left": cx - half, "top": cy - half,
                    "width": size_px, "height": size_px,
                })
                bgra = np.array(raw)
                bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        except Exception as exc:
            logger.warning("Capture template échouée : {}", exc)
            return None

        # Détermine le prochain nom disponible
        dst = self._next_path(resource_id)
        try:
            cv2.imwrite(str(dst), bgr)
        except Exception as exc:
            logger.warning("Écriture template échouée : {}", exc)
            return None

        variant = TemplateVariant(path=dst, image=bgr, width=size_px, height=size_px)
        bundle = self._bundles.setdefault(resource_id, TemplateBundle(resource_id=resource_id))
        bundle.variants.append(variant)
        # Génère aussi les variantes auto (scales × rotations) pour ce nouveau template
        bundle.variants.extend(self._generate_variants(variant))
        logger.info(
            "Template '{}' sauvegardé ({}×{} px) + {} variantes auto → {}",
            resource_id, size_px, size_px, len(bundle.variants) - 1, dst.name,
        )
        return variant

    # ---------- Matching ----------

    def find(
        self,
        frame: np.ndarray,
        resource_id: str,
        threshold: float | None = None,
    ) -> list[DetectedObject]:
        """Retourne toutes les occurrences du resource_id dans la frame.

        Teste CHAQUE variant (template) et fusionne via NMS.
        """
        bundle = self._bundles.get(resource_id)
        if bundle is None or not bundle.variants:
            return []
        thr = threshold if threshold is not None else self.DEFAULT_MATCH_THRESHOLD

        all_points: list[tuple[int, int, float, int, int]] = []  # (x, y, score, w, h)
        # Pré-calcule la version gray de la frame pour matching invariant à l'éclairage
        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        for variant in bundle.variants:
            # Match sur BGR (couleur)
            try:
                result_bgr = cv2.matchTemplate(frame, variant.image, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where(result_bgr >= thr)
                for x, y in zip(xs, ys):
                    all_points.append((int(x), int(y), float(result_bgr[y, x]), variant.width, variant.height))
            except Exception as exc:
                logger.debug("matchTemplate BGR échec : {}", exc)

            # Match sur gray : seuil PLUS ÉLEVÉ que BGR car moins discriminant
            # (l'herbe jaune et le blé ont la même luminosité). On l'utilise
            # uniquement comme confirmation, pas comme source primaire.
            try:
                var_gray = cv2.cvtColor(variant.image, cv2.COLOR_BGR2GRAY)
                result_gray = cv2.matchTemplate(frame_gray, var_gray, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where(result_gray >= thr + 0.10)
                for x, y in zip(xs, ys):
                    all_points.append((int(x), int(y), float(result_gray[y, x]) * 0.90, variant.width, variant.height))
            except Exception:
                pass

        if not all_points:
            return []

        # Tri par score décroissant
        all_points.sort(key=lambda p: p[2], reverse=True)

        # Post-filtre COULEUR : rejette les matches dont la teinte moyenne est trop
        # éloignée du template d'origine. Évite de cliquer sur herbe verte / terre.
        frame_hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ref_h, ref_s, ref_v = self._reference_color(bundle)
        filtered: list[tuple[int, int, float, int, int]] = []
        for x, y, score, w, h in all_points:
            patch = frame_hsv[y : y + h, x : x + w]
            if patch.size == 0:
                continue
            # Utilise la médiane pour robustesse aux reflets/ombres
            mh = float(np.median(patch[:, :, 0]))
            ms = float(np.median(patch[:, :, 1]))
            mv = float(np.median(patch[:, :, 2]))
            # Distance hue circulaire (0-180 boucle)
            dh = min(abs(mh - ref_h), 180 - abs(mh - ref_h))
            ds = abs(ms - ref_s)
            dv = abs(mv - ref_v)
            # Seuils : hue ±18°, sat ±60, val ±70 — large mais élimine vraiment le vert/marron
            if dh > 18 or ds > 60 or dv > 70:
                continue
            filtered.append((x, y, score, w, h))

        if not filtered:
            return []

        # Non-maximum suppression (distance < demi-template)
        min_dim = min(v.width for v in bundle.variants) // 2
        min_dist_sq = min_dim ** 2

        kept: list[tuple[int, int, float, int, int]] = []
        for x, y, score, w, h in filtered:
            if any((x - kx) ** 2 + (y - ky) ** 2 < min_dist_sq for kx, ky, _, _, _ in kept):
                continue
            kept.append((x, y, score, w, h))

        return [
            DetectedObject(
                label=resource_id,
                box=Region(x=x, y=y, w=w, h=h),
                confidence=min(1.0, score),
            )
            for x, y, score, w, h in kept
        ]

    def _reference_color(self, bundle: TemplateBundle) -> tuple[float, float, float]:
        """Couleur HSV médiane de référence = moyenne des templates originaux (non pivotés).

        Cache le résultat sur le bundle pour éviter de recalculer.
        """
        cached = getattr(bundle, "_ref_color", None)
        if cached is not None:
            return cached
        # Prend les variantes originales (celles qui ont un path distinct)
        seen_paths = set()
        hs, ss, vs = [], [], []
        for v in bundle.variants:
            if v.path in seen_paths:
                continue
            seen_paths.add(v.path)
            hsv = cv2.cvtColor(v.image, cv2.COLOR_BGR2HSV)
            hs.append(float(np.median(hsv[:, :, 0])))
            ss.append(float(np.median(hsv[:, :, 1])))
            vs.append(float(np.median(hsv[:, :, 2])))
        ref = (
            float(np.mean(hs)) if hs else 0.0,
            float(np.mean(ss)) if ss else 0.0,
            float(np.mean(vs)) if vs else 0.0,
        )
        setattr(bundle, "_ref_color", ref)
        return ref

    # ---------- Library ----------

    def has_template(self, resource_id: str) -> bool:
        bundle = self._bundles.get(resource_id)
        return bundle is not None and bundle.count > 0

    def list_templates(self) -> list[str]:
        return sorted(self._bundles.keys())

    def count_variants(self, resource_id: str) -> int:
        bundle = self._bundles.get(resource_id)
        return bundle.count if bundle else 0

    def delete(self, resource_id: str) -> bool:
        bundle = self._bundles.pop(resource_id, None)
        if bundle is None:
            return False
        for v in bundle.variants:
            try:
                v.path.unlink(missing_ok=True)
            except Exception:
                pass
        return True

    # ---------- Internals ----------

    def _next_path(self, resource_id: str) -> Path:
        """Retourne le prochain chemin disponible : `ble.png`, puis `ble_2.png`, `ble_3.png`..."""
        base = self._dir / f"{resource_id}.png"
        if not base.exists():
            return base
        for n in range(2, 1000):
            candidate = self._dir / f"{resource_id}_{n}.png"
            if not candidate.exists():
                return candidate
        return base  # fallback improbable

    def _load_all(self) -> None:
        """Charge tous les PNGs + génère des variantes auto (scales × rotations)."""
        seen_bases: set[str] = set()
        for png in sorted(self._dir.glob("*.png")):
            try:
                img = cv2.imread(str(png), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                h, w = img.shape[:2]
                m = _SUFFIX_RE.match(png.stem)
                if not m:
                    continue
                base = m.group("base")
                bundle = self._bundles.setdefault(base, TemplateBundle(resource_id=base))
                # Ajoute le template original
                bundle.variants.append(TemplateVariant(path=png, image=img, width=w, height=h))
                seen_bases.add(base)
            except Exception as exc:
                logger.debug("Chargement template {} échoué : {}", png, exc)

        # Pour chaque bundle, génère des variantes auto (scales + rotations)
        for base in seen_bases:
            bundle = self._bundles[base]
            originals = list(bundle.variants)  # copie avant d'ajouter
            for orig in originals:
                bundle.variants.extend(self._generate_variants(orig))

        if self._bundles:
            summary = ", ".join(f"{k}×{v.count}" for k, v in self._bundles.items())
            logger.info("Templates chargés (avec variantes auto) : {}", summary)

    def _generate_variants(self, original: TemplateVariant) -> list[TemplateVariant]:
        """Génère des variantes (scale × rotation) à partir d'un template original.

        Skip la combinaison (scale=1.0, rot=0) qui est déjà le template original.
        """
        variants: list[TemplateVariant] = []
        h, w = original.height, original.width
        center = (w / 2, h / 2)

        for scale in self.AUTO_SCALES:
            for rot_deg in self.AUTO_ROTATIONS_DEG:
                if abs(scale - 1.0) < 1e-3 and rot_deg == 0:
                    continue  # c'est l'original
                try:
                    M = cv2.getRotationMatrix2D(center, rot_deg, scale)
                    new_w = max(16, int(w * scale))
                    new_h = max(16, int(h * scale))
                    # Ajuste la translation pour recentrer après scale
                    M[0, 2] += (new_w - w) / 2
                    M[1, 2] += (new_h - h) / 2
                    warped = cv2.warpAffine(
                        original.image, M, (new_w, new_h),
                        flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT,
                    )
                    variants.append(TemplateVariant(
                        path=original.path,  # pas de fichier distinct
                        image=warped,
                        width=new_w,
                        height=new_h,
                    ))
                except Exception:
                    continue
        return variants

    def _get_cursor_pos(self) -> tuple[int, int] | tuple[None, None]:
        try:
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            p = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            return int(p.x), int(p.y)
        except Exception:
            return None, None
