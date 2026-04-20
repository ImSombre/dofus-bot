"""Apprentissage HSV par sampling sous le curseur.

Usage type :
    learner = HsvLearner()
    # L'utilisateur hover sa souris sur un blé dans Dofus
    hsv = learner.sample_around_cursor(radius_px=20)
    learner.save("ble", hsv)

Les HSV apprises sont stockées dans `data/calibration/learned_hsv.json`.
Le FarmWorker les lit en priorité par rapport aux estimations du catalogue.
"""
from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import mss
import numpy as np
from loguru import logger


@dataclass
class LearnedHsv:
    h: int           # hue OpenCV [0..180]
    s: int           # saturation [0..255]
    v: int           # value [0..255]
    tolerance: int   # tolérance hue (±N)
    samples: int     # nombre d'échantillons utilisés
    notes: str = ""

    def to_dict(self) -> dict:
        return {"h": self.h, "s": self.s, "v": self.v, "tolerance": self.tolerance,
                "samples": self.samples, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "LearnedHsv":
        return cls(
            h=int(d["h"]), s=int(d["s"]), v=int(d["v"]),
            tolerance=int(d.get("tolerance", 18)),
            samples=int(d.get("samples", 1)),
            notes=d.get("notes", ""),
        )


class HsvLearner:
    """Gère l'échantillonnage + la persistance des HSV apprises."""

    def __init__(self, calibration_dir: Path | None = None) -> None:
        base = calibration_dir or Path("data/calibration")
        base.mkdir(parents=True, exist_ok=True)
        self._path = base / "learned_hsv.json"
        self._cache: dict[str, LearnedHsv] = {}
        self._load()

    # ---------- Sampling ----------

    def sample_around_cursor(self, radius_px: int = 20) -> LearnedHsv | None:
        """Capture un carré centré sur le curseur et retourne la HSV dominante.

        Retourne None si la capture échoue.
        """
        cx, cy = self._get_cursor_pos()
        if cx is None:
            return None

        size = radius_px * 2 + 1
        x0 = cx - radius_px
        y0 = cy - radius_px

        try:
            with mss.mss() as sct:
                raw = sct.grab({"left": x0, "top": y0, "width": size, "height": size})
                bgra = np.array(raw)
                bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        except Exception as exc:
            logger.warning("Sampling échoué : {}", exc)
            return None

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        # Médiane (robuste aux outliers) sur chaque canal
        h_med = int(np.median(hsv[:, :, 0]))
        s_med = int(np.median(hsv[:, :, 1]))
        v_med = int(np.median(hsv[:, :, 2]))
        # Écart-type sur H pour tolérance adaptative
        h_std = int(np.std(hsv[:, :, 0]))
        tolerance = max(10, min(35, h_std + 8))

        return LearnedHsv(h=h_med, s=s_med, v=v_med, tolerance=tolerance, samples=1)

    # ---------- Persistance ----------

    def save(self, resource_id: str, hsv: LearnedHsv, merge: bool = True) -> None:
        """Sauvegarde la HSV apprise pour une ressource.

        Si `merge=True` et qu'une HSV existe déjà, fait une moyenne pondérée
        (samples cumulés) pour affiner.
        """
        if merge and resource_id in self._cache:
            old = self._cache[resource_id]
            total = old.samples + hsv.samples
            merged = LearnedHsv(
                h=int((old.h * old.samples + hsv.h * hsv.samples) / total),
                s=int((old.s * old.samples + hsv.s * hsv.samples) / total),
                v=int((old.v * old.samples + hsv.v * hsv.samples) / total),
                tolerance=max(old.tolerance, hsv.tolerance),
                samples=total,
                notes=old.notes or hsv.notes,
            )
            self._cache[resource_id] = merged
            logger.info(
                "HSV appris (fusion {} échantillons) pour '{}': H={} S={} V={} tol={}",
                total, resource_id, merged.h, merged.s, merged.v, merged.tolerance,
            )
        else:
            self._cache[resource_id] = hsv
            logger.info(
                "HSV appris (1 échantillon) pour '{}': H={} S={} V={} tol={}",
                resource_id, hsv.h, hsv.s, hsv.v, hsv.tolerance,
            )
        self._persist()

    def get(self, resource_id: str) -> LearnedHsv | None:
        return self._cache.get(resource_id)

    def all_learned(self) -> dict[str, LearnedHsv]:
        return dict(self._cache)

    def clear(self, resource_id: str | None = None) -> None:
        if resource_id is None:
            self._cache.clear()
        else:
            self._cache.pop(resource_id, None)
        self._persist()

    # ---------- Internals ----------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for k, v in data.items():
                self._cache[k] = LearnedHsv.from_dict(v)
            logger.info("Calibration chargée : {} ressources apprises", len(self._cache))
        except Exception as exc:
            logger.warning("Lecture learned_hsv.json échouée : {}", exc)

    def _persist(self) -> None:
        try:
            data = {k: v.to_dict() for k, v in self._cache.items()}
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("Écriture learned_hsv.json échouée : {}", exc)

    def _get_cursor_pos(self) -> tuple[int, int] | tuple[None, None]:
        try:
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            p = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            return int(p.x), int(p.y)
        except Exception:
            return None, None
