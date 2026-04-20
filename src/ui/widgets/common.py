"""Reusable UI components: StatCard, StateIndicator, ImageViewer, SparklineWidget."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.styles import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Card container
# ---------------------------------------------------------------------------


def make_card(parent: QWidget | None = None) -> QFrame:
    """Return a styled card QFrame with QObjectName 'card'."""
    frame = QFrame(parent)
    frame.setObjectName("card")
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    return frame


# ---------------------------------------------------------------------------
# StateIndicator
# ---------------------------------------------------------------------------

_STATE_STYLES: dict[str, tuple[str, str, str]] = {
    # state_name: (bg_color, text_color, icon_char)
    "IDLE":       ("#333344", TEXT_SECONDARY, "●"),
    "RUNNING":    ("#1e3a1e", ACCENT_GREEN,   "▶"),
    "STARTING":   ("#1e3a1e", ACCENT_GREEN,   "▶"),
    "SCANNING":   ("#1e3a1e", ACCENT_GREEN,   "◎"),
    "MOVING":     ("#1e3a1e", ACCENT_GREEN,   "➤"),
    "ACTING":     ("#1e3a1e", ACCENT_GREEN,   "⚡"),
    "COMBAT":     ("#3a1e1e", ACCENT_RED,     "⚔"),
    "PAUSED":     ("#3a2e10", ACCENT_ORANGE,  "⏸"),
    "ERROR":      ("#3a1e1e", ACCENT_RED,     "✖"),
    "RECONNECTING": ("#2a2a3a", ACCENT_BLUE,  "↻"),
    "CALIBRATING": ("#2a2a3a", ACCENT_BLUE,   "◈"),
    "BANKING":    ("#1e3a1e", ACCENT_GREEN,   "🏦"),
    "CHECKING_INVENTORY": ("#1e3a1e", ACCENT_GREEN, "📦"),
    "STOPPING":   ("#3a2e10", ACCENT_ORANGE,  "⏹"),
}


class StateIndicator(QFrame):
    """Large state badge: icon + state name + description."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(80)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)

        self._icon_label = QLabel("●", self)
        icon_font = QFont("Segoe UI", 28)
        self._icon_label.setFont(icon_font)
        self._icon_label.setFixedWidth(40)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_widget = QWidget(self)
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self._state_label = QLabel("EN ATTENTE", self)
        state_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        self._state_label.setFont(state_font)

        self._desc_label = QLabel("En attente...", self)
        desc_font = QFont("Segoe UI", 10)
        self._desc_label.setFont(desc_font)
        self._desc_label.setStyleSheet(f"color: {TEXT_SECONDARY};")

        text_layout.addWidget(self._state_label)
        text_layout.addWidget(self._desc_label)

        layout.addWidget(self._icon_label)
        layout.addWidget(text_widget, stretch=1)

        self.set_state("IDLE")

    def set_state(self, state_name: str, description: str = "") -> None:
        bg, color, icon = _STATE_STYLES.get(
            state_name.upper(), ("#333344", TEXT_SECONDARY, "●")
        )
        self.setStyleSheet(
            f"QFrame#card {{ background-color: {bg}; border: 1px solid {color}40; border-radius: 8px; }}"
        )
        self._icon_label.setText(icon)
        self._icon_label.setStyleSheet(f"color: {color}; background-color: transparent;")
        # Traduction FR des états
        libelle_fr = {
            "IDLE": "EN ATTENTE",
            "RUNNING": "EN COURS",
            "STARTING": "DÉMARRAGE",
            "SCANNING": "SCAN",
            "MOVING": "DÉPLACEMENT",
            "ACTING": "ACTION",
            "COMBAT": "COMBAT",
            "PAUSED": "PAUSE",
            "ERROR": "ERREUR",
            "RECONNECTING": "RECONNEXION",
            "CALIBRATING": "CALIBRAGE",
            "BANKING": "BANQUE",
            "STOPPING": "ARRÊT",
            "CHECKING_INVENTORY": "INVENTAIRE",
        }.get(state_name.upper(), state_name.upper())
        self._state_label.setText(libelle_fr)
        self._state_label.setStyleSheet(f"color: {color}; background-color: transparent;")
        if description:
            self._desc_label.setText(description)


# ---------------------------------------------------------------------------
# SparklineWidget (pyqtgraph)
# ---------------------------------------------------------------------------


class SparklineWidget(pg.PlotWidget):
    """Mini line chart showing the last N data points."""

    def __init__(
        self,
        max_points: int = 30,
        color: str = ACCENT_BLUE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._max_points = max_points
        self._data: deque[float] = deque(maxlen=max_points)

        self.setBackground(BG_CARD)
        self.getPlotItem().hideAxis("left")
        self.getPlotItem().hideAxis("bottom")
        self.getPlotItem().setContentsMargins(0, 0, 0, 0)
        self.getPlotItem().hideButtons()
        self.getPlotItem().setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.setMaximumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        r, g, b = self._hex_to_rgb(color)
        pen = pg.mkPen(color=(r, g, b), width=2)
        fill_color = QColor(r, g, b, 40)
        self._curve = self.plot(pen=pen, fillLevel=0, brush=fill_color)

    def push(self, value: float) -> None:
        self._data.append(value)
        y = np.array(list(self._data), dtype=float)
        x = np.arange(len(y), dtype=float)
        self._curve.setData(x=x, y=y)

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ---------------------------------------------------------------------------
# StatCard
# ---------------------------------------------------------------------------


class StatCard(QFrame):
    """Card showing: title + big value + optional sparkline."""

    def __init__(
        self,
        title: str,
        unit: str = "",
        color: str = ACCENT_BLUE,
        with_sparkline: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._unit = unit
        self._color = color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self._title_label = QLabel(title, self)
        self._title_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; font-weight: 600;")

        self._value_label = QLabel("—", self)
        self._value_label.setStyleSheet(
            f"color: {color}; font-size: 22pt; font-weight: 700; background: transparent;"
        )
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)

        self._sparkline: SparklineWidget | None = None
        if with_sparkline:
            self._sparkline = SparklineWidget(max_points=30, color=color, parent=self)
            layout.addWidget(self._sparkline)

    def set_value(self, value: str | int | float, push_history: bool = True) -> None:
        display = f"{value}{' ' + self._unit if self._unit else ''}"
        self._value_label.setText(display)
        if self._sparkline is not None and push_history:
            try:
                self._sparkline.push(float(str(value).replace(" ", "").replace(",", ".")))
            except (ValueError, TypeError):
                pass


# ---------------------------------------------------------------------------
# ImageViewer — QLabel with scroll + zoom
# ---------------------------------------------------------------------------


class ImageViewer(QScrollArea):
    """Zoomable image viewer. Accepts QPixmap via set_pixmap()."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zoom: float = 1.0
        self._pixmap: QPixmap | None = None

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setText("Aucune capture")
        self._label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 13pt;")
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        self.setWidget(self._label)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background-color: {BG_MAIN}; border: 1px solid {BORDER}; border-radius: 6px;")

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._apply_zoom()

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        if event.angleDelta().y() > 0:
            self._zoom = min(self._zoom * 1.15, 6.0)
        else:
            self._zoom = max(self._zoom / 1.15, 0.1)
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self._pixmap is None:
            return
        w = int(self._pixmap.width() * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        scaled = self._pixmap.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._apply_zoom()
