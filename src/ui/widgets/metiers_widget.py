"""Onglet Métiers : liste toutes les ressources du catalogue par métier."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.data.catalog import ResourceEntry, get_catalog, traduire_metier


class MetiersWidget(QWidget):
    """Vue catalogue des métiers + filtres niveau/métier."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalog = get_catalog()
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Titre
        title = QLabel("Catalogue des métiers")
        title.setStyleSheet("font-size: 18pt; font-weight: bold; color: #e0e0e0;")
        root.addWidget(title)

        info = QLabel(
            f"<span style='color:#b0b0b0'>"
            f"{len(self._catalog.resources)} ressources — {len(self._catalog.zones)} zones de farm — "
            f"{len(self._catalog.metiers_disponibles())} métiers catalogués."
            f"</span>"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(info)

        # Filtres
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Métier :"))
        self.combo_metier = QComboBox()
        self.combo_metier.addItem("Tous", "")
        for m in self._catalog.metiers_disponibles():
            self.combo_metier.addItem(traduire_metier(m), m)
        self.combo_metier.currentIndexChanged.connect(self._populate)
        filters.addWidget(self.combo_metier)

        filters.addSpacing(20)
        filters.addWidget(QLabel("Niveau perso max :"))
        self.spin_niveau = QSpinBox()
        self.spin_niveau.setRange(1, 200)
        self.spin_niveau.setValue(200)
        self.spin_niveau.valueChanged.connect(self._populate)
        filters.addWidget(self.spin_niveau)

        filters.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #4fc3f7; font-weight: bold;")
        filters.addWidget(self.count_label)
        root.addLayout(filters)

        # Tableau
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Nom", "Métier", "Niveau", "Famille", "Respawn", "Notes"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self.table, stretch=1)

        # Legende calibration
        legend = QLabel(
            "💡 <b>Astuce :</b> les valeurs HSV du catalogue sont des estimations. "
            "Clique sur une ressource en jeu pour que le bot apprenne sa couleur réelle "
            "(après 3 clics validés, la détection devient fiable)."
        )
        legend.setWordWrap(True)
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setStyleSheet("color: #b0b0b0; padding: 6px; border-left: 3px solid #4fc3f7;")
        root.addWidget(legend)

    def _populate(self) -> None:
        metier_filter = self.combo_metier.currentData() or ""
        niveau_max = self.spin_niveau.value()

        items: list[ResourceEntry]
        if metier_filter:
            items = self._catalog.by_niveau_max(metier_filter, niveau_max)
        else:
            items = [r for r in self._catalog.resources if r.niveau_requis <= niveau_max]

        self.table.setRowCount(len(items))
        for row, res in enumerate(items):
            self.table.setItem(row, 0, QTableWidgetItem(res.nom_fr))
            self.table.setItem(row, 1, QTableWidgetItem(traduire_metier(res.metier)))
            self.table.setItem(row, 2, QTableWidgetItem(str(res.niveau_requis)))
            self.table.setItem(row, 3, QTableWidgetItem(res.famille))
            self.table.setItem(row, 4, QTableWidgetItem(f"{res.respawn}s"))
            notes_item = QTableWidgetItem(res.notes)
            notes_item.setForeground(QColor("#909090"))
            self.table.setItem(row, 5, notes_item)

        self.count_label.setText(f"{len(items)} ressource(s)")
