"""Stats tab — session history (placeholder + live session count)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui.styles import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    BG_CARD,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from src.ui.widgets.common import make_card

if TYPE_CHECKING:
    from src.services.persistence import PersistenceService


class StatsWidget(QWidget):
    """Statistics tab: coming soon placeholder + recent sessions table."""

    def __init__(
        self,
        persistence: "PersistenceService",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._persistence = persistence
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # Coming soon card (centered)
        coming_card = make_card(self)
        coming_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        coming_layout = QVBoxLayout(coming_card)
        coming_layout.setContentsMargins(32, 28, 32, 28)
        coming_layout.setSpacing(10)
        coming_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("📊", coming_card)
        icon.setFont(QFont("Segoe UI Emoji", 40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: transparent;")

        title = QLabel("Historique des sessions", coming_card)
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel(
            "Graphiques de progression XP/Kamas par session — bientôt disponible",
            coming_card,
        )
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge = QLabel("Bientôt", coming_card)
        badge.setStyleSheet(
            f"background-color: #1a2a4a; color: {ACCENT_BLUE}; "
            f"border: 1px solid {ACCENT_BLUE}; border-radius: 12px; "
            f"padding: 4px 16px; font-size: 10pt; font-weight: 600;"
        )
        badge.setFixedWidth(100)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        coming_layout.addWidget(icon)
        coming_layout.addWidget(title)
        coming_layout.addWidget(subtitle)
        coming_layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignHCenter)

        root.addWidget(coming_card)

        # Recent sessions table (live data if available)
        sessions_card = make_card(self)
        sessions_layout = QVBoxLayout(sessions_card)
        sessions_layout.setContentsMargins(14, 12, 14, 12)
        sessions_layout.setSpacing(8)

        header_row = QHBoxLayout()
        table_title = QLabel("Sessions récentes", sessions_card)
        table_title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        table_title.setStyleSheet(f"color: {TEXT_PRIMARY};")

        self._sessions_count_label = QLabel("", sessions_card)
        self._sessions_count_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")

        header_row.addWidget(table_title)
        header_row.addStretch()
        header_row.addWidget(self._sessions_count_label)

        self._table = QTableWidget(0, 6, sessions_card)
        self._table.setHorizontalHeaderLabels(
            ["#", "Démarré", "Terminé", "Mode", "Zone/Métier", "Actions"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(False)

        sessions_layout.addLayout(header_row)
        sessions_layout.addWidget(self._table)

        root.addWidget(sessions_card, stretch=1)

        self._load_sessions()

    def _load_sessions(self) -> None:
        """Load recent sessions from persistence if available."""
        try:
            sessions = self._fetch_recent_sessions()
            self._sessions_count_label.setText(f"{len(sessions)} session(s) trouvée(s)")
            self._table.setRowCount(0)
            for row_data in sessions:
                r = self._table.rowCount()
                self._table.insertRow(r)
                for col, val in enumerate(row_data):
                    item = QTableWidgetItem(str(val) if val is not None else "—")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self._table.setItem(r, col, item)
        except Exception:  # noqa: BLE001
            self._sessions_count_label.setText("Données non disponibles")
            self._table.setRowCount(1)
            self._table.insertRow(0)
            msg = QTableWidgetItem("Aucune session enregistrée")
            msg.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(0, 0, msg)
            self._table.setSpan(0, 0, 1, 6)

    def _fetch_recent_sessions(self) -> list[tuple]:
        """Query last 20 sessions from SQLite. Returns list of row tuples."""
        import sqlite3  # noqa: PLC0415

        db_path = self._persistence._db_path  # noqa: SLF001
        if not db_path.exists():
            return []
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                """SELECT id, started_at, ended_at, mode, job_or_zone, total_actions
                   FROM sessions ORDER BY id DESC LIMIT 20"""
            ).fetchall()
        return rows
