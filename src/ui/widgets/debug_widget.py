"""Debug tab — live vision testing without running the bot."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ui.styles import ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED, BORDER, TEXT_SECONDARY
from src.ui.widgets.common import ImageViewer, make_card

if TYPE_CHECKING:
    from src.models.detection import DetectedObject
    from src.services.vision import MssVisionService


# ---------------------------------------------------------------------------
# Worker — runs vision operations off the main thread
# ---------------------------------------------------------------------------


class _Signals(QObject):
    finished = pyqtSignal(object, float, str)  # (result, elapsed_ms, op_name)
    error = pyqtSignal(str)


class VisionWorker(QRunnable):
    """Generic vision task executed in a QThreadPool worker."""

    def __init__(self, fn: Any, op_name: str) -> None:
        super().__init__()
        self.signals = _Signals()
        self._fn = fn
        self._op_name = op_name
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self) -> None:
        t0 = time.perf_counter()
        try:
            result = self._fn()
            elapsed = (time.perf_counter() - t0) * 1000
            self.signals.finished.emit(result, elapsed, self._op_name)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Clickable image label with rubber-band selection
# ---------------------------------------------------------------------------


class SelectableImageLabel(QLabel):
    """QLabel that shows a rubberband on drag to select an OCR region."""

    region_selected = pyqtSignal(int, int, int, int)  # x, y, w, h in image coords

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._origin: tuple[int, int] | None = None
        self._rubberband = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._pixmap_size: tuple[int, int] = (1, 1)
        self._display_size: tuple[int, int] = (1, 1)

    def set_pixmap_info(self, orig_w: int, orig_h: int, disp_w: int, disp_h: int) -> None:
        self._pixmap_size = (orig_w, orig_h)
        self._display_size = (disp_w, disp_h)

    def mousePressEvent(self, ev: Any) -> None:  # type: ignore[override]
        if ev.button() == Qt.MouseButton.LeftButton:
            self._origin = (ev.pos().x(), ev.pos().y())
            from PyQt6.QtCore import QRect  # noqa: PLC0415

            self._rubberband.setGeometry(QRect(ev.pos(), ev.pos()))
            self._rubberband.show()

    def mouseMoveEvent(self, ev: Any) -> None:  # type: ignore[override]
        if self._origin is not None:
            from PyQt6.QtCore import QPoint, QRect  # noqa: PLC0415

            self._rubberband.setGeometry(
                QRect(QPoint(*self._origin), ev.pos()).normalized()
            )

    def mouseReleaseEvent(self, ev: Any) -> None:  # type: ignore[override]
        if self._origin is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._rubberband.hide()
            from PyQt6.QtCore import QPoint, QRect  # noqa: PLC0415

            rect = QRect(QPoint(*self._origin), ev.pos()).normalized()
            self._origin = None
            if rect.width() > 5 and rect.height() > 5:
                # Convert display coords → original image coords
                sx = self._pixmap_size[0] / max(self._display_size[0], 1)
                sy = self._pixmap_size[1] / max(self._display_size[1], 1)
                self.region_selected.emit(
                    int(rect.x() * sx),
                    int(rect.y() * sy),
                    int(rect.width() * sx),
                    int(rect.height() * sy),
                )


# ---------------------------------------------------------------------------
# DebugWidget
# ---------------------------------------------------------------------------


class DebugWidget(QWidget):
    """Debug tab: capture, detect, OCR zone, results table, log panel."""

    # Signal thread-safe pour logger depuis les workers
    _log_signal = pyqtSignal(str, str)  # (msg, color)

    def __init__(
        self,
        vision: "MssVisionService",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vision = vision
        self._last_frame: np.ndarray | None = None
        self._pool = QThreadPool.globalInstance()
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._do_capture)
        self._ocr_pending = False

        self._build_ui()
        # Route tous les _log via le signal → thread-safe
        self._log_signal.connect(self._do_log_main_thread)
        # Charge et applique les prefs utilisateur (template size, zaap dest, fenêtre)
        self._apply_user_prefs()

    def _apply_user_prefs(self) -> None:
        """Charge les prefs sauvegardées et les applique aux champs Debug."""
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            gp = get_user_prefs().global_prefs
            if gp.template_size_px and hasattr(self, "_spin_template_size"):
                self._spin_template_size.setValue(gp.template_size_px)
            if gp.last_zaap_query and hasattr(self, "_edit_zaap_dest"):
                self._edit_zaap_dest.setText(gp.last_zaap_query)
            if gp.dofus_window_title and hasattr(self, "_edit_window_title"):
                # Ne pas écraser si déjà rempli par Settings
                if not self._edit_window_title.text().strip():
                    self._edit_window_title.setText(gp.dofus_window_title)
            # Restaure le mode écran entier si sauvegardé OU auto-activé par vision
            auto_active = hasattr(self._vision, "is_fullscreen_mode") and self._vision.is_fullscreen_mode()
            should_enable = auto_active or getattr(gp, "fullscreen_mode", False)
            if hasattr(self, "_chk_fullscreen_mode") and should_enable:
                self._chk_fullscreen_mode.blockSignals(True)
                self._chk_fullscreen_mode.setChecked(True)
                self._chk_fullscreen_mode.blockSignals(False)
                if hasattr(self._vision, "set_fullscreen_mode"):
                    self._vision.set_fullscreen_mode(True)
        except Exception as exc:
            from loguru import logger  # noqa: PLC0415
            logger.debug("Chargement prefs Debug échoué : {}", exc)

    def _sync_fullscreen_checkbox(self) -> None:
        """Synchronise la case avec l'état réel de la vision (pour l'auto-activation)."""
        if not hasattr(self, "_chk_fullscreen_mode") or not hasattr(self._vision, "is_fullscreen_mode"):
            return
        current = self._vision.is_fullscreen_mode()
        if self._chk_fullscreen_mode.isChecked() != current:
            self._chk_fullscreen_mode.blockSignals(True)
            self._chk_fullscreen_mode.setChecked(current)
            self._chk_fullscreen_mode.blockSignals(False)

    def _on_fullscreen_toggle(self, state: int) -> None:
        """Active/désactive le mode capture écran entier + sauve le prefs."""
        enabled = state == Qt.CheckState.Checked.value
        if hasattr(self._vision, "set_fullscreen_mode"):
            self._vision.set_fullscreen_mode(enabled)
        self._log(
            f"🖥 Mode écran entier {'ACTIVÉ' if enabled else 'désactivé'}",
            color=ACCENT_GREEN if enabled else ACCENT_BLUE,
        )
        # Sauvegarde dans les prefs
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            prefs = get_user_prefs()
            prefs.global_prefs.fullscreen_mode = enabled
            prefs.save()
        except Exception:
            pass

    def _save_debug_prefs(self) -> None:
        """Sauvegarde les champs Debug persistants (template size, dest zaap, fenêtre)."""
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            prefs = get_user_prefs()
            gp = prefs.global_prefs
            if hasattr(self, "_spin_template_size"):
                gp.template_size_px = self._spin_template_size.value()
            if hasattr(self, "_edit_zaap_dest"):
                gp.last_zaap_query = self._edit_zaap_dest.text().strip() or gp.last_zaap_query
            if hasattr(self, "_edit_window_title"):
                gp.dofus_window_title = self._edit_window_title.text().strip() or gp.dofus_window_title
            prefs.save()
        except Exception as exc:
            from loguru import logger  # noqa: PLC0415
            logger.debug("Sauvegarde prefs Debug échouée : {}", exc)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        splitter.setSizes([400, 600])

        root.addWidget(splitter)
        # Populate window list only after _log_panel is constructed
        self._refresh_windows()

    # ------------------------------------------------------------------
    # Helper : détection de changement visuel (map change via frame diff)
    # ------------------------------------------------------------------

    @staticmethod
    def _frame_differs_significantly(
        frame_a: np.ndarray | None,
        frame_b: np.ndarray | None,
        threshold_pct: float = 15.0,
    ) -> bool:
        """Retourne True si les 2 frames diffèrent de >= threshold_pct % de pixels.

        Sert de double-check quand l'OCR donne le même coord et qu'on veut
        quand même savoir si la map a changé. Sur un changement de map Dofus,
        la majorité de l'écran change (30-80 % selon la map précédente/nouvelle).
        """
        if frame_a is None or frame_b is None:
            return False
        if frame_a.shape != frame_b.shape:
            return True  # capture de taille différente = forcément changé
        try:
            import cv2  # noqa: PLC0415
            gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
            gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(gray_a, gray_b)
            # Pixels qui ont changé "significativement" (diff > 30 niveaux de gris)
            changed = (diff > 30).sum()
            total = diff.size
            pct = 100.0 * changed / max(total, 1)
            return pct >= threshold_pct
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Helpers auto-focus : bascule sur la fenêtre Dofus pendant le countdown
    # ------------------------------------------------------------------

    def _activate_dofus_window(self, title: str | None = None) -> bool:
        """Bascule immédiatement sur la fenêtre Dofus (pendant le countdown utilisateur).

        Retourne True si le focus a été donné. Passer `title=None` pour utiliser
        le champ "Fenêtre cible" de l'UI.
        """
        if title is None:
            title = self._edit_window_title.text().strip()
        if not title:
            return False
        try:
            import pygetwindow as gw  # noqa: PLC0415
            matches = gw.getWindowsWithTitle(title)
            if not matches:
                return False
            w = matches[0]
            if w.isMinimized:
                w.restore()
            try:
                w.activate()
            except Exception:
                # Fallback win32 si activate() est bloqué
                try:
                    import ctypes  # noqa: PLC0415
                    ctypes.windll.user32.SetForegroundWindow(w._hWnd)
                except Exception:
                    return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Helpers pour un layout propre
    # ------------------------------------------------------------------

    @staticmethod
    def _styled_button(label: str, color: str = "#4fc3f7", text_color: str = "black") -> QPushButton:
        """Bouton stylisé compact pour l'onglet Debug."""
        btn = QPushButton(label)
        # Teinte hover : on assombrit légèrement
        hover_map = {
            "#4fc3f7": "#29b6f6",
            "#66bb6a": "#43a047",
            "#ffa726": "#fb8c00",
            "#ab47bc": "#8e24aa",
            "#ef5350": "#e53935",
            "#78909c": "#607d8b",
        }
        hover = hover_map.get(color, color)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: {text_color}; font-weight: 600;"
            f" padding: 7px 12px; border-radius: 5px; border: none; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
            "QPushButton:disabled { background-color: #3a3a4e; color: #808080; }"
        )
        return btn

    @staticmethod
    def _make_group(title: str) -> tuple[QGroupBox, QVBoxLayout]:
        """Retourne (groupbox, layout) — la groupbox est stylée avec un titre clair."""
        grp = QGroupBox(title)
        grp.setStyleSheet(
            "QGroupBox {"
            "  background-color: #1e1e2a; border: 1px solid #3a3a4e; border-radius: 8px;"
            "  margin-top: 12px; padding-top: 8px; color: #e0e0e0; font-weight: 600;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin; subcontrol-position: top left;"
            "  left: 12px; padding: 0 8px; color: #4fc3f7; font-size: 10pt;"
            "}"
        )
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(8)
        return grp, lay

    # ------------------------------------------------------------------
    # Left panel : sections organisées dans une QScrollArea
    # ------------------------------------------------------------------

    def _build_left_panel(self) -> QWidget:
        # Scroll area pour que tout rentre quelle que soit la taille de la fenêtre
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        # === Section 1 : Fenêtre Dofus ===
        grp, grp_lay = self._make_group("🎯  Fenêtre Dofus cible")
        self._edit_window_title = QLineEdit()
        self._edit_window_title.setPlaceholderText("Dofus 2.64 (vide = écran entier)")
        try:
            from src.config.settings import Settings  # noqa: PLC0415
            s = Settings()
            self._edit_window_title.setText(s.dofus_window_title)
        except Exception:
            pass
        self._edit_window_title.editingFinished.connect(self._on_window_title_edited)
        self._edit_window_title.editingFinished.connect(self._save_debug_prefs)
        grp_lay.addWidget(self._edit_window_title)

        self._list_windows = QListWidget()
        self._list_windows.setMaximumHeight(110)
        self._list_windows.itemClicked.connect(self._on_window_item_clicked)
        grp_lay.addWidget(self._list_windows)

        self._btn_refresh_windows = self._styled_button("↺  Rafraîchir la liste", color="#78909c", text_color="white")
        self._btn_refresh_windows.clicked.connect(self._refresh_windows)
        grp_lay.addWidget(self._btn_refresh_windows)

        # Toggle : mode écran entier (override pour Dofus windowed sur grand écran)
        self._chk_fullscreen_mode = QCheckBox("🖥  Forcer capture ÉCRAN ENTIER (ignore fenêtre Dofus)")
        self._chk_fullscreen_mode.setToolTip(
            "Active ce mode si Dofus tourne en fenêtré mais tu veux que les clics bord\n"
            "soient calculés sur ton écran entier (ex: 2560×1440 au lieu de 1920×1080)."
        )
        self._chk_fullscreen_mode.stateChanged.connect(self._on_fullscreen_toggle)
        grp_lay.addWidget(self._chk_fullscreen_mode)
        layout.addWidget(grp)

        # === Section 2 : Capture ===
        grp, grp_lay = self._make_group("📷  Capture d'écran")
        self._btn_capture = self._styled_button("Capturer fenêtre Dofus", color="#4fc3f7")
        self._btn_capture.clicked.connect(self._do_capture)
        grp_lay.addWidget(self._btn_capture)

        self._btn_capture_full = self._styled_button("Capturer écran ENTIER", color="#78909c", text_color="white")
        self._btn_capture_full.setToolTip("Bypass la détection Dofus — capture l'écran primaire complet")
        self._btn_capture_full.clicked.connect(self._do_capture_full_screen)
        grp_lay.addWidget(self._btn_capture_full)

        self._btn_test_combat_detection = self._styled_button(
            "🎯 Tester détection combat (ennemis/perso)", color="#ff9800",
        )
        self._btn_test_combat_detection.setToolTip(
            "Capture + analyse : détecte les cercles rouge (perso) et bleus (ennemis).\n"
            "Sauvegarde une image annotée dans data/ocr_debug/ pour calibration."
        )
        self._btn_test_combat_detection.clicked.connect(self._do_test_combat_detection)
        grp_lay.addWidget(self._btn_test_combat_detection)

        auto_row = QHBoxLayout()
        self._chk_auto = QCheckBox("Auto toutes les")
        self._chk_auto.stateChanged.connect(self._toggle_auto_capture)
        self._spin_auto = QSpinBox()
        self._spin_auto.setRange(1, 60)
        self._spin_auto.setValue(5)
        self._spin_auto.setSuffix(" s")
        self._spin_auto.setFixedWidth(70)
        auto_row.addWidget(self._chk_auto)
        auto_row.addWidget(self._spin_auto)
        auto_row.addStretch()
        grp_lay.addLayout(auto_row)
        layout.addWidget(grp)

        # === Section 3 : Détection ===
        grp, grp_lay = self._make_group("🔎  Détection ressources")
        self._btn_detect_cs = self._styled_button("Détecter (ColorShape)", color="#4fc3f7")
        self._btn_detect_cs.clicked.connect(self._do_detect_color_shape)
        grp_lay.addWidget(self._btn_detect_cs)

        self._btn_detect_tpl = self._styled_button("Détecter (Template)", color="#4fc3f7")
        self._btn_detect_tpl.clicked.connect(self._do_detect_template)
        grp_lay.addWidget(self._btn_detect_tpl)

        self._btn_detect_yolo = self._styled_button("Détecter (YOLO)", color="#78909c", text_color="white")
        yolo_ok = hasattr(self._vision, "yolo") and self._vision.yolo is not None
        self._btn_detect_yolo.setEnabled(yolo_ok)
        self._btn_detect_yolo.clicked.connect(self._do_detect_yolo)
        if not yolo_ok:
            self._btn_detect_yolo.setToolTip("YOLO non disponible")
        grp_lay.addWidget(self._btn_detect_yolo)
        layout.addWidget(grp)

        # === Section 4 : OCR ===
        grp, grp_lay = self._make_group("✂  Lecture texte (OCR)")
        self._btn_ocr_zone = self._styled_button("Sélectionner zone OCR", color="#4fc3f7")
        self._btn_ocr_zone.clicked.connect(self._arm_ocr_selection)
        grp_lay.addWidget(self._btn_ocr_zone)

        self._ocr_result_label = QLabel("")
        self._ocr_result_label.setWordWrap(True)
        self._ocr_result_label.setStyleSheet(f"color: {ACCENT_BLUE}; font-size: 10pt; padding: 4px;")
        grp_lay.addWidget(self._ocr_result_label)

        self._btn_read_map = self._styled_button("🗺  Lire coords map", color="#66bb6a", text_color="white")
        self._btn_read_map.setToolTip("OCR de la bannière haut-gauche")
        self._btn_read_map.clicked.connect(self._on_read_map_coords)
        grp_lay.addWidget(self._btn_read_map)

        self._map_info_label = QLabel("Carte : <i>non détectée</i>")
        self._map_info_label.setWordWrap(True)
        self._map_info_label.setTextFormat(Qt.TextFormat.RichText)
        self._map_info_label.setStyleSheet("color: #b0b0b0; font-size: 10pt; padding: 4px;")
        grp_lay.addWidget(self._map_info_label)
        layout.addWidget(grp)

        # === Section 5 : Test clic souris ===
        grp, grp_lay = self._make_group("🖱  Test clic (anti-bot)")
        self._btn_test_click = self._styled_button("Tester un clic (3 s)", color="#ffa726")
        self._btn_test_click.clicked.connect(self._on_test_click)
        grp_lay.addWidget(self._btn_test_click)
        layout.addWidget(grp)

        # === Section 6 : Calibration HSV ===
        grp, grp_lay = self._make_group("🎯  Calibration couleur (HSV)")
        self._btn_calibrate = self._styled_button("Calibrer une ressource (5 s)", color="#66bb6a", text_color="white")
        self._btn_calibrate.clicked.connect(self._on_calibrate_resource)
        grp_lay.addWidget(self._btn_calibrate)

        self._calibration_status = QLabel(self._format_calibration_status())
        self._calibration_status.setWordWrap(True)
        self._calibration_status.setStyleSheet("color: #b0b0b0; font-size: 9pt; padding: 4px;")
        grp_lay.addWidget(self._calibration_status)

        self._btn_clear_calibration = self._styled_button("🗑  Effacer calibration", color="#78909c", text_color="white")
        self._btn_clear_calibration.clicked.connect(self._on_clear_calibration)
        grp_lay.addWidget(self._btn_clear_calibration)
        layout.addWidget(grp)

        # === Section 7 : Templates matching ===
        grp, grp_lay = self._make_group("📷  Templates (matching précis)")
        tpl_size_row = QHBoxLayout()
        tpl_size_row.addWidget(QLabel("Taille crop :"))
        self._spin_template_size = QSpinBox()
        self._spin_template_size.setRange(30, 200)
        self._spin_template_size.setValue(50)
        self._spin_template_size.setSingleStep(5)
        self._spin_template_size.setSuffix(" px")
        self._spin_template_size.setFixedWidth(90)
        self._spin_template_size.valueChanged.connect(lambda _v: self._save_debug_prefs())
        tpl_size_row.addWidget(self._spin_template_size)
        tpl_size_row.addStretch()
        grp_lay.addLayout(tpl_size_row)

        self._btn_capture_template = self._styled_button("Capturer template (5 s)", color="#ab47bc", text_color="white")
        self._btn_capture_template.clicked.connect(self._on_capture_template)
        grp_lay.addWidget(self._btn_capture_template)

        self._template_status = QLabel(self._format_template_status())
        self._template_status.setWordWrap(True)
        self._template_status.setStyleSheet("color: #b0b0b0; font-size: 9pt; padding: 4px;")
        grp_lay.addWidget(self._template_status)
        layout.addWidget(grp)

        # === Section 8 : Chat + Zaap ===
        grp, grp_lay = self._make_group("💬  Chat & Téléportation zaap")
        chat_row = QHBoxLayout()
        self._edit_chat_cmd = QLineEdit()
        self._edit_chat_cmd.setPlaceholderText(".zaap")
        self._edit_chat_cmd.setText(".zaap")
        chat_row.addWidget(self._edit_chat_cmd, stretch=1)
        self._btn_send_chat = self._styled_button("💬 Envoyer (3 s)", color="#4fc3f7")
        self._btn_send_chat.clicked.connect(self._on_send_chat_command)
        chat_row.addWidget(self._btn_send_chat)
        grp_lay.addLayout(chat_row)

        tp_row = QHBoxLayout()
        self._edit_zaap_dest = QLineEdit()
        self._edit_zaap_dest.setPlaceholderText("ingalsse")
        self._edit_zaap_dest.setText("ingalsse")
        self._edit_zaap_dest.editingFinished.connect(self._save_debug_prefs)
        tp_row.addWidget(self._edit_zaap_dest, stretch=1)
        self._btn_teleport = self._styled_button("🌀 Téléporter (5 s)", color="#ab47bc", text_color="white")
        self._btn_teleport.clicked.connect(self._on_test_teleport)
        tp_row.addWidget(self._btn_teleport)
        grp_lay.addLayout(tp_row)
        layout.addWidget(grp)

        # === Section 9 : Navigation map par map ===
        grp, grp_lay = self._make_group("🧭  Navigation map (clics de bord)")
        dir_row = QHBoxLayout()
        dir_row.setSpacing(4)
        for name, label in [("haut", "⬆ Haut"), ("bas", "⬇ Bas"), ("gauche", "⬅ Gauche"), ("droite", "➡ Droite")]:
            btn = self._styled_button(label, color="#66bb6a", text_color="white")
            btn.setToolTip(f"Clic bord '{name}' (3 s countdown)")
            btn.clicked.connect(lambda _checked=False, d=name: self._on_test_edge_click(d))
            dir_row.addWidget(btn)
        grp_lay.addLayout(dir_row)

        # Boutons de calibration : l'user pose la souris où il veut que le bot clique
        cal_label = QLabel("Calibration (pose ta souris où le bot doit cliquer puis :)")
        cal_label.setStyleSheet("color: #b0b0b0; font-size: 9pt; padding-top: 4px;")
        grp_lay.addWidget(cal_label)
        cal_row = QHBoxLayout()
        cal_row.setSpacing(4)
        for name, label in [("haut", "📍 Haut"), ("bas", "📍 Bas"), ("gauche", "📍 Gauche"), ("droite", "📍 Droite")]:
            btn = self._styled_button(label, color="#ab47bc", text_color="white")
            btn.setToolTip(f"Calibre '{name}' : 5 s countdown puis capture la position de ta souris")
            btn.clicked.connect(lambda _checked=False, d=name: self._on_calibrate_edge(d))
            cal_row.addWidget(btn)
        grp_lay.addLayout(cal_row)

        nav_row = QHBoxLayout()
        self._edit_nav_target = QLineEdit()
        self._edit_nav_target.setPlaceholderText("9,6")
        self._edit_nav_target.setToolTip("Coords cible (format x,y)")
        nav_row.addWidget(self._edit_nav_target, stretch=1)
        self._btn_navigate = self._styled_button("🧭 Aller à (5 s)", color="#4fc3f7")
        self._btn_navigate.clicked.connect(self._on_test_navigate)
        nav_row.addWidget(self._btn_navigate)
        grp_lay.addLayout(nav_row)
        layout.addWidget(grp)

        # === Section 10 : Macros (séquences de clics configurables) ===
        grp, grp_lay = self._make_group("🎬  Macros (séquences d'actions)")
        hint = QLabel(
            "Pour la macro 'Rejoindre DJ' : ajoute clic droit sur le NPC, puis les options de dialogue.\n"
            "Chaque 'clic' capture la position de ta souris après un countdown de 3 s."
        )
        hint.setStyleSheet("color: #808080; font-size: 9pt;")
        hint.setWordWrap(True)
        grp_lay.addWidget(hint)

        # Sélecteur de macro
        macro_row = QHBoxLayout()
        macro_row.addWidget(QLabel("Macro :"))
        self._combo_macro = QComboBox()
        self._combo_macro.setEditable(True)
        self._combo_macro.setMinimumWidth(200)
        self._combo_macro.setToolTip("Choisis une macro existante ou tape un nouveau nom")
        macro_row.addWidget(self._combo_macro, stretch=1)
        self._btn_macro_load = self._styled_button("📂 Charger", color="#78909c", text_color="white")
        self._btn_macro_load.clicked.connect(self._on_macro_load)
        macro_row.addWidget(self._btn_macro_load)
        self._btn_macro_delete = self._styled_button("🗑", color="#ef5350", text_color="white")
        self._btn_macro_delete.setMaximumWidth(40)
        self._btn_macro_delete.clicked.connect(self._on_macro_delete)
        macro_row.addWidget(self._btn_macro_delete)
        grp_lay.addLayout(macro_row)

        # Liste des steps
        from PyQt6.QtWidgets import QListWidget as _QL  # noqa: PLC0415
        self._list_macro_steps = _QL()
        self._list_macro_steps.setMaximumHeight(120)
        self._list_macro_steps.setStyleSheet(
            "QListWidget { background: #1a1a24; border: 1px solid #3a3a4e; border-radius: 4px; padding: 4px; }"
        )
        grp_lay.addWidget(self._list_macro_steps)

        # Boutons "Ajouter"
        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self._btn_add_click = self._styled_button("+ Clic 🖱", color="#4fc3f7")
        self._btn_add_click.setToolTip("5 s countdown : place ta souris où cliquer, la position est capturée")
        self._btn_add_click.clicked.connect(self._on_macro_add_click)
        add_row.addWidget(self._btn_add_click)

        self._btn_add_right_click = self._styled_button("+ Clic D 🖱", color="#4fc3f7")
        self._btn_add_right_click.setToolTip("Idem mais clic droit (pour ouvrir menu NPC)")
        self._btn_add_right_click.clicked.connect(lambda: self._on_macro_add_click(right=True))
        add_row.addWidget(self._btn_add_right_click)

        self._btn_add_wait = self._styled_button("+ Pause ⏳", color="#66bb6a", text_color="white")
        self._btn_add_wait.setToolTip("Ajoute une pause d'1 seconde (configurable)")
        self._btn_add_wait.clicked.connect(self._on_macro_add_wait)
        add_row.addWidget(self._btn_add_wait)
        grp_lay.addLayout(add_row)

        # Boutons contrôle
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)
        self._btn_macro_save = self._styled_button("💾 Sauver", color="#66bb6a", text_color="white")
        self._btn_macro_save.clicked.connect(self._on_macro_save)
        ctrl_row.addWidget(self._btn_macro_save)

        self._btn_macro_play = self._styled_button("▶ Jouer (5 s)", color="#ab47bc", text_color="white")
        self._btn_macro_play.setToolTip("Countdown 5 s (bascule sur Dofus), puis exécute toute la macro")
        self._btn_macro_play.clicked.connect(self._on_macro_play)
        ctrl_row.addWidget(self._btn_macro_play)

        self._btn_macro_clear = self._styled_button("🗑 Vider", color="#ef5350", text_color="white")
        self._btn_macro_clear.setToolTip("Supprime toutes les étapes de la macro en cours")
        self._btn_macro_clear.clicked.connect(self._on_macro_clear)
        ctrl_row.addWidget(self._btn_macro_clear)
        grp_lay.addLayout(ctrl_row)
        layout.addWidget(grp)

        # État interne pour la macro en cours d'édition
        self._current_macro_steps: list = []
        self._refresh_macro_combo()

        # --- Auto-rejoin DJ : scan coords + joue macro ---
        sep = QLabel("── Auto-rejoin sur map trigger ──")
        sep.setStyleSheet("color: #808080; font-size: 9pt; padding-top: 6px;")
        grp_lay.addWidget(sep)

        trigger_row = QHBoxLayout()
        trigger_row.addWidget(QLabel("Map trigger (x,y) :"))
        self._edit_rejoin_coords = QLineEdit()
        self._edit_rejoin_coords.setPlaceholderText("-60,-8")
        self._edit_rejoin_coords.setToolTip(
            "Quand le bot détecte cette map, il joue la macro sélectionnée.\n"
            "Typiquement = la map d'entrée du donjon où est le NPC."
        )
        trigger_row.addWidget(self._edit_rejoin_coords, stretch=1)
        grp_lay.addLayout(trigger_row)

        # Cadence auto-rejoin
        timing_row = QHBoxLayout()
        timing_row.addWidget(QLabel("Scan coords :"))
        self._spin_rejoin_scan = QSpinBox()
        self._spin_rejoin_scan.setRange(200, 10000)
        self._spin_rejoin_scan.setValue(800)
        self._spin_rejoin_scan.setSingleStep(100)
        self._spin_rejoin_scan.setSuffix(" ms")
        self._spin_rejoin_scan.setToolTip(
            "Fréquence d'OCR des coords. Plus bas = plus réactif mais plus de CPU."
        )
        timing_row.addWidget(self._spin_rejoin_scan)

        timing_row.addSpacing(16)
        timing_row.addWidget(QLabel("Cooldown :"))
        self._spin_rejoin_cooldown = QSpinBox()
        self._spin_rejoin_cooldown.setRange(1, 120)
        self._spin_rejoin_cooldown.setValue(8)
        self._spin_rejoin_cooldown.setSuffix(" s")
        self._spin_rejoin_cooldown.setToolTip(
            "Délai min entre 2 déclenchements de la macro (anti-spam)."
        )
        timing_row.addWidget(self._spin_rejoin_cooldown)
        timing_row.addStretch()
        grp_lay.addLayout(timing_row)

        rejoin_ctrl_row = QHBoxLayout()
        self._btn_rejoin_start = self._styled_button(
            "🔁 Démarrer auto-rejoin DJ", color="#66bb6a", text_color="white",
        )
        self._btn_rejoin_start.setToolTip(
            "Lance un scan continu des coords. Quand tu arrives sur la map trigger,\n"
            "la macro sélectionnée se déclenche automatiquement (cooldown 15 s)."
        )
        self._btn_rejoin_start.clicked.connect(self._on_rejoin_start)
        rejoin_ctrl_row.addWidget(self._btn_rejoin_start)

        self._btn_rejoin_stop = self._styled_button(
            "⏹ Arrêter auto-rejoin", color="#ef5350", text_color="white",
        )
        self._btn_rejoin_stop.clicked.connect(self._on_rejoin_stop)
        self._btn_rejoin_stop.setEnabled(False)
        rejoin_ctrl_row.addWidget(self._btn_rejoin_stop)
        grp_lay.addLayout(rejoin_ctrl_row)

        self._rejoin_worker = None  # AutoRejoinWorker

        # === Section 11 : Export ===
        grp, grp_lay = self._make_group("💾  Export")
        self._btn_save = self._styled_button("Sauvegarder capture (PNG)", color="#78909c", text_color="white")
        self._btn_save.clicked.connect(self._do_save_capture)
        grp_lay.addWidget(self._btn_save)

        self._btn_export_json = self._styled_button("Exporter détections (JSON)", color="#78909c", text_color="white")
        self._btn_export_json.clicked.connect(self._do_export_json)
        grp_lay.addWidget(self._btn_export_json)
        layout.addWidget(grp)

        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_right_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)

        # Image viewer
        self._image_viewer = ImageViewer(panel)
        self._image_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._image_viewer, stretch=3)

        # Results table
        self._table = QTableWidget(0, 6, panel)
        self._table.setHorizontalHeaderLabels(["Label", "x", "y", "w", "h", "Conf."])
        self._table.setMaximumHeight(150)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, stretch=0)

        # Debug log
        log_label = QLabel("Journal debug :", panel)
        log_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; font-weight: 600;")
        layout.addWidget(log_label)

        self._log_panel = QTextEdit(panel)
        self._log_panel.setReadOnly(True)
        self._log_panel.setMaximumHeight(130)
        self._log_panel.setPlaceholderText("Les messages de debug apparaîtront ici...")
        layout.addWidget(self._log_panel, stretch=0)

        return panel

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text.upper(), None)
        lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8pt; font-weight: 600; "
            f"border-bottom: 1px solid {BORDER}; padding-bottom: 3px; margin-top: 6px;"
        )
        return lbl

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _do_capture(self) -> None:
        self._log(f"Capture demandée...")

        def task() -> np.ndarray:
            return self._vision.capture()

        worker = VisionWorker(task, "capture")
        worker.signals.finished.connect(self._on_capture_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _do_capture_full_screen(self) -> None:
        """Capture l'écran entier (bypass fenêtre Dofus)."""
        self._log("Capture écran entier...")

        def task() -> np.ndarray:
            import mss  # noqa: PLC0415
            import numpy as np  # noqa: PLC0415
            import cv2  # noqa: PLC0415
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # écran primaire
                raw = sct.grab(monitor)
            bgra = np.array(raw)
            return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

        worker = VisionWorker(task, "capture_full")
        worker.signals.finished.connect(self._on_capture_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _do_test_combat_detection(self) -> None:
        """Lance la détection combat (perso + ennemis) et sauvegarde une image annotée."""
        self._activate_dofus_window()
        self._log("🎯 Test détection combat en cours...")

        def task() -> dict:
            from src.services.combat_state_reader import CombatStateReader  # noqa: PLC0415
            reader = CombatStateReader(self._vision)
            snap = reader.read()
            dump_path = reader.debug_dump()
            return {
                "perso": snap.perso,
                "ennemis": snap.ennemis,
                "allies": snap.allies,
                "pa": snap.pa_restants,
                "pm": snap.pm_restants,
                "hp": snap.hp_perso,
                "hp_max": snap.hp_perso_max,
                "dump_path": str(dump_path) if dump_path else None,
            }

        worker = VisionWorker(task, "combat_detection")
        worker.signals.finished.connect(self._on_combat_detection_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _on_combat_detection_done(self, result: object, elapsed: float, _op: str) -> None:
        if not isinstance(result, dict):
            self._log("⚠ Résultat détection invalide")
            return
        perso = result.get("perso")
        ennemis = result.get("ennemis") or []
        allies = result.get("allies") or []
        pa, pm = result.get("pa"), result.get("pm")
        hp, hp_max = result.get("hp"), result.get("hp_max")
        dump = result.get("dump_path")

        self._log(f"✓ Détection en {elapsed*1000:.0f} ms")
        self._log(f"   Perso (rouge) : {'OUI @ ' + str(perso.pos) if perso else 'NON DÉTECTÉ'}")
        self._log(f"   Ennemis (bleus) : {len(ennemis)}")
        for i, e in enumerate(ennemis, 1):
            self._log(f"     #{i} @ ({e.x}, {e.y}) rayon={e.radius}")
        if allies:
            self._log(f"   Alliés (verts) : {len(allies)}")
        self._log(f"   PA={pa} PM={pm} HP={hp}/{hp_max}")
        if dump:
            self._log(f"   Image annotée : {dump}")
            self._log("   → Ouvre-la pour voir ce que le bot voit")

    def _on_capture_done(self, result: object, elapsed: float, _op: str) -> None:
        frame = result  # np.ndarray
        if not isinstance(frame, np.ndarray):
            return
        self._last_frame = frame
        self._display_frame(frame)
        h, w = frame.shape[:2]
        self._log(f"Capture OK {w}x{h} en {elapsed:.0f} ms", color=ACCENT_GREEN)
        # Sync la checkbox si la vision a auto-activé le mode écran entier
        self._sync_fullscreen_checkbox()

    def _display_frame(self, frame: np.ndarray) -> None:
        """Convert BGR ndarray → QPixmap and push to image viewer."""
        import cv2  # noqa: PLC0415

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self._image_viewer.set_pixmap(pixmap)

    def _display_frame_with_boxes(
        self, frame: np.ndarray, detections: list["DetectedObject"]
    ) -> None:
        """Draw bounding boxes on a copy of frame, then display."""
        import cv2  # noqa: PLC0415

        vis = frame.copy()
        for det in detections:
            b = det.box
            cv2.rectangle(vis, (b.x, b.y), (b.x + b.w, b.y + b.h), (79, 195, 247), 2)
            label = f"{det.label} {det.confidence:.2f}"
            cv2.putText(vis, label, (b.x, b.y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (79, 195, 247), 1)
        self._display_frame(vis)

    # ------------------------------------------------------------------
    # Detections
    # ------------------------------------------------------------------

    def _do_detect_color_shape(self) -> None:
        if self._last_frame is None:
            self._log("Aucune capture disponible — cliquez d'abord sur 'Capturer'.", color=ACCENT_RED)
            return
        frame = self._last_frame.copy()
        self._log("Détection ColorShape...")

        def task() -> list:
            return self._vision.color_shape.detect(frame)

        worker = VisionWorker(task, "color_shape")
        worker.signals.finished.connect(self._on_detection_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _do_detect_template(self) -> None:
        if self._last_frame is None:
            self._log("Aucune capture disponible — cliquez d'abord sur 'Capturer'.", color=ACCENT_RED)
            return
        frame = self._last_frame.copy()
        self._log("Détection Template matching...")

        def task() -> list:
            return self._vision.template_matching.detect(frame)

        worker = VisionWorker(task, "template")
        worker.signals.finished.connect(self._on_detection_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _do_detect_yolo(self) -> None:
        if self._last_frame is None or not (hasattr(self._vision, "yolo") and self._vision.yolo):
            return
        frame = self._last_frame.copy()
        self._log("Détection YOLO...")

        def task() -> list:
            return self._vision.yolo.detect(frame)  # type: ignore[union-attr]

        worker = VisionWorker(task, "yolo")
        worker.signals.finished.connect(self._on_detection_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _on_detection_done(self, result: object, elapsed: float, op: str) -> None:
        detections = result  # list[DetectedObject]
        if not isinstance(detections, list):
            return
        self._populate_table(detections)
        if self._last_frame is not None:
            self._display_frame_with_boxes(self._last_frame, detections)
        self._log(
            f"[{op}] {len(detections)} objet(s) détecté(s) en {elapsed:.0f} ms",
            color=ACCENT_GREEN,
        )

    # ------------------------------------------------------------------
    # OCR zone
    # ------------------------------------------------------------------

    def _arm_ocr_selection(self) -> None:
        if self._last_frame is None:
            self._log("Capturez d'abord une image.", color=ACCENT_RED)
            return
        self._log("Dessinez un rectangle sur l'image pour sélectionner la zone OCR.")
        self._ocr_pending = True
        # Swap image viewer for selectable label
        self._enable_selection_mode()

    def _enable_selection_mode(self) -> None:
        """Replace the ImageViewer with a SelectableImageLabel temporarily."""
        if self._last_frame is None:
            return
        import cv2  # noqa: PLC0415

        frame = self._last_frame
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        viewer_widget = self._image_viewer.widget()
        if isinstance(viewer_widget, QLabel):
            sel = SelectableImageLabel(self._image_viewer)
            sel.setPixmap(
                pixmap.scaled(
                    self._image_viewer.width() - 4,
                    self._image_viewer.height() - 4,
                    Qt.AspectRatioMode.KeepAspectRatio,
                )
            )
            dw = self._image_viewer.width() - 4
            dh = int(dw * h / max(w, 1))
            sel.set_pixmap_info(w, h, dw, dh)
            sel.region_selected.connect(self._on_ocr_region_selected)
            self._image_viewer.setWidget(sel)

    def _on_ocr_region_selected(self, x: int, y: int, w: int, h: int) -> None:
        if not self._ocr_pending or self._last_frame is None:
            return
        self._ocr_pending = False
        # Restore normal viewer
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_viewer.setWidget(lbl)
        self._image_viewer.setWidgetResizable(True)
        self._display_frame(self._last_frame)

        frame = self._last_frame
        region_frame = frame[y : y + h, x : x + w]
        if region_frame.size == 0:
            self._log("Région trop petite.", color=ACCENT_RED)
            return

        self._log(f"OCR zone ({x},{y},{w},{h})...")

        def task() -> str:
            from src.models.detection import Region  # noqa: PLC0415

            r = Region(x=x, y=y, w=w, h=h)
            return self._vision.read_text(frame, region=r)

        worker = VisionWorker(task, "ocr")
        worker.signals.finished.connect(self._on_ocr_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _on_ocr_done(self, result: object, elapsed: float, _op: str) -> None:
        text = str(result) if result else "(vide)"
        self._ocr_result_label.setText(f"OCR : « {text} »")
        self._log(f"OCR terminé en {elapsed:.0f} ms : {text!r}", color=ACCENT_BLUE)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _do_save_capture(self) -> None:
        if self._last_frame is None:
            QMessageBox.information(self, "Pas de capture", "Capturez d'abord une image.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Sauvegarder la capture", "capture.png", "PNG (*.png)"
        )
        if not path:
            return
        import cv2  # noqa: PLC0415

        cv2.imwrite(path, self._last_frame)
        self._log(f"Image sauvegardée : {path}", color=ACCENT_GREEN)

    def _do_export_json(self) -> None:
        rows = self._table.rowCount()
        if rows == 0:
            QMessageBox.information(self, "Pas de données", "Lancez d'abord une détection.")
            return
        data: list[dict[str, object]] = []
        for r in range(rows):
            data.append({
                "label": self._table.item(r, 0).text() if self._table.item(r, 0) else "",
                "x": self._table.item(r, 1).text() if self._table.item(r, 1) else "",
                "y": self._table.item(r, 2).text() if self._table.item(r, 2) else "",
                "w": self._table.item(r, 3).text() if self._table.item(r, 3) else "",
                "h": self._table.item(r, 4).text() if self._table.item(r, 4) else "",
                "confidence": self._table.item(r, 5).text() if self._table.item(r, 5) else "",
            })
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les détections", "detections.json", "JSON (*.json)"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._log(f"Détections exportées : {path}", color=ACCENT_GREEN)

    # ------------------------------------------------------------------
    # Windows list
    # ------------------------------------------------------------------

    def _refresh_windows(self) -> None:
        self._list_windows.clear()
        try:
            import pygetwindow as gw  # noqa: PLC0415

            titles = [t for t in gw.getAllTitles() if t.strip()]
            for title in sorted(set(titles)):
                self._list_windows.addItem(title)
            self._log(f"{len(titles)} fenêtres détectées.")
        except Exception as exc:  # noqa: BLE001
            self._list_windows.addItem("(pygetwindow non disponible)")
            self._log(f"Erreur refresh fenêtres : {exc}", color=ACCENT_RED)

    def _on_window_title_edited(self) -> None:
        """Champ 'Fenêtre cible' validé → applique à la vision."""
        title = self._edit_window_title.text().strip()
        try:
            if title:
                self._vision.set_target_window(title)
                self._log(f"Fenêtre cible = '{title}'", color=ACCENT_GREEN)
            else:
                self._vision.set_target_window(None)
                self._log("Cible vidée → écran primaire complet", color=ACCENT_BLUE)
        except Exception as exc:
            self._log(f"Échec cible : {exc}", color=ACCENT_RED)

    def _format_calibration_status(self) -> str:
        try:
            from src.services.hsv_learner import HsvLearner  # noqa: PLC0415
            learner = HsvLearner()
            learned = learner.all_learned()
            if not learned:
                return "Aucune ressource calibrée. Clique le bouton vert ci-dessus."
            lines = [f"{len(learned)} calibrée(s) :"]
            for rid, hsv in list(learned.items())[:6]:
                lines.append(f"  • {rid} — H={hsv.h} S={hsv.s} V={hsv.v} ({hsv.samples} échant.)")
            if len(learned) > 6:
                lines.append(f"  … et {len(learned) - 6} autres")
            return "\n".join(lines)
        except Exception:
            return ""

    def _format_template_status(self) -> str:
        try:
            from src.services.template_matcher import TemplateMatcher  # noqa: PLC0415
            matcher = TemplateMatcher()
            ids = matcher.list_templates()
            if not ids:
                return "Aucun template. Survole une ressource en jeu puis clique le bouton violet."
            return f"{len(ids)} template(s) : {', '.join(ids[:6])}"
        except Exception:
            return ""

    def _on_capture_template(self) -> None:
        """Countdown 5s, capture un crop 120×120 autour du curseur, sauve comme template."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415

        from src.data.catalog import get_catalog  # noqa: PLC0415

        catalog = get_catalog()
        all_ids = sorted([r.id for r in catalog.resources])
        res_id, ok = QInputDialog.getItem(
            self, "Capturer un template",
            "Choisis la ressource, puis pendant 5 s survole la souris pile SUR la ressource dans le jeu :",
            all_ids, 0, True,
        )
        if not ok or not res_id:
            return

        # Bascule immédiate sur Dofus
        self._activate_dofus_window()

        self._btn_capture_template.setEnabled(False)
        self._log(
            f"📷 Template '{res_id}' : survole une ressource, capture dans 5 s...",
            color=ACCENT_BLUE,
        )

        size_px = self._spin_template_size.value()

        def _do_capture():
            try:
                from src.services.template_matcher import TemplateMatcher  # noqa: PLC0415
                matcher = TemplateMatcher()
                info = matcher.capture_template_around_cursor(res_id, size_px=size_px)
                if info is None:
                    self._log("Échec de la capture (position invalide)", color=ACCENT_RED)
                    return
                self._log(
                    f"✓ Template '{res_id}' sauvegardé ({info.width}×{info.height} px)",
                    color=ACCENT_GREEN,
                )
                self._template_status.setText(self._format_template_status())
            except Exception as exc:
                self._log(f"Erreur capture template : {exc}", color=ACCENT_RED)
            finally:
                self._btn_capture_template.setEnabled(True)

        _QT.singleShot(5000, _do_capture)

    def _on_clear_calibration(self) -> None:
        """Supprime une calibration existante (ex: calibrage accidentel)."""
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415

        from src.services.hsv_learner import HsvLearner  # noqa: PLC0415

        learner = HsvLearner()
        learned = learner.all_learned()
        if not learned:
            self._log("Aucune calibration à effacer.", color=ACCENT_BLUE)
            return
        items = ["TOUT EFFACER"] + sorted(learned.keys())
        choice, ok = QInputDialog.getItem(
            self, "Effacer une calibration",
            "Quelle calibration supprimer ?", items, 0, False,
        )
        if not ok:
            return
        if choice == "TOUT EFFACER":
            learner.clear()
            self._log("🗑 Toutes les calibrations effacées.", color=ACCENT_GREEN)
        else:
            learner.clear(choice)
            self._log(f"🗑 Calibration '{choice}' effacée.", color=ACCENT_GREEN)
        self._calibration_status.setText(self._format_calibration_status())

    def _on_calibrate_resource(self) -> None:
        """Dialogue : demande le nom de la ressource puis échantillonne HSV sous le curseur après 5s."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415

        from src.data.catalog import get_catalog  # noqa: PLC0415

        # Propose la liste des ressources du catalogue
        catalog = get_catalog()
        all_ids = sorted([r.id for r in catalog.resources])
        res_id, ok = QInputDialog.getItem(
            self, "Calibrer une ressource",
            "Choisis la ressource à calibrer (tu vas survoler un exemplaire en jeu) :",
            all_ids, 0, True,
        )
        if not ok or not res_id:
            return

        # Bascule immédiate sur Dofus
        self._activate_dofus_window()

        self._btn_calibrate.setEnabled(False)
        self._log(
            f"⏱ Calibrage '{res_id}' : va survoler un exemplaire dans le jeu. 5 s...",
            color=ACCENT_BLUE,
        )

        def _do_calibrate():
            try:
                from src.services.hsv_learner import HsvLearner  # noqa: PLC0415
                learner = HsvLearner()
                sample = learner.sample_around_cursor(radius_px=18)
                if sample is None:
                    self._log("Échec du sampling — position souris invalide.", color=ACCENT_RED)
                    return
                learner.save(res_id, sample, merge=True)
                self._log(
                    f"✓ '{res_id}' calibré : H={sample.h} S={sample.s} V={sample.v} "
                    f"tol={sample.tolerance} (survole à nouveau pour affiner)",
                    color=ACCENT_GREEN,
                )
                self._calibration_status.setText(self._format_calibration_status())
            except Exception as exc:
                self._log(f"Erreur calibrage : {exc}", color=ACCENT_RED)
            finally:
                self._btn_calibrate.setEnabled(True)

        _QT.singleShot(5000, _do_calibrate)

    def _on_send_chat_command(self) -> None:
        """Focus Dofus puis envoie la commande chat après 3 s."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        cmd = self._edit_chat_cmd.text().strip()
        if not cmd:
            self._log("Commande vide.", color=ACCENT_RED)
            return
        title = self._edit_window_title.text().strip()
        if not title:
            self._log("Renseigne d'abord la fenêtre cible.", color=ACCENT_RED)
            return

        # Bascule immédiate sur Dofus — l'utilisateur voit le jeu pendant le countdown
        self._activate_dofus_window(title)

        self._btn_send_chat.setEnabled(False)
        self._log(f"⏱ Envoi '{cmd}' dans 3 s (focus Dofus)...", color=ACCENT_BLUE)

        def _do_send():
            try:
                import pygetwindow as gw  # noqa: PLC0415
                matches = gw.getWindowsWithTitle(title)
                if not matches:
                    self._log(f"Fenêtre '{title}' introuvable.", color=ACCENT_RED)
                    return
                w = matches[0]
                if w.isMinimized:
                    w.restore()
                try:
                    w.activate()
                except Exception:
                    pass
                time.sleep(0.4)

                from src.services.chat_service import ChatService  # noqa: PLC0415
                from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415

                # Clic sur le centre de la fenêtre Dofus (focus clavier) puis Espace + commande
                click_x = w.left + w.width // 2
                click_y = w.top + w.height // 2

                input_svc = PyAutoGuiInputService(humanize=True)
                chat = ChatService(input_svc)
                chat.send_command(cmd, click_at=(click_x, click_y))
                self._log(f"✓ '{cmd}' envoyé (clic ({click_x},{click_y}) + Espace + texte)", color=ACCENT_GREEN)
            except Exception as exc:
                self._log(f"Erreur envoi : {exc}", color=ACCENT_RED)
            finally:
                self._btn_send_chat.setEnabled(True)

        _QT.singleShot(3000, _do_send)

    def _on_read_map_coords(self) -> None:
        """OCR la bannière top-left pour récupérer la position actuelle."""
        # Bascule sur Dofus pour que la capture soit bien celle du jeu
        self._activate_dofus_window()
        self._log("Lecture coords map...", color=ACCENT_BLUE)

        def task():
            from src.services.map_locator import MapLocator  # noqa: PLC0415
            # Log callback pour voir le raw OCR si parsing échoue
            log_cb = lambda msg, lvl: self._log(msg, color=ACCENT_RED if lvl in ("error", "warn") else ACCENT_BLUE)
            loc = MapLocator(self._vision, log_callback=log_cb)
            return loc.locate()

        worker = VisionWorker(task, "map_locate")
        worker.signals.finished.connect(self._on_map_info_done)
        worker.signals.error.connect(self._on_worker_error)
        self._pool.start(worker)

    def _on_map_info_done(self, result: object, elapsed: float, _op: str) -> None:
        info = result
        if info is None:
            self._map_info_label.setText("Carte : <span style='color:#ff7043'>OCR échoué</span>")
            self._log(f"OCR map échoué en {elapsed:.0f} ms", color=ACCENT_RED)
            return
        # MapInfo
        if not info.is_valid:
            raw = info.raw_ocr.replace("\n", " | ")[:120]
            self._map_info_label.setText(
                f"Carte : <span style='color:#ff7043'>coords non parsées</span> — "
                f"<i>raw: {raw}</i>"
            )
            self._log(f"OCR map sans coords valides ({elapsed:.0f} ms) — raw='{raw}'", color=ACCENT_RED)
            return
        region = info.region or "?"
        name = info.name or ""
        lvl = f" [niv {info.level}]" if info.level else ""
        name_part = f" — {name}" if name else ""
        self._map_info_label.setText(
            f"Carte : <b style='color:#66bb6a'>{region}{name_part}</b> "
            f"<span style='color:#4fc3f7'>({info.x},{info.y})</span>{lvl}"
        )
        self._log(
            f"✓ Map : {region}{name_part} ({info.x},{info.y}){lvl} en {elapsed:.0f} ms",
            color=ACCENT_GREEN,
        )

    def _on_test_teleport(self) -> None:
        """Flow complet de test : ouvre zaap, tape la query, double-clic, attend le loading.

        Exécuté sur un thread worker (sinon ça gèle l'UI pendant 25 s et Windows
        considère l'app comme "not responding" → crash).
        """
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        query = self._edit_zaap_dest.text().strip()
        if not query:
            self._log("Destination vide.", color=ACCENT_RED)
            return
        title = self._edit_window_title.text().strip()
        if not title:
            self._log("Renseigne d'abord la fenêtre cible.", color=ACCENT_RED)
            return

        # Bascule immédiate sur Dofus
        self._activate_dofus_window(title)

        self._btn_teleport.setEnabled(False)
        self._log(f"⏱ Téléportation vers '{query}' dans 5 s (focus Dofus)...", color=ACCENT_BLUE)

        def _start_worker():
            """Lance le flow sur un QRunnable pour ne pas bloquer l'UI."""
            def task():
                from src.services.chat_service import ChatService  # noqa: PLC0415
                from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415
                from src.services.map_locator import MapLocator  # noqa: PLC0415
                from src.services.zaap_service import ZaapService  # noqa: PLC0415

                input_svc = PyAutoGuiInputService(humanize=True)
                chat = ChatService(input_svc)
                log_cb = lambda msg, lvl: self._log(msg, color=ACCENT_RED if lvl in ("error", "warn") else ACCENT_BLUE)
                locator = MapLocator(self._vision, log_callback=log_cb)
                zaap = ZaapService(
                    vision=self._vision,
                    input_svc=input_svc,
                    chat_svc=chat,
                    map_locator=locator,
                    window_title=title,
                )
                return zaap.teleport_to(query)

            worker = VisionWorker(task, "teleport")
            worker.signals.finished.connect(self._on_teleport_done)
            worker.signals.error.connect(self._on_teleport_error)
            self._pool.start(worker)

        _QT.singleShot(5000, _start_worker)

    def _on_teleport_done(self, result: object, elapsed: float, _op: str) -> None:
        """Callback quand le worker teleport retourne."""
        # result = ZaapResult
        try:
            success = getattr(result, "success", False)
            if success:
                after = getattr(result, "after_map", None)
                self._log(f"✓ Téléportation réussie en {elapsed:.0f} ms → {after}", color=ACCENT_GREEN)
            else:
                outcome = getattr(result, "outcome", None)
                outcome_val = outcome.value if outcome else "?"
                message = getattr(result, "message", "")
                self._log(
                    f"✗ Téléportation échouée ({outcome_val}) : {message}",
                    color=ACCENT_RED,
                )
                before = getattr(result, "before_map", None)
                after = getattr(result, "after_map", None)
                if before or after:
                    self._log(f"  Avant : {before} | Après : {after}", color=TEXT_SECONDARY)
        finally:
            self._btn_teleport.setEnabled(True)

    def _on_teleport_error(self, error_msg: str) -> None:
        self._log(f"Erreur téléportation : {error_msg}", color=ACCENT_RED)
        self._btn_teleport.setEnabled(True)

    # ------------------------------------------------------------------
    # Navigation map-par-map : clics de bord
    # ------------------------------------------------------------------

    def _on_test_edge_click(self, direction: str) -> None:
        """Focus Dofus puis clique sur le bord demandé après 3 s."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        title = self._edit_window_title.text().strip()
        if not title:
            self._log("Renseigne d'abord la fenêtre cible.", color=ACCENT_RED)
            return

        # Bascule immédiate sur Dofus
        self._activate_dofus_window(title)

        self._log(f"⏱ Clic bord '{direction}' dans 3 s...", color=ACCENT_BLUE)

        def _do_click():
            try:
                import pygetwindow as gw  # noqa: PLC0415
                matches = gw.getWindowsWithTitle(title)
                if not matches:
                    self._log(f"Fenêtre '{title}' introuvable.", color=ACCENT_RED)
                    return
                w = matches[0]
                if w.isMinimized:
                    w.restore()
                try:
                    w.activate()
                except Exception:
                    pass
                time.sleep(0.4)

                from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415
                from src.services.map_locator import MapLocator  # noqa: PLC0415
                from src.services.map_navigator import EdgeRatios, MapNavigator  # noqa: PLC0415
                from src.services.user_prefs import get_user_prefs  # noqa: PLC0415

                input_svc = PyAutoGuiInputService(humanize=True)
                # Callback pour que les logs OCR détaillés remontent dans le journal
                log_cb = lambda msg, lvl: self._log(msg, color=ACCENT_RED if lvl in ("error", "warn") else ACCENT_BLUE)
                locator = MapLocator(self._vision, log_callback=log_cb)
                # Charge les ratios calibrés par l'user si dispos
                gp = get_user_prefs().global_prefs
                edge_ratios = EdgeRatios.from_dict(gp.edge_ratios) if gp.edge_ratios else None
                nav = MapNavigator(
                    vision=self._vision,
                    input_svc=input_svc,
                    map_locator=locator,
                    window_title=title,
                    edge_ratios=edge_ratios,
                    log_callback=log_cb,
                )
                # Capture frame AVANT (pour diff visuelle en plus de l'OCR)
                frame_before = self._vision.capture()
                before = locator.locate()
                before_str = str(before.coords) if before and before.is_valid else "?"

                # Fait le clic directement via _click_edge (accès bypass)
                win = (int(w.left), int(w.top), int(w.width), int(w.height))
                nav._click_edge(direction, win)

                # Attend le changement
                time.sleep(1.8)
                frame_after = self._vision.capture()
                after = locator.locate()
                after_str = str(after.coords) if after and after.is_valid else "?"

                # Détection visuelle : si > 20% des pixels ont changé → la map a clairement changé
                visual_changed = self._frame_differs_significantly(frame_before, frame_after)

                after_ok = after is not None and after.is_valid
                before_ok = before is not None and before.is_valid
                visual_tag = "📷 map visuellement changée" if visual_changed else "📷 même map visuellement"

                if before_ok and after_ok and before.coords != after.coords:
                    self._log(
                        f"✓ Map changée : {before_str} → {after_str} (clic '{direction}' OK, {visual_tag})",
                        color=ACCENT_GREEN,
                    )
                elif visual_changed:
                    # L'image a clairement changé → on considère le clic réussi, même si OCR identique
                    self._log(
                        f"✓ Map visuellement changée (clic '{direction}' OK). "
                        f"OCR avant={before_str} après={after_str} (Tesseract n'a pas su suivre)",
                        color=ACCENT_GREEN,
                    )
                elif before_ok and after_ok and before.coords == after.coords:
                    self._log(
                        f"✗ Pas de changement : {before_str} — le clic '{direction}' n'a rien fait "
                        f"(revois les ratios du bord)",
                        color=ACCENT_RED,
                    )
                else:
                    self._log(
                        f"⚠ Indéterminé. avant={before_str} après={after_str} {visual_tag}",
                        color=ACCENT_RED,
                    )
            except Exception as exc:
                self._log(f"Erreur clic bord : {exc}", color=ACCENT_RED)

        _QT.singleShot(3000, _do_click)

    def _on_test_navigate(self) -> None:
        """Test complet MapNavigator : va aux coords (x, y) demandées."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        raw = self._edit_nav_target.text().strip()
        import re  # noqa: PLC0415
        m = re.match(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$", raw)
        if not m:
            self._log("Coords invalides — format attendu 'x,y' (ex: 9,6)", color=ACCENT_RED)
            return
        target = (int(m.group(1)), int(m.group(2)))

        title = self._edit_window_title.text().strip()
        if not title:
            self._log("Renseigne d'abord la fenêtre cible.", color=ACCENT_RED)
            return

        # Bascule immédiate sur Dofus
        self._activate_dofus_window(title)

        self._btn_navigate.setEnabled(False)
        self._log(f"⏱ Navigation vers {target} dans 5 s...", color=ACCENT_BLUE)

        def _start_worker():
            def task():
                from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415
                from src.services.map_locator import MapLocator  # noqa: PLC0415
                from src.services.map_navigator import MapNavigator  # noqa: PLC0415

                input_svc = PyAutoGuiInputService(humanize=True)
                locator = MapLocator(self._vision, log_callback=lambda msg, lvl: self._log(msg, color=ACCENT_BLUE if lvl == "info" else ACCENT_RED))
                nav = MapNavigator(
                    vision=self._vision,
                    input_svc=input_svc,
                    map_locator=locator,
                    window_title=title,
                    log_callback=lambda msg, lvl: self._log(msg, color=ACCENT_BLUE if lvl == "info" else ACCENT_RED),
                )
                return nav.go_to(target)

            worker = VisionWorker(task, "navigate")
            worker.signals.finished.connect(self._on_navigate_done)
            worker.signals.error.connect(self._on_navigate_error)
            self._pool.start(worker)

        _QT.singleShot(5000, _start_worker)

    def _on_navigate_done(self, result: object, elapsed: float, _op: str) -> None:
        try:
            success = getattr(result, "success", False)
            hops = getattr(result, "hops", 0)
            final = getattr(result, "final_pos", None)
            if success:
                self._log(
                    f"✓ Arrivé à {final} en {hops} hops ({elapsed / 1000:.1f} s)",
                    color=ACCENT_GREEN,
                )
            else:
                outcome = getattr(result, "outcome", None)
                outcome_val = outcome.value if outcome else "?"
                message = getattr(result, "message", "")
                self._log(
                    f"✗ Navigation échouée ({outcome_val}) : {message} | final={final} hops={hops}",
                    color=ACCENT_RED,
                )
        finally:
            self._btn_navigate.setEnabled(True)

    def _on_navigate_error(self, error_msg: str) -> None:
        self._log(f"Erreur navigation : {error_msg}", color=ACCENT_RED)
        self._btn_navigate.setEnabled(True)

    # ------------------------------------------------------------------
    # Calibration des positions de clic bord (via position souris)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Macros : édition pas à pas + sauvegarde + lecture
    # ------------------------------------------------------------------

    def _refresh_macro_combo(self) -> None:
        """Recharge la liste des macros existantes dans le combo."""
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            self._combo_macro.blockSignals(True)
            self._combo_macro.clear()
            macros = get_user_prefs().all_macros()
            for name in sorted(macros.keys()):
                self._combo_macro.addItem(name)
            if "rejoindre_dj" not in macros:
                self._combo_macro.setCurrentText("rejoindre_dj")
            self._combo_macro.blockSignals(False)
        except Exception:
            pass

    def _refresh_macro_steps_view(self) -> None:
        """Recharge l'affichage de la liste des steps de la macro en édition."""
        self._list_macro_steps.clear()
        for i, step in enumerate(self._current_macro_steps, 1):
            d = step.to_dict()
            t = d.get("type")
            if t == "click":
                btn = d.get("button", "left")
                dbl = " x2" if d.get("double") else ""
                txt = f"{i}. Clic {btn}{dbl} ({d['x']},{d['y']}) puis {d.get('delay_ms_after', 0)}ms"
            elif t == "key":
                txt = f"{i}. Touche '{d['key']}' puis {d.get('delay_ms_after', 0)}ms"
            elif t == "wait":
                txt = f"{i}. Pause {d['duration_ms']}ms"
            else:
                txt = f"{i}. ?"
            self._list_macro_steps.addItem(txt)

    def _on_macro_load(self) -> None:
        """Charge la macro sélectionnée dans l'édition."""
        name = self._combo_macro.currentText().strip()
        if not name:
            self._log("Nom de macro vide.", color=ACCENT_RED)
            return
        try:
            from src.services.macro_service import Macro  # noqa: PLC0415
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            d = get_user_prefs().get_macro(name)
            if d is None:
                self._current_macro_steps = []
                self._log(f"Nouvelle macro '{name}' (vide).", color=ACCENT_BLUE)
            else:
                macro = Macro.from_dict(d)
                self._current_macro_steps = list(macro.steps)
                self._log(f"✓ Macro '{name}' chargée ({len(macro.steps)} étapes).", color=ACCENT_GREEN)
            self._refresh_macro_steps_view()
        except Exception as exc:
            self._log(f"Erreur load macro : {exc}", color=ACCENT_RED)

    def _on_macro_delete(self) -> None:
        name = self._combo_macro.currentText().strip()
        if not name:
            return
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            prefs = get_user_prefs()
            if prefs.delete_macro(name):
                prefs.save()
                self._log(f"🗑 Macro '{name}' supprimée.", color=ACCENT_GREEN)
                self._current_macro_steps = []
                self._refresh_macro_steps_view()
                self._refresh_macro_combo()
        except Exception as exc:
            self._log(f"Erreur delete macro : {exc}", color=ACCENT_RED)

    def _on_macro_add_click(self, right: bool = False) -> None:
        """Countdown 5 s, capture la position souris, ajoute un clic à la macro."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        button = "right" if right else "left"
        self._activate_dofus_window()
        self._log(
            f"⏱ Ajouter clic {button} : place ta souris où cliquer (5 s)...",
            color=ACCENT_BLUE,
        )

        def _do_add():
            try:
                import ctypes  # noqa: PLC0415
                from ctypes import wintypes  # noqa: PLC0415
                from src.services.macro_service import ClickStep  # noqa: PLC0415
                point = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                step = ClickStep(x=int(point.x), y=int(point.y), button=button, delay_ms_after=600)
                self._current_macro_steps.append(step)
                self._refresh_macro_steps_view()
                self._log(
                    f"✓ Clic {button} ({point.x},{point.y}) ajouté (step {len(self._current_macro_steps)})",
                    color=ACCENT_GREEN,
                )
            except Exception as exc:
                self._log(f"Erreur add click : {exc}", color=ACCENT_RED)

        _QT.singleShot(5000, _do_add)

    def _on_macro_add_wait(self) -> None:
        """Ajoute une pause fixe à la macro. Dialogue pour choisir la durée."""
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        from src.services.macro_service import WaitStep  # noqa: PLC0415
        ms, ok = QInputDialog.getInt(
            self, "Ajouter une pause",
            "Durée de la pause en millisecondes :",
            1000, 100, 30000, 100,
        )
        if not ok:
            return
        self._current_macro_steps.append(WaitStep(duration_ms=int(ms)))
        self._refresh_macro_steps_view()
        self._log(f"✓ Pause {ms}ms ajoutée (step {len(self._current_macro_steps)})", color=ACCENT_GREEN)

    def _on_macro_clear(self) -> None:
        self._current_macro_steps = []
        self._refresh_macro_steps_view()
        self._log("🗑 Macro vidée (non sauvegardée)", color=ACCENT_BLUE)

    def _on_macro_save(self) -> None:
        name = self._combo_macro.currentText().strip()
        if not name:
            self._log("Nom de macro vide.", color=ACCENT_RED)
            return
        if not self._current_macro_steps:
            self._log("Aucune étape à sauver.", color=ACCENT_RED)
            return
        try:
            from src.services.macro_service import Macro  # noqa: PLC0415
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            macro = Macro(name=name, steps=list(self._current_macro_steps))
            prefs = get_user_prefs()
            prefs.set_macro(name, macro.to_dict())
            prefs.save()
            self._log(
                f"💾 Macro '{name}' sauvée ({len(macro.steps)} étapes)",
                color=ACCENT_GREEN,
            )
            self._refresh_macro_combo()
        except Exception as exc:
            self._log(f"Erreur save macro : {exc}", color=ACCENT_RED)

    def _on_macro_play(self) -> None:
        """Joue la macro en cours d'édition (avec countdown 5s pour basculer sur Dofus)."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        if not self._current_macro_steps:
            self._log("Macro vide — rien à jouer.", color=ACCENT_RED)
            return

        self._activate_dofus_window()
        self._log(
            f"▶ Lecture macro dans 5 s ({len(self._current_macro_steps)} étapes). "
            f"Bascule sur Dofus maintenant...",
            color=ACCENT_BLUE,
        )

        def _do_play():
            def task():
                from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415
                from src.services.macro_service import Macro, MacroPlayer  # noqa: PLC0415
                input_svc = PyAutoGuiInputService(humanize=True)
                log_cb = lambda msg, lvl: self._log(msg, color=ACCENT_RED if lvl in ("error", "warn") else ACCENT_BLUE)
                player = MacroPlayer(input_svc, log_callback=log_cb)
                macro = Macro(name="__temp__", steps=list(self._current_macro_steps))
                return player.play(macro)

            worker = VisionWorker(task, "macro_play")
            worker.signals.finished.connect(lambda r, e, o: self._log(
                f"✓ Macro terminée en {e:.0f}ms" if r else "✗ Macro interrompue",
                color=ACCENT_GREEN if r else ACCENT_RED,
            ))
            worker.signals.error.connect(self._on_worker_error)
            self._pool.start(worker)

        _QT.singleShot(5000, _do_play)

    # ------------------------------------------------------------------
    # Auto-rejoin DJ : scan coords + joue macro automatiquement
    # ------------------------------------------------------------------

    def _on_rejoin_start(self) -> None:
        import re  # noqa: PLC0415
        raw = self._edit_rejoin_coords.text().strip()
        m = re.match(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$", raw)
        if not m:
            self._log("Coords trigger invalides (format x,y).", color=ACCENT_RED)
            return
        trigger = (int(m.group(1)), int(m.group(2)))

        macro_name = self._combo_macro.currentText().strip()
        if not macro_name:
            self._log("Choisis une macro dans le combo d'abord.", color=ACCENT_RED)
            return

        try:
            from src.services.macro_service import Macro  # noqa: PLC0415
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            d = get_user_prefs().get_macro(macro_name)
            if d is None or not d.get("steps"):
                self._log(f"Macro '{macro_name}' vide ou introuvable.", color=ACCENT_RED)
                return
            macro = Macro.from_dict(d)

            from src.services.auto_rejoin_worker import (  # noqa: PLC0415
                AutoRejoinConfig, AutoRejoinWorker,
            )
            from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415

            input_svc = PyAutoGuiInputService(humanize=True)
            cfg = AutoRejoinConfig(
                trigger_coords=trigger,
                macro=macro,
                dofus_window_title=self._edit_window_title.text().strip() or None,
                scan_interval_sec=self._spin_rejoin_scan.value() / 1000.0,
                cooldown_sec=float(self._spin_rejoin_cooldown.value()),
            )
            worker = AutoRejoinWorker(
                vision=self._vision, input_svc=input_svc, config=cfg,
            )
            worker.log_event.connect(
                lambda msg, lvl: self._log(msg, color=ACCENT_RED if lvl in ("error", "warn") else ACCENT_BLUE)
            )
            worker.stopped.connect(self._on_rejoin_stopped)
            self._rejoin_worker = worker
            worker.start()

            self._btn_rejoin_start.setEnabled(False)
            self._btn_rejoin_stop.setEnabled(True)
            self._log(
                f"🔁 Auto-rejoin lancé : trigger={trigger}, macro='{macro_name}'",
                color=ACCENT_GREEN,
            )
        except Exception as exc:
            self._log(f"Erreur lancement auto-rejoin : {exc}", color=ACCENT_RED)

    def _on_rejoin_stop(self) -> None:
        if self._rejoin_worker is not None:
            self._rejoin_worker.request_stop()
            self._log("⏹ Auto-rejoin arrêt demandé...", color=ACCENT_BLUE)

    def _on_rejoin_stopped(self) -> None:
        """Callback quand le worker s'arrête (thread terminé)."""
        self._rejoin_worker = None
        self._btn_rejoin_start.setEnabled(True)
        self._btn_rejoin_stop.setEnabled(False)
        self._log("✓ Auto-rejoin arrêté.", color=ACCENT_GREEN)

    def _on_calibrate_edge(self, direction: str) -> None:
        """Countdown 5s, capture la position actuelle de la souris, enregistre le ratio."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        self._activate_dofus_window()
        self._log(
            f"📍 Calibration '{direction}' : place ta souris où tu veux que le bot clique. "
            f"Capture dans 5 s...",
            color=ACCENT_BLUE,
        )

        def _do_calibrate():
            try:
                import ctypes  # noqa: PLC0415
                from ctypes import wintypes  # noqa: PLC0415
                # Récupère la position souris (pixels physiques)
                point = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                mouse_x, mouse_y = int(point.x), int(point.y)

                # Force une capture pour avoir les vraies dims actuelles
                self._vision.capture()
                region = getattr(self._vision, "last_capture_region", None)
                if region is None or region.w == 0 or region.h == 0:
                    self._log("⚠ Région capture indisponible", color=ACCENT_RED)
                    return

                # Calcule le ratio relatif à la région de capture
                rel_x = mouse_x - region.x
                rel_y = mouse_y - region.y
                ratio_x = rel_x / region.w
                ratio_y = rel_y / region.h

                if not (0.0 <= ratio_x <= 1.0 and 0.0 <= ratio_y <= 1.0):
                    self._log(
                        f"⚠ Souris hors région capture ({mouse_x},{mouse_y} vs {region}) — "
                        f"ratio=({ratio_x:.3f},{ratio_y:.3f}). Vérifie que la capture cible bien Dofus.",
                        color=ACCENT_RED,
                    )
                    return

                # Charge / met à jour / sauve les ratios
                from src.services.map_navigator import EdgeRatios  # noqa: PLC0415
                from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
                prefs = get_user_prefs()
                current = (
                    EdgeRatios.from_dict(prefs.global_prefs.edge_ratios)
                    if prefs.global_prefs.edge_ratios
                    else EdgeRatios()
                )
                if direction == "haut":
                    current.top_x, current.top_y = ratio_x, ratio_y
                elif direction == "bas":
                    current.bottom_x, current.bottom_y = ratio_x, ratio_y
                elif direction == "gauche":
                    current.left_x, current.left_y = ratio_x, ratio_y
                elif direction == "droite":
                    current.right_x, current.right_y = ratio_x, ratio_y
                prefs.global_prefs.edge_ratios = current.to_dict()
                prefs.save()
                self._log(
                    f"✓ Bord '{direction}' calibré : souris=({mouse_x},{mouse_y}) → ratio=({ratio_x:.3f}, {ratio_y:.3f})",
                    color=ACCENT_GREEN,
                )
            except Exception as exc:
                self._log(f"Erreur calibration : {exc}", color=ACCENT_RED)

        _QT.singleShot(5000, _do_calibrate)

    def _on_test_click(self) -> None:
        """Test manuel : focus Dofus + clic au centre de la fenêtre après countdown 3s."""
        from PyQt6.QtCore import QTimer as _QT  # noqa: PLC0415

        title = self._edit_window_title.text().strip()
        if not title:
            self._log("Renseigne d'abord la fenêtre cible.", color=ACCENT_RED)
            return

        # Bascule immédiate sur Dofus pour que l'utilisateur voie le countdown là-bas
        self._activate_dofus_window(title)

        self._btn_test_click.setEnabled(False)
        self._log(f"⏱ Test clic dans 3 s — focus sur '{title}'...", color=ACCENT_BLUE)

        def _do_test():
            try:
                # Focus la fenêtre
                import pygetwindow as gw  # noqa: PLC0415
                matches = gw.getWindowsWithTitle(title)
                if not matches:
                    self._log(f"Fenêtre '{title}' introuvable.", color=ACCENT_RED)
                    return
                w = matches[0]
                if w.isMinimized:
                    w.restore()
                try:
                    w.activate()
                except Exception:
                    pass
                # Pause pour laisser le focus s'appliquer
                time.sleep(0.3)

                # Calcule centre écran
                cx = w.left + w.width // 2
                cy = w.top + w.height // 2

                # Essaie les 3 méthodes et logge laquelle fonctionne
                from src.services.input_service import (  # noqa: PLC0415
                    _win32_click, _win32_get_cursor, get_active_window_title,
                )
                import pyautogui  # noqa: PLC0415

                before = _win32_get_cursor()
                active_before = get_active_window_title()

                # Mouvement + clic via win32 legacy
                ok_win32 = _win32_click(cx, cy, "left")
                after = _win32_get_cursor()
                active_after = get_active_window_title()

                self._log(
                    f"Clic centre ({cx},{cy}) | win32={ok_win32} | "
                    f"pos avant={before} après={after} | "
                    f"fenêtre active avant='{active_before[:30]}' après='{active_after[:30]}'",
                    color=ACCENT_GREEN if ok_win32 else ACCENT_RED,
                )

                if before == after:
                    self._log(
                        "⚠️ La souris n'a PAS bougé — anti-bot actif ou permissions insuffisantes. "
                        "Essaie de lancer le bot en ADMINISTRATEUR.",
                        color=ACCENT_RED,
                    )
                elif active_after != active_before and title in active_after:
                    self._log(f"✓ Dofus était au premier plan au moment du clic", color=ACCENT_GREEN)
                else:
                    self._log(
                        f"⚠️ Dofus a perdu le focus juste après le clic (anti-bot ?)",
                        color=ACCENT_RED,
                    )
            except Exception as exc:
                self._log(f"Erreur test clic : {exc}", color=ACCENT_RED)
            finally:
                self._btn_test_click.setEnabled(True)

        _QT.singleShot(3000, _do_test)

    def _on_window_item_clicked(self, item) -> None:
        """Clic sur une fenêtre dans la liste → cible la vision + focus la fenêtre."""
        title = item.text().strip()
        if not title:
            return
        self._edit_window_title.setText(title)
        try:
            self._vision.set_target_window(title)
            self._log(f"Fenêtre cible mise à jour : {title}", color=ACCENT_GREEN)
        except Exception as exc:
            self._log(f"Échec set_target_window : {exc}", color=ACCENT_RED)
        # Focus la fenêtre pour que l'utilisateur la voie
        try:
            import pygetwindow as gw  # noqa: PLC0415
            matches = gw.getWindowsWithTitle(title)
            if matches:
                w = matches[0]
                if w.isMinimized:
                    w.restore()
                try:
                    w.activate()
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Auto-capture toggle
    # ------------------------------------------------------------------

    def _toggle_auto_capture(self, state: int) -> None:
        if state == Qt.CheckState.Checked.value:
            interval = self._spin_auto.value() * 1000
            self._auto_timer.start(interval)
            self._log(f"Auto-capture activée toutes les {self._spin_auto.value()} s.")
        else:
            self._auto_timer.stop()
            self._log("Auto-capture désactivée.")

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _populate_table(self, detections: list["DetectedObject"]) -> None:
        self._table.setRowCount(0)
        for det in detections:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(det.label))
            self._table.setItem(r, 1, QTableWidgetItem(str(det.box.x)))
            self._table.setItem(r, 2, QTableWidgetItem(str(det.box.y)))
            self._table.setItem(r, 3, QTableWidgetItem(str(det.box.w)))
            self._table.setItem(r, 4, QTableWidgetItem(str(det.box.h)))
            self._table.setItem(r, 5, QTableWidgetItem(f"{det.confidence:.3f}"))

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str, color: str = TEXT_SECONDARY) -> None:
        """Thread-safe : émet un signal qui sera traité sur le thread UI."""
        self._log_signal.emit(msg, color)

    def _do_log_main_thread(self, msg: str, color: str) -> None:
        """Slot sur thread main : fait l'append réel."""
        from datetime import datetime  # noqa: PLC0415
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_panel.append(
            f'<span style="color:{TEXT_SECONDARY}">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>'
        )

    def _on_worker_error(self, error_msg: str) -> None:
        self._log(f"Erreur : {error_msg}", color=ACCENT_RED)
        QMessageBox.warning(self, "Erreur vision", f"Une erreur est survenue :\n{error_msg}")
