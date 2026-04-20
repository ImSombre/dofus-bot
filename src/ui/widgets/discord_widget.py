"""Discord tab — status display when discord is enabled, or disabled state card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
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
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from src.ui.widgets.common import make_card

if TYPE_CHECKING:
    from src.config.settings import Settings


class DiscordWidget(QWidget):
    """Discord integration tab."""

    def __init__(self, settings: "Settings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        if not self._settings.discord_enabled:
            root.addStretch()
            root.addWidget(self._build_disabled_card(), alignment=Qt.AlignmentFlag.AlignCenter)
            root.addStretch()
        else:
            root.addWidget(self._build_enabled_panel())

    def _build_disabled_card(self) -> QFrame:
        card = make_card(self)
        card.setFixedWidth(480)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("🔕", card)
        icon.setFont(QFont("Segoe UI Emoji", 48))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: transparent;")

        title = QLabel("Discord désactivé", card)
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc = QLabel(
            "L'intégration Discord n'est pas activée.\n"
            "Pour l'activer, configure ton fichier .env puis redémarre.",
            card,
        )
        desc.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)

        env_hint = QLabel(
            "DISCORD_ENABLED=true\nDISCORD_TOKEN=ton_token_ici\nDISCORD_GUILD_ID=ton_guild_id",
            card,
        )
        env_hint.setStyleSheet(
            f"background-color: #1a1a2a; color: {ACCENT_BLUE}; "
            f"border: 1px solid {BORDER}; border-radius: 6px; "
            f"padding: 10px 16px; font-family: Consolas, monospace; font-size: 9pt;"
        )
        env_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        btn_open_env = QPushButton("📝  Ouvrir .env dans l'éditeur", card)
        btn_open_env.clicked.connect(self._open_env)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(env_hint)
        layout.addWidget(btn_open_env)

        return card

    def _build_enabled_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Status card
        status_card = make_card(container)
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(16, 12, 16, 12)
        status_layout.setSpacing(16)

        status_icon = QLabel("🟢", status_card)
        status_icon.setFont(QFont("Segoe UI Emoji", 20))
        status_icon.setStyleSheet("background: transparent;")

        status_text = QVBoxLayout()
        status_title = QLabel("Discord activé", status_card)
        status_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        status_title.setStyleSheet(f"color: {ACCENT_GREEN};")

        guild_id = str(self._settings.discord_guild_id or "Non configuré")
        status_detail = QLabel(f"Guild ID : {guild_id}", status_card)
        status_detail.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")

        status_text.addWidget(status_title)
        status_text.addWidget(status_detail)
        status_layout.addWidget(status_icon)
        status_layout.addLayout(status_text)
        status_layout.addStretch()

        layout.addWidget(status_card)

        # Allowed users
        users_card = make_card(container)
        users_layout = QVBoxLayout(users_card)
        users_layout.setContentsMargins(14, 12, 14, 12)
        users_layout.setSpacing(8)

        users_title = QLabel("Utilisateurs autorisés", users_card)
        users_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        users_title.setStyleSheet(f"color: {TEXT_PRIMARY};")

        users_list = QListWidget(users_card)
        users_list.setMaximumHeight(140)
        allowed = self._settings.discord_allowed_user_ids
        if allowed:
            for uid in allowed:
                users_list.addItem(str(uid))
        else:
            users_list.addItem("(aucun utilisateur configuré)")

        users_layout.addWidget(users_title)
        users_layout.addWidget(users_list)
        layout.addWidget(users_card)

        # Test command card
        test_card = make_card(container)
        test_layout = QVBoxLayout(test_card)
        test_layout.setContentsMargins(14, 12, 14, 12)
        test_layout.setSpacing(8)

        test_title = QLabel("Envoyer une commande test", test_card)
        test_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        test_title.setStyleSheet(f"color: {TEXT_PRIMARY};")

        test_note = QLabel(
            "Fonctionnalité disponible quand le service Discord est actif.",
            test_card,
        )
        test_note.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")

        cmd_row = QHBoxLayout()
        self._edit_cmd = QLineEdit(test_card)
        self._edit_cmd.setPlaceholderText("!status / !stop / !stats ...")
        self._edit_cmd.setEnabled(False)

        self._btn_send = QPushButton("Envoyer", test_card)
        self._btn_send.setEnabled(False)
        self._btn_send.setToolTip("Connexion Discord requise")

        cmd_row.addWidget(self._edit_cmd, stretch=1)
        cmd_row.addWidget(self._btn_send)

        test_layout.addWidget(test_title)
        test_layout.addWidget(test_note)
        test_layout.addLayout(cmd_row)
        layout.addWidget(test_card)

        layout.addStretch()
        return container

    def _open_env(self) -> None:
        import os  # noqa: PLC0415

        env_path = ".env"
        if not os.path.exists(env_path):
            with open(env_path, "w") as f:
                f.write("# Dofus Bot configuration\nDISCORD_ENABLED=true\nDISCORD_TOKEN=\n")
        os.startfile(os.path.abspath(env_path))
