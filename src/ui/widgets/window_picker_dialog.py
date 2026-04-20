"""Dialogue Qt pour sélectionner la fenêtre Dofus parmi les candidates."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.services.window_detector import DofusWindow, SmartWindowDetector


class WindowPickerDialog(QDialog):
    """Modal de sélection de la fenêtre Dofus (si plusieurs candidates).

    Exemple :
        dlg = WindowPickerDialog(settings.dofus_window_title, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            window = dlg.selected_window
    """

    selection_changed = pyqtSignal(object)  # DofusWindow

    def __init__(self, configured_title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sélection de la fenêtre Dofus")
        self.setMinimumSize(780, 420)
        self._detector = SmartWindowDetector(configured_title=configured_title)
        self._windows: list[DofusWindow] = []
        self._selected: DofusWindow | None = None

        self._build_ui()
        self.refresh()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QLabel("<b>Plusieurs fenêtres peuvent correspondre à Dofus. Sélectionne la bonne :</b>")
        header.setWordWrap(True)
        root.addWidget(header)

        split = QHBoxLayout()
        root.addLayout(split, stretch=1)

        # Gauche : liste
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        split.addWidget(self.list_widget, stretch=1)

        # Droite : détails
        self.details_panel = QVBoxLayout()
        self.details_title = QLabel("—")
        self.details_title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self.details_title.setWordWrap(True)
        self.details_panel.addWidget(self.details_title)

        self.details_info = QLabel("—")
        self.details_info.setStyleSheet("color: #b0b0b0;")
        self.details_info.setWordWrap(True)
        self.details_info.setTextFormat(Qt.TextFormat.RichText)
        self.details_panel.addWidget(self.details_info)

        self.details_reasons = QLabel("")
        self.details_reasons.setWordWrap(True)
        self.details_reasons.setStyleSheet("color: #66bb6a; font-size: 10pt;")
        self.details_panel.addWidget(self.details_reasons)

        self.details_panel.addStretch(1)
        details_widget = QWidget()
        details_widget.setLayout(self.details_panel)
        split.addWidget(details_widget, stretch=1)

        # Boutons
        actions = QHBoxLayout()
        self.btn_refresh = QPushButton("Actualiser")
        self.btn_refresh.clicked.connect(self.refresh)
        actions.addWidget(self.btn_refresh)
        actions.addStretch(1)
        self.btn_cancel = QPushButton("Annuler")
        self.btn_cancel.clicked.connect(self.reject)
        actions.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton("Utiliser cette fenêtre")
        self.btn_ok.setDefault(True)
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._confirm)
        actions.addWidget(self.btn_ok)
        root.addLayout(actions)

    # ---------- Logique ----------

    def refresh(self) -> None:
        self._windows = self._detector.scan()
        self.list_widget.clear()
        for w in self._windows:
            item = QListWidgetItem()
            text = f"[{w.score:.0f}] {w.title}\n     {w.width}×{w.height}" + ("   (active)" if w.is_active else "")
            item.setText(text)
            item.setData(Qt.ItemDataRole.UserRole, w)
            self.list_widget.addItem(item)

        if self._windows:
            # Sélectionne la meilleure par défaut
            self.list_widget.setCurrentRow(0)
        else:
            self.details_title.setText("Aucune fenêtre Dofus détectée")
            self.details_info.setText(
                "Vérifie que :<br>"
                "• Dofus est bien <b>lancé</b><br>"
                "• Il est en mode <b>fenêtré</b> (pas plein écran exclusif)<br>"
                "• Le titre de la fenêtre contient « Dofus »"
            )

    def _on_selection_changed(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            self._selected = None
            self.btn_ok.setEnabled(False)
            return
        w: DofusWindow = current.data(Qt.ItemDataRole.UserRole)
        self._selected = w
        self.btn_ok.setEnabled(True)
        self.details_title.setText(w.title)
        self.details_info.setText(
            f"<b>Position :</b> ({w.left}, {w.top})<br>"
            f"<b>Taille :</b> {w.width} × {w.height} (ratio {w.ratio:.2f})<br>"
            f"<b>Score :</b> {w.score:.1f} / 100<br>"
            f"<b>Active :</b> {'oui' if w.is_active else 'non'}"
        )
        self.details_reasons.setText("✓ " + "\n✓ ".join(w.reasons) if w.reasons else "")
        self.selection_changed.emit(w)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Focus la fenêtre au simple clic pour que l'utilisateur la voie."""
        w: DofusWindow | None = item.data(Qt.ItemDataRole.UserRole)
        if w is not None:
            w.focus()

    def _confirm(self) -> None:
        if self._selected is not None:
            self.accept()

    # ---------- API publique ----------

    @property
    def selected_window(self) -> DofusWindow | None:
        return self._selected
