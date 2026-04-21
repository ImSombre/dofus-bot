"""Outil de calibration HSV pour Dofus.

Permet au user de :
  1. Charger un screenshot Dofus (ou capture live)
  2. Cliquer sur des pixels précis (cases PM, murs, ennemis…)
  3. Voir la valeur HSV du pixel + calculer le range élargi
  4. Sauvegarder la calibration dans un JSON → repris par les modules detecteurs

Interface Qt : fenêtre avec :
  - Image Dofus affichée
  - Panneau latéral : catégorie (PM cells, obstacles, enemy circles, player circle)
  - Ligne du bas : HSV picked + range auto élargi
  - Bouton "Sauvegarder"

Utilisation :
    python -m src.services.hsv_calibrator path/to/screenshot.jpg
    ou sans arg : capture écran temps réel via mss

Le fichier de sortie : data/knowledge/hsv_calibration.json
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from loguru import logger


CALIBRATION_FILE = Path("data/knowledge/hsv_calibration.json")


# Catégories à calibrer + ranges par défaut (v0.6.x actuel)
DEFAULT_CATEGORIES = {
    "pm_cell": {
        "description": "Cases de déplacement vertes (PM)",
        "default_low": [45, 100, 120],
        "default_high": [85, 255, 255],
        "samples": [],  # [[h, s, v], ...]
    },
    "obstacle_stone_light": {
        "description": "Murs / pierre claire beige",
        "default_low": [10, 25, 140],
        "default_high": [30, 110, 215],
        "samples": [],
    },
    "obstacle_stone_dark": {
        "description": "Pierre sombre / colonnes gris",
        "default_low": [0, 0, 70],
        "default_high": [30, 50, 150],
        "samples": [],
    },
    "enemy_circle": {
        "description": "Cercle bleu ennemi sous le mob",
        "default_low": [90, 120, 80],
        "default_high": [120, 255, 255],
        "samples": [],
    },
    "player_circle": {
        "description": "Cercle rouge perso sous toi",
        "default_low": [0, 120, 80],
        "default_high": [10, 255, 255],
        "samples": [],
    },
    "end_turn_button_active": {
        "description": "Bouton TERMINER LE TOUR actif (jaune-vert vif)",
        "default_low": [25, 80, 160],
        "default_high": [70, 255, 255],
        "samples": [],
    },
}


@dataclass
class CalibrationData:
    """Données de calibration sauvegardées."""
    categories: dict[str, dict] = field(default_factory=lambda: dict(DEFAULT_CATEGORIES))
    version: int = 1

    @classmethod
    def load(cls, path: Path | str = CALIBRATION_FILE) -> CalibrationData:
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            out = cls()
            for cat_name, cat_data in data.get("categories", {}).items():
                if cat_name in out.categories:
                    out.categories[cat_name].update(cat_data)
            return out
        except Exception as exc:
            logger.warning("load calibration échec : {}, default", exc)
            return cls()

    def save(self, path: Path | str = CALIBRATION_FILE) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # On recalcule les ranges actuels depuis les samples avant save
        self.recompute_ranges()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    def add_sample(self, category: str, hsv: tuple[int, int, int]) -> None:
        if category in self.categories:
            self.categories[category].setdefault("samples", []).append(list(hsv))

    def recompute_ranges(self) -> None:
        """Pour chaque catégorie avec des samples, calcule le range [min, max]
        avec marge sécurité."""
        for cat_name, cat in self.categories.items():
            samples = cat.get("samples", [])
            if not samples:
                continue
            arr = np.array(samples)
            # Min/Max par canal avec padding
            h_min = max(0, int(arr[:, 0].min() - 5))
            h_max = min(179, int(arr[:, 0].max() + 5))
            s_min = max(0, int(arr[:, 1].min() - 20))
            s_max = min(255, int(arr[:, 1].max() + 20))
            v_min = max(0, int(arr[:, 2].min() - 20))
            v_max = min(255, int(arr[:, 2].max() + 20))
            cat["computed_low"] = [h_min, s_min, v_min]
            cat["computed_high"] = [h_max, s_max, v_max]

    def get_range(self, category: str) -> tuple[list[int], list[int]]:
        """Retourne (low, high) : soit calculé depuis samples, soit défaut."""
        cat = self.categories.get(category, {})
        if cat.get("computed_low") and cat.get("computed_high"):
            return cat["computed_low"], cat["computed_high"]
        return cat.get("default_low", [0, 0, 0]), cat.get("default_high", [179, 255, 255])


def pick_hsv_from_bgr(frame_bgr: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
    """Retourne le HSV du pixel (x, y) dans la frame BGR."""
    if frame_bgr is None or frame_bgr.size == 0:
        return (0, 0, 0)
    h, w = frame_bgr.shape[:2]
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    # Moyenne sur un 3x3 pour lisser le bruit
    y1, y2 = max(0, y - 1), min(h, y + 2)
    x1, x2 = max(0, x - 1), min(w, x + 2)
    patch_bgr = frame_bgr[y1:y2, x1:x2]
    patch_hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    mean = patch_hsv.reshape(-1, 3).mean(axis=0)
    return (int(mean[0]), int(mean[1]), int(mean[2]))


class HsvCalibratorApp:
    """Application GUI Qt pour calibration interactive.

    Utilisation :
        app = HsvCalibratorApp()
        app.load_image("screenshot.jpg")
        app.run()  # ouvre la fenêtre
    """

    def __init__(self) -> None:
        self.data = CalibrationData.load()
        self.current_category = "pm_cell"
        self.current_image_path: Path | None = None
        self.current_frame: np.ndarray | None = None
        self._qt_app = None
        self._window = None
        self._label_image = None
        self._combo_category = None
        self._label_status = None

    def load_image(self, path: str | Path) -> bool:
        self.current_image_path = Path(path)
        img = cv2.imread(str(path))
        if img is None:
            logger.error("Impossible de charger {}", path)
            return False
        self.current_frame = img
        return True

    def _on_click(self, x: int, y: int) -> None:
        if self.current_frame is None:
            return
        hsv = pick_hsv_from_bgr(self.current_frame, x, y)
        self.data.add_sample(self.current_category, hsv)
        logger.info(
            "Sample ajouté cat={} : HSV=({}, {}, {}) total={}",
            self.current_category, *hsv,
            len(self.data.categories[self.current_category]["samples"]),
        )
        if self._label_status:
            count = len(self.data.categories[self.current_category]["samples"])
            self._label_status.setText(
                f"{self.current_category} : {count} samples — dernier HSV={hsv}"
            )

    def save(self) -> None:
        self.data.save()
        logger.info("Calibration sauvée dans {}", CALIBRATION_FILE)
        if self._label_status:
            self._label_status.setText(f"✓ Sauvegardé dans {CALIBRATION_FILE.name}")

    def run(self) -> None:
        """Lance l'interface Qt."""
        try:
            from PyQt6.QtCore import Qt  # noqa: PLC0415
            from PyQt6.QtGui import QImage, QPixmap  # noqa: PLC0415
            from PyQt6.QtWidgets import (  # noqa: PLC0415
                QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow,
                QPushButton, QVBoxLayout, QWidget,
            )
        except ImportError:
            logger.error("PyQt6 non installé")
            return

        self._qt_app = QApplication.instance() or QApplication(sys.argv)
        self._window = QMainWindow()
        self._window.setWindowTitle("Dofus HSV Calibrator")
        self._window.resize(1400, 900)

        central = QWidget()
        main_layout = QVBoxLayout(central)

        # Barre du haut : catégorie + bouton save
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Catégorie à calibrer :"))
        self._combo_category = QComboBox()
        for name, cat in self.data.categories.items():
            desc = cat.get("description", name)
            self._combo_category.addItem(f"{name} — {desc}", name)
        self._combo_category.currentIndexChanged.connect(self._on_category_changed)
        top_row.addWidget(self._combo_category, stretch=1)

        btn_save = QPushButton("💾 Sauvegarder")
        btn_save.clicked.connect(lambda: self.save())
        top_row.addWidget(btn_save)

        btn_reset = QPushButton("🗑 Reset catégorie")
        btn_reset.clicked.connect(self._reset_current_category)
        top_row.addWidget(btn_reset)
        main_layout.addLayout(top_row)

        # Image avec click handler
        self._label_image = QLabel()
        self._label_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_image.setScaledContents(False)
        self._label_image.mousePressEvent = self._qt_click_handler  # override
        main_layout.addWidget(self._label_image, stretch=1)

        # Label status bas
        self._label_status = QLabel("Clique sur un pixel de la catégorie choisie.")
        main_layout.addWidget(self._label_status)

        self._window.setCentralWidget(central)

        # Display image
        self._refresh_image()

        self._window.show()
        logger.info("HSV Calibrator ouvert. Cliquer sur les pixels pour capturer.")
        self._qt_app.exec()

    def _refresh_image(self) -> None:
        if self.current_frame is None or self._label_image is None:
            return
        from PyQt6.QtCore import Qt  # noqa: PLC0415
        from PyQt6.QtGui import QImage, QPixmap  # noqa: PLC0415
        h, w = self.current_frame.shape[:2]
        # Resize pour tenir dans la fenêtre
        max_w = 1300
        scale = min(1.0, max_w / w)
        disp_w, disp_h = int(w * scale), int(h * scale)
        disp = cv2.resize(self.current_frame, (disp_w, disp_h))
        # BGR → RGB
        disp_rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        qimg = QImage(disp_rgb.data, disp_w, disp_h, disp_w * 3, QImage.Format.Format_RGB888)
        self._label_image.setPixmap(QPixmap.fromImage(qimg))
        self._label_image._scale = scale  # sauvegarde pour scale inverse clic

    def _qt_click_handler(self, event) -> None:
        if self.current_frame is None:
            return
        pos = event.position()
        # Scale inverse : position clic (label) → pixel frame original
        scale = getattr(self._label_image, "_scale", 1.0)
        # Le label centre l'image → on prend la taille réelle du pixmap
        pm = self._label_image.pixmap()
        if pm is None:
            return
        label_w = self._label_image.width()
        label_h = self._label_image.height()
        pm_w = pm.width()
        pm_h = pm.height()
        # Coin top-left du pixmap dans le label (centré)
        offset_x = (label_w - pm_w) // 2
        offset_y = (label_h - pm_h) // 2
        rx = int(pos.x()) - offset_x
        ry = int(pos.y()) - offset_y
        if rx < 0 or ry < 0 or rx >= pm_w or ry >= pm_h:
            return
        orig_x = int(rx / scale)
        orig_y = int(ry / scale)
        self._on_click(orig_x, orig_y)

    def _on_category_changed(self, _idx: int) -> None:
        self.current_category = self._combo_category.currentData()
        count = len(self.data.categories[self.current_category].get("samples", []))
        self._label_status.setText(f"{self.current_category} : {count} samples.")

    def _reset_current_category(self) -> None:
        self.data.categories[self.current_category]["samples"] = []
        self.data.categories[self.current_category].pop("computed_low", None)
        self.data.categories[self.current_category].pop("computed_high", None)
        self._label_status.setText(f"{self.current_category} reset à 0 samples.")


def main() -> None:
    """Entry point CLI."""
    if len(sys.argv) < 2:
        print("Usage: python -m src.services.hsv_calibrator <screenshot.jpg>")
        print("  ou avec une capture live : python -m src.services.hsv_calibrator live")
        sys.exit(1)

    app = HsvCalibratorApp()
    arg = sys.argv[1]
    if arg == "live":
        # Capture écran en live
        try:
            from mss import mss  # noqa: PLC0415
            with mss() as sct:
                sct_img = sct.grab(sct.monitors[1])
                frame = np.array(sct_img)
                # Convert BGRA → BGR
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            app.current_frame = frame
        except Exception as exc:
            print(f"Capture échec : {exc}")
            sys.exit(1)
    else:
        if not app.load_image(arg):
            print(f"Impossible de charger {arg}")
            sys.exit(1)

    app.run()


if __name__ == "__main__":
    main()
