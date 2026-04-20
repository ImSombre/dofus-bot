"""Configuration tab — read-only view of settings, .env shortcut."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.styles import (
    ACCENT_BLUE,
    ACCENT_ORANGE,
    BG_CARD,
    BG_INPUT,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from src.ui.widgets.common import make_card

if TYPE_CHECKING:
    from src.config.settings import Settings


class ConfigWidget(QWidget):
    """Read-only view of Settings + link to .env file."""

    def __init__(self, settings: "Settings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header
        header_card = make_card(self)
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel("Configuration", header_card)
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")

        info = QLabel(
            "Les paramètres sont en lecture seule. Édite le fichier .env puis redémarre l'application.",
            header_card,
        )
        info.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
        info.setWordWrap(True)

        self._btn_open_env = QPushButton("📝  Ouvrir .env dans l'éditeur", header_card)
        self._btn_open_env.setFixedWidth(240)
        self._btn_open_env.clicked.connect(self._open_env_file)

        header_layout.addWidget(title)
        header_layout.addWidget(info, stretch=1)
        header_layout.addWidget(self._btn_open_env)

        root.addWidget(header_card)

        # Warning banner
        warning = QLabel(
            "⚠  Édite le fichier .env puis redémarre l'app pour appliquer les changements.",
            self,
        )
        warning.setStyleSheet(
            f"background-color: #3a2e10; color: {ACCENT_ORANGE}; "
            f"border: 1px solid {ACCENT_ORANGE}40; border-radius: 6px; "
            f"padding: 8px 12px; font-size: 10pt;"
        )
        root.addWidget(warning)

        # Scrollable form
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        form_container = QWidget()
        form_layout = QFormLayout(form_container)
        form_layout.setContentsMargins(4, 4, 4, 4)
        form_layout.setSpacing(8)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        settings_dict = self._settings.model_dump(exclude={"discord_token"})
        for key, value in settings_dict.items():
            label = QLabel(key + "  ", form_container)
            label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; font-family: Consolas;")

            val_str = str(value) if value is not None else "(non défini)"
            # Mask sensitive fields
            if "token" in key.lower() or "password" in key.lower():
                val_str = "••••••••" if value else "(vide)"

            field = QLineEdit(val_str, form_container)
            field.setReadOnly(True)
            field.setStyleSheet(
                f"background-color: {BG_INPUT}; color: {TEXT_PRIMARY}; "
                f"border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px; "
                f"font-family: Consolas, monospace; font-size: 10pt;"
            )
            form_layout.addRow(label, field)

        scroll.setWidget(form_container)
        root.addWidget(scroll, stretch=1)

    def _open_env_file(self) -> None:
        env_path = ".env"
        if os.path.exists(env_path):
            os.startfile(os.path.abspath(env_path))
        else:
            # Create an empty .env and open it
            with open(env_path, "w") as f:
                f.write("# Dofus Bot configuration\n")
            os.startfile(os.path.abspath(env_path))
