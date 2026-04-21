"""Dashboard simplifié en mode wizard.

Flow utilisateur :
  1. Page d'accueil : 3 grandes cartes (Farmer / Combattre / Crafter)
  2. Wizard dédié selon le choix (2-3 clics max)
  3. Vue « En cours » avec un gros bouton Arrêter
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from loguru import logger

from src.data.catalog import get_catalog, traduire_metier
from src.handlers.combat import classes_implementees, get_class_combat
from src.handlers.combat.combat_ai import CombatStrategy
from src.handlers.jobs.harvesting import get_runner_class
from src.services.farm_worker import FarmConfig, FarmStats, FarmWorker
from src.services.input_service import PyAutoGuiInputService
from src.services.user_prefs import FarmMetierPrefs, get_user_prefs
from src.services.window_detector import DofusWindow, SmartWindowDetector
from src.ui.widgets.window_picker_dialog import WindowPickerDialog

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.handlers.state_machine import BotStateMachine
    from src.services.vision import MssVisionService


METIER_ICONS = {
    "lumberjack": "🌲",
    "farmer": "🌾",
    "miner": "⛏️",
    "alchemist": "🌿",
    "fisherman": "🎣",
    "hunter": "🏹",
}

CLASSE_ICONS = {
    "iop": "⚔️", "cra": "🏹", "eniripsa": "✨", "ecaflip": "🐱",
    "enutrof": "💰", "sram": "🗡️", "sadida": "🌱", "osamodas": "🐕",
    "feca": "🛡️", "pandawa": "🥋", "roublard": "💣", "zobal": "🎭",
    "steamer": "🤖", "eliotrope": "🔮", "huppermage": "🌀",
    "ouginak": "🐺", "forgelance": "🔨", "xelor": "⏱️",
    "sacrieur": "🩸",
}

# Style commun pour les GroupBox du wizard combat
_GROUPBOX_STYLE = """
QGroupBox {
    font-weight: 600;
    font-size: 12pt;
    color: #e0e0e0;
    border: 1px solid #3a3a4e;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 10px;
    background-color: #1e1e2e;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #4fc3f7;
    left: 12px;
}
"""


# ---------------------------------------------------------------------------
# Composants réutilisables
# ---------------------------------------------------------------------------

def _make_card_button(icon: str, title: str, desc: str, *, disabled: bool = False) -> QPushButton:
    btn = QPushButton()
    btn.setMinimumSize(280, 180)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setText(f"{icon}\n\n{title}\n\n{desc}")
    btn.setStyleSheet(
        "QPushButton {"
        "  background-color: #2a2a3e; border: 2px solid #3a3a4e; border-radius: 12px;"
        "  color: #e0e0e0; font-size: 13pt; padding: 18px; text-align: center;"
        "}"
        "QPushButton:hover { border-color: #4fc3f7; background-color: #2f2f44; }"
        "QPushButton:pressed { background-color: #35355a; }"
        "QPushButton:disabled { color: #707080; border-color: #2a2a3a; }"
    )
    btn.setEnabled(not disabled)
    return btn


def _make_primary_button(text: str, *, kind: str = "primary") -> QPushButton:
    btn = QPushButton(text)
    btn.setMinimumHeight(54)
    btn.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    colors = {
        "primary": ("#4fc3f7", "#1976d2", "#0d47a1"),
        "success": ("#66bb6a", "#388e3c", "#1b5e20"),
        "danger":  ("#ef5350", "#c62828", "#8a0808"),
    }[kind]
    btn.setStyleSheet(
        f"QPushButton {{ background-color: {colors[0]}; color: white; border: none; border-radius: 8px; padding: 8px 24px; }}"
        f"QPushButton:hover {{ background-color: {colors[1]}; }}"
        f"QPushButton:pressed {{ background-color: {colors[2]}; }}"
        "QPushButton:disabled { background-color: #555; color: #aaa; }"
    )
    return btn


# ---------------------------------------------------------------------------
# SimpleDashboardWidget
# ---------------------------------------------------------------------------

class SimpleDashboardWidget(QWidget):
    """Page d'accueil « wizard » pour démarrer un farm / combat en 3 clics."""

    session_started = pyqtSignal(str)  # activité ("farm:lumberjack" / "combat:iop")
    session_stopped = pyqtSignal()

    def __init__(
        self,
        state_machine: "BotStateMachine",
        settings: "Settings",
        vision: "MssVisionService | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sm = state_machine
        self._settings = settings
        self._vision = vision
        self._input = PyAutoGuiInputService(humanize=settings.humanize_clicks)
        self._detector = SmartWindowDetector(configured_title=settings.dofus_window_title)
        self._selected_window: DofusWindow | None = None
        self._active_session_label = ""
        self._farm_worker: FarmWorker | None = None
        self._combat_worker = None
        self._hunt_worker = None
        self._last_farm_stats: FarmStats | None = None
        self._log_lines: list[str] = []

        self._stack = QStackedWidget(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

        self._page_home = self._build_home()
        self._page_farm = self._build_farm_wizard()
        self._page_combat = self._build_combat_wizard()
        self._page_running = self._build_running()

        self._stack.addWidget(self._page_home)     # 0
        self._stack.addWidget(self._page_farm)     # 1
        self._stack.addWidget(self._page_combat)   # 2
        self._stack.addWidget(self._page_running)  # 3

        self._auto_detect_window()

        # Refresh stats running view
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_running_view)

        # Raccourci clavier F1 : arrêt d'urgence du farm
        from PyQt6.QtGui import QShortcut, QKeySequence  # noqa: PLC0415
        self._hotkey_stop = QShortcut(QKeySequence("F1"), self)
        self._hotkey_stop.activated.connect(self._emergency_stop)

    def _emergency_stop(self) -> None:
        """Raccourci F1 : arrête immédiatement le farm s'il tourne."""
        if self._farm_worker is not None and self._farm_worker.isRunning():
            logger.info("F1 : arrêt d'urgence du farm")
            self._stop_session()

    # ---------------------------------------------------------------------
    # Page 0 : Accueil (Hub)
    # ---------------------------------------------------------------------

    def _build_home(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background-color: #1e1e2e;")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── HEADER ────────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(72)
        header.setStyleSheet(
            "background-color: #16162a;"
            "border-bottom: 1px solid #2e2e48;"
        )
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(28, 0, 28, 0)
        header_lay.setSpacing(12)

        logo = QLabel("🎮")
        logo.setFont(QFont("Segoe UI", 22))
        header_lay.addWidget(logo)

        title_lbl = QLabel("Dofus Bot")
        title_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #e8e8f0;")
        header_lay.addWidget(title_lbl)

        # version
        _version = "0.1.0"
        try:
            import pathlib as _pl
            _vfile = _pl.Path(__file__).parents[4] / "VERSION"
            if not _vfile.exists():
                _vfile = _pl.Path(__file__).parents[3] / "VERSION"
            if _vfile.exists():
                _version = _vfile.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        ver_lbl = QLabel(f"v{_version}")
        ver_lbl.setStyleSheet(
            "color: #5a5a7a; font-size: 10pt; "
            "background: #22223a; border-radius: 4px; padding: 2px 8px;"
        )
        header_lay.addWidget(ver_lbl)

        header_lay.addStretch(1)

        # Status badges zone (filled dynamically)
        self._badge_ai = QLabel()
        self._badge_ai.setTextFormat(Qt.TextFormat.RichText)
        self._badge_ai.setStyleSheet("font-size: 9pt;")
        header_lay.addWidget(self._badge_ai)

        self._btn_test_ai_home = QPushButton("Tester")
        self._btn_test_ai_home.setFixedHeight(28)
        self._btn_test_ai_home.setStyleSheet(
            "QPushButton { background: #2a2a3e; color: #4fc3f7; border: 1px solid #4fc3f7;"
            " border-radius: 5px; padding: 0 10px; font-size: 9pt; }"
            "QPushButton:hover { background: #3a3a54; }"
        )
        self._btn_test_ai_home.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_test_ai_home.clicked.connect(self._test_ai_from_home)
        header_lay.addWidget(self._btn_test_ai_home)

        # Separator
        sep_v = QFrame()
        sep_v.setFrameShape(QFrame.Shape.VLine)
        sep_v.setStyleSheet("color: #2e2e48;")
        sep_v.setFixedWidth(1)
        header_lay.addWidget(sep_v)

        self._badge_update = QLabel()
        self._badge_update.setTextFormat(Qt.TextFormat.RichText)
        self._badge_update.setStyleSheet("font-size: 9pt;")
        header_lay.addWidget(self._badge_update)

        root.addWidget(header)

        # ── BODY ──────────────────────────────────────────────────────────
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(32, 20, 32, 16)
        body_lay.setSpacing(16)

        # Subtitle
        subtitle = QLabel("Que veux-tu faire ?")
        subtitle.setFont(QFont("Segoe UI", 13))
        subtitle.setStyleSheet("color: #8080a0;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_lay.addWidget(subtitle)

        # ── 3 ACTION CARDS ────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(20)

        self._card_farm = _make_card_button(
            "🌾", "Farmer",
            "Bûcheron, Paysan, Mineur…\n6 métiers disponibles"
        )
        self._card_farm.setMinimumSize(240, 180)
        self._card_farm.clicked.connect(lambda: self._go_to(1))
        cards_row.addWidget(self._card_farm)

        self._card_combat = _make_card_button(
            "⚔️", "Combattre",
            "PvM automatique\nIA vision, toutes les classes"
        )
        self._card_combat.setMinimumSize(240, 180)
        self._card_combat.clicked.connect(lambda: self._go_to(2))
        cards_row.addWidget(self._card_combat)

        self._card_craft = _make_card_button(
            "🔨", "Crafter",
            "Bientôt disponible\n(calibration en cours)",
            disabled=True,
        )
        self._card_craft.setMinimumSize(240, 180)
        cards_row.addWidget(self._card_craft)

        body_lay.addLayout(cards_row)

        # ── STATS RAPIDES ─────────────────────────────────────────────────
        stats_container = QWidget()
        stats_container.setStyleSheet(
            "background-color: #22223a; border-radius: 10px;"
            "border: 1px solid #2e2e48;"
        )
        stats_lay = QHBoxLayout(stats_container)
        stats_lay.setContentsMargins(24, 14, 24, 14)
        stats_lay.setSpacing(0)

        stats_defs = [
            ("Combats aujourd'hui", "—", "#4fc3f7"),
            ("Temps de farm", "—", "#66bb6a"),
            ("Drops rares", "—", "#ff9800"),
            ("XP gagné", "—", "#ab47bc"),
        ]

        for i, (label, value, color) in enumerate(stats_defs):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet(f"color: #2e2e48; min-height: 32px; max-height: 32px;")
                stats_lay.addWidget(sep)
                stats_lay.addSpacing(0)

            stat_widget = QWidget()
            stat_v = QVBoxLayout(stat_widget)
            stat_v.setContentsMargins(20, 0, 20, 0)
            stat_v.setSpacing(2)
            stat_v.setAlignment(Qt.AlignmentFlag.AlignCenter)

            val_lbl = QLabel(value)
            val_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {color};")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("font-size: 9pt; color: #707090;")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            stat_v.addWidget(val_lbl)
            stat_v.addWidget(name_lbl)
            stats_lay.addWidget(stat_widget, stretch=1)

        body_lay.addWidget(stats_container)

        root.addWidget(body, stretch=1)

        # ── FOOTER ────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(52)
        footer.setStyleSheet(
            "background-color: #16162a;"
            "border-top: 1px solid #2e2e48;"
        )
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(20, 0, 20, 0)
        footer_lay.setSpacing(8)

        _footer_btn_style = (
            "QPushButton { background: transparent; color: #8080a8; border: 1px solid #2e2e48;"
            " border-radius: 6px; padding: 4px 14px; font-size: 9pt; }"
            "QPushButton:hover { background: #2a2a3e; color: #c0c0d8; border-color: #4a4a6e; }"
        )

        btn_debug = QPushButton("Debug Vision")
        btn_debug.setStyleSheet(_footer_btn_style)
        btn_debug.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_debug.setToolTip("Lance le mode debug pour tester la détection visuelle")
        footer_lay.addWidget(btn_debug)

        btn_settings = QPushButton("Paramètres")
        btn_settings.setStyleSheet(_footer_btn_style)
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_settings.setToolTip("Accéder aux paramètres avancés")
        footer_lay.addWidget(btn_settings)

        btn_discord = QPushButton("Discord")
        btn_discord.setStyleSheet(_footer_btn_style)
        btn_discord.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_discord.clicked.connect(lambda: self._open_url("https://discord.gg/dofus"))
        footer_lay.addWidget(btn_discord)

        # Fenêtre Dofus (dans footer, compact)
        footer_lay.addSpacing(16)
        self._window_footer = QLabel()
        self._window_footer.setTextFormat(Qt.TextFormat.RichText)
        self._window_footer.setWordWrap(False)
        self._window_footer.setStyleSheet("color: #606080; font-size: 9pt;")
        footer_lay.addWidget(self._window_footer, stretch=1)

        change_btn = QPushButton("Changer…")
        change_btn.setFlat(True)
        change_btn.setStyleSheet(
            "color: #4fc3f7; background: transparent; border: none; font-size: 9pt;"
            "text-decoration: underline;"
        )
        change_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        change_btn.clicked.connect(self._open_window_picker)
        footer_lay.addWidget(change_btn)

        footer_lay.addSpacing(20)

        made_lbl = QLabel("Made with Claude Code")
        made_lbl.setStyleSheet("color: #3a3a58; font-size: 8pt;")
        footer_lay.addWidget(made_lbl)

        root.addWidget(footer)

        # Populate status badges after widget tree is built
        QTimer.singleShot(0, self._update_home_status_badges)

        # Check périodique des MAJ (toutes les 5 min tant que le bot tourne)
        # Si une maj est détectée ET qu'aucun combat n'est actif, elle s'installe auto.
        self._update_check_timer = QTimer(self)
        self._update_check_timer.setInterval(5 * 60 * 1000)  # 5 minutes
        self._update_check_timer.timeout.connect(self._periodic_update_check)
        self._update_check_timer.start()

        return page

    def _update_home_status_badges(self) -> None:
        """Met à jour les badges IA et mise à jour dans le header du hub."""
        if not hasattr(self, "_badge_ai"):
            return

        # ── Badge IA ──────────────────────────────────────────────────────
        try:
            from src.services.user_prefs import get_user_prefs as _gup  # noqa: PLC0415
            api_key = getattr(_gup().global_prefs, "gemini_api_key", "") or ""
        except Exception:
            api_key = ""

        provider_widget_val = ""
        if hasattr(self, "_combo_llm_provider"):
            provider_widget_val = self._combo_llm_provider.currentData() or ""

        # Determine badge state
        if not api_key and (not provider_widget_val or provider_widget_val == "gemini"):
            ai_html = (
                "<span style='background:#3a1a1a; color:#ef5350; border-radius:4px;"
                " padding:2px 10px; border:1px solid #5a2a2a;'>"
                " Cle API manquante</span>"
            )
        else:
            ai_html = (
                "<span style='background:#1a2a1a; color:#888; border-radius:4px;"
                " padding:2px 10px; border:1px solid #2a3a2a;'>"
                " Gemini non teste</span>"
            )

        self._badge_ai.setText(ai_html)

        # ── Badge mise à jour ─────────────────────────────────────────────
        try:
            from src.services.auto_updater import get_current_version, check_for_update  # noqa: PLC0415
        except Exception:
            get_current_version = lambda: "0.1.0"  # noqa: E731
            check_for_update = None

        _version = get_current_version()
        # Affiche d'abord "A jour" (check asynchrone ensuite)
        self._badge_update.setText(
            f"<span style='background:#1a2a1a; color:#66bb6a; border-radius:4px;"
            f" padding:2px 10px; border:1px solid #2a4a2a;'>"
            f" A jour (v{_version})</span>"
        )

        # Lance le check dans un thread (non bloquant — ~2s d'appel réseau)
        if check_for_update is None:
            return
        from PyQt6.QtCore import QThreadPool, QRunnable, QObject  # noqa: PLC0415
        from PyQt6.QtCore import pyqtSignal as _Sig  # noqa: PLC0415

        class _UpdSig(QObject):
            done = _Sig(object)

        class _UpdCheckJob(QRunnable):
            def __init__(self_j) -> None:
                super().__init__()
                self_j.sig = _UpdSig()

            def run(self_j) -> None:
                try:
                    info = check_for_update()
                    self_j.sig.done.emit(info)
                except Exception:
                    self_j.sig.done.emit(None)

        def _on_update_checked(info) -> None:
            if info is None or not info.has_update:
                return  # reste sur "A jour"
            self._latest_update_info = info
            # Auto-install au démarrage si pas de combat actif
            no_combat = (
                self._combat_worker is None
                and self._farm_worker is None
                and self._hunt_worker is None
            )
            if no_combat:
                # Installation automatique (sans confirmation) — le bot redémarrera seul
                self._auto_install_update(info)
            else:
                # En combat : affiche juste le badge (installation à la fin du combat)
                html = (
                    f"<span style='background:#2a1f0a; color:#ffa726; border-radius:4px;"
                    f" padding:2px 10px; border:1px solid #5a3a10; font-weight: 600;'>"
                    f" Maj v{info.latest_version} — installation apres ce combat</span>"
                )
                self._badge_update.setText(html)
                try:
                    self._badge_update.mousePressEvent = lambda e: self._install_update_from_home()  # type: ignore
                    from PyQt6.QtCore import Qt  # noqa: PLC0415
                    self._badge_update.setCursor(Qt.CursorShape.PointingHandCursor)
                except Exception:
                    pass

        job = _UpdCheckJob()
        job.sig.done.connect(_on_update_checked)
        QThreadPool.globalInstance().start(job)

    def _periodic_update_check(self) -> None:
        """Check périodique des MAJ (appelé toutes les 5 min par QTimer).

        Si nouvelle version disponible ET aucun combat actif :
          - télécharge auto
          - applique auto
          - relance le bot auto
        Si un combat est en cours : attend la prochaine itération.
        """
        # Skip si un combat est actif (on ne veut pas couper un combat en cours)
        if self._combat_worker is not None or self._farm_worker is not None or self._hunt_worker is not None:
            return

        from PyQt6.QtCore import QThreadPool, QRunnable, QObject  # noqa: PLC0415
        from PyQt6.QtCore import pyqtSignal as _Sig  # noqa: PLC0415

        class _UpdSig(QObject):
            done = _Sig(object)

        class _UpdJob(QRunnable):
            def __init__(self_j) -> None:
                super().__init__()
                self_j.sig = _UpdSig()

            def run(self_j) -> None:
                try:
                    from src.services.auto_updater import check_for_update  # noqa: PLC0415
                    info = check_for_update()
                    self_j.sig.done.emit(info)
                except Exception:
                    self_j.sig.done.emit(None)

        def _on_checked(info) -> None:
            if info is None or not info.has_update:
                return
            # Une maj est dispo ET on n'est pas en combat → install auto
            self._latest_update_info = info
            self._auto_install_update(info)

        job = _UpdJob()
        job.sig.done.connect(_on_checked)
        QThreadPool.globalInstance().start(job)

    def _auto_install_update(self, info) -> None:
        """Télécharge + installe la maj + redémarre le bot, sans confirmation."""
        logger.info("Auto-update : téléchargement v{}", info.latest_version)
        try:
            self._badge_update.setText(
                f"<span style='background:#1a2030; color:#4fc3f7; border-radius:4px;"
                f" padding:2px 10px; border:1px solid #2a3a50; font-weight: 600;'>"
                f" Telechargement v{info.latest_version}...</span>"
            )
        except Exception:
            pass

        from src.services.auto_updater import download_and_apply_update, restart_bot  # noqa: PLC0415
        ok, msg = download_and_apply_update(info, auto_restart=False)
        if ok:
            logger.info("Auto-update : {} — redémarrage", msg)
            try:
                self._badge_update.setText(
                    f"<span style='background:#1a2a1a; color:#66bb6a; border-radius:4px;"
                    f" padding:2px 10px; border:1px solid #2a4a2a;'>"
                    f" v{info.latest_version} installee, redemarrage auto...</span>"
                )
            except Exception:
                pass
            # Court délai pour que l'utilisateur voie le badge puis redémarre
            QTimer.singleShot(1500, lambda: restart_bot())
        else:
            logger.warning("Auto-update échoué : {}", msg)
            try:
                self._badge_update.setText(
                    f"<span style='background:#2a1f0a; color:#ffa726; border-radius:4px;"
                    f" padding:2px 10px; border:1px solid #5a3a10;'>"
                    f" Maj v{info.latest_version} echouee (clique pour reessayer)</span>"
                )
            except Exception:
                pass

    def _install_update_from_home(self) -> None:
        """Télécharge et applique la mise à jour affichée dans le badge."""
        info = getattr(self, "_latest_update_info", None)
        if info is None or not info.has_update:
            return

        from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415
        reply = QMessageBox.question(
            self,
            f"Installer v{info.latest_version} ?",
            f"Nouvelle version disponible : v{info.latest_version}\n\n"
            f"{(info.release_notes or '')[:400]}\n\n"
            f"Le bot va se mettre à jour puis devra redémarrer.\nContinuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from src.services.auto_updater import download_and_apply_update  # noqa: PLC0415
        self._badge_update.setText(
            "<span style='background:#1a2030; color:#4fc3f7; border-radius:4px;"
            " padding:2px 10px; border:1px solid #2a3a50;'>"
            " Téléchargement...</span>"
        )
        # Télécharge ET applique SANS redémarrer tout de suite, on veut afficher le msg
        ok, msg = download_and_apply_update(info, auto_restart=False)
        if ok:
            # Montre confirmation courte puis relance auto
            QMessageBox.information(
                self,
                "Mise à jour appliquée ✓",
                f"{msg}\n\nLe bot va redémarrer automatiquement dans 2 secondes.",
            )
            self._badge_update.setText(
                f"<span style='background:#1a2a1a; color:#66bb6a; border-radius:4px;"
                f" padding:2px 10px; border:1px solid #2a4a2a;'>"
                f" v{info.latest_version} OK — redémarrage...</span>"
            )
            # Lance le redémarrage (tue le processus actuel)
            try:
                from src.services.auto_updater import restart_bot  # noqa: PLC0415
                restart_bot()
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Redémarrage manuel requis",
                    f"Impossible de redémarrer auto : {exc}\n"
                    f"Ferme le bot et relance-le manuellement.",
                )
        else:
            QMessageBox.warning(self, "Mise à jour échouée", msg)
            self._update_home_status_badges()

    def _test_ai_from_home(self) -> None:
        """Test rapide de la connexion IA depuis le hub — met à jour le badge en résultat."""
        if not hasattr(self, "_badge_ai"):
            return

        self._badge_ai.setText(
            "<span style='background:#1a2030; color:#4fc3f7; border-radius:4px;"
            " padding:2px 10px; border:1px solid #2a3a50;'>"
            " Test en cours...</span>"
        )

        from PyQt6.QtCore import QThreadPool, QRunnable, QObject  # noqa: PLC0415
        from PyQt6.QtCore import pyqtSignal as _Sig  # noqa: PLC0415

        class _Sig2(QObject):
            done = _Sig(str)  # html string

        class _AiTestJob(QRunnable):
            def __init__(self_j, api_key: str) -> None:
                super().__init__()
                self_j.api_key = api_key
                self_j.sig = _Sig2()

            def run(self_j) -> None:
                try:
                    import time as _t  # noqa: PLC0415
                    from src.services.llm_client import LLMClient  # noqa: PLC0415
                    client = LLMClient(
                        provider="gemini",
                        model="gemini-flash-latest",
                        api_key=self_j.api_key or None,
                        timeout_sec=15.0,
                    )
                    if not client.is_available():
                        self_j.sig.done.emit(
                            "<span style='background:#3a1a1a; color:#ef5350; border-radius:4px;"
                            " padding:2px 10px; border:1px solid #5a2a2a;'>"
                            " Gemini indisponible</span>"
                        )
                        return
                    t0 = _t.time()
                    resp = client.ask_json("Réponds juste: {\"ok\": true}", fallback={})
                    elapsed = _t.time() - t0
                    if resp:
                        self_j.sig.done.emit(
                            f"<span style='background:#1a2a1a; color:#66bb6a; border-radius:4px;"
                            f" padding:2px 10px; border:1px solid #2a4a2a;'>"
                            f" Gemini connecte (avg {elapsed:.1f}s)</span>"
                        )
                    else:
                        self_j.sig.done.emit(
                            "<span style='background:#3a2a1a; color:#ff9800; border-radius:4px;"
                            " padding:2px 10px; border:1px solid #5a3a1a;'>"
                            " Gemini : reponse vide</span>"
                        )
                except Exception as exc:
                    self_j.sig.done.emit(
                        f"<span style='background:#3a1a1a; color:#ef5350; border-radius:4px;"
                        f" padding:2px 10px; border:1px solid #5a2a2a;'>"
                        f" Erreur : {str(exc)[:40]}</span>"
                    )

        try:
            from src.services.user_prefs import get_user_prefs as _gup2  # noqa: PLC0415
            api_key = getattr(_gup2().global_prefs, "gemini_api_key", "") or ""
        except Exception:
            api_key = ""

        if hasattr(self, "_edit_gemini_key"):
            typed = self._edit_gemini_key.text().strip()
            if typed:
                api_key = typed

        job = _AiTestJob(api_key=api_key)
        job.sig.done.connect(lambda html: self._badge_ai.setText(html))
        QThreadPool.globalInstance().start(job)

    @staticmethod
    def _open_url(url: str) -> None:
        try:
            import webbrowser  # noqa: PLC0415
            webbrowser.open(url)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Page 1 : Wizard Farm
    # ---------------------------------------------------------------------

    def _build_farm_wizard(self) -> QWidget:
        # On enveloppe le contenu dans un QScrollArea pour que tout reste accessible
        # même sur des petites fenêtres (<800px de haut).
        outer_page = QWidget()
        outer_layout = QVBoxLayout(outer_page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea(outer_page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        # Bouton retour
        back = QPushButton("← Retour")
        back.setFlat(True)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet("background: transparent; color: #b0b0b0; padding: 4px 8px;")
        back.clicked.connect(lambda: self._go_to(0))
        root.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

        title = QLabel("Choisis ton métier")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        root.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)

        # Grille 3x2 des métiers
        catalog = get_catalog()
        metiers_grid = QGridLayout()
        metiers_grid.setSpacing(12)
        self._farm_job_buttons: dict[str, QPushButton] = {}

        metiers_recolte = [m for m in catalog.metiers_disponibles() if m in METIER_ICONS]
        for i, metier in enumerate(metiers_recolte):
            nb_ress = len(catalog.by_metier(metier))
            # Carte métier compacte (icône à gauche, nom + compte à droite)
            btn = QPushButton()
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(70)
            btn.setText(
                f"{METIER_ICONS.get(metier, '•')}   {traduire_metier(metier)}\n"
                f"       {nb_ress} ressources"
            )
            btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #2a2a3e; border: 2px solid #3a3a4e; border-radius: 10px;"
                "  color: #e0e0e0; font-size: 12pt; padding: 10px 16px; text-align: left;"
                "}"
                "QPushButton:hover { border-color: #4fc3f7; background-color: #2f2f44; }"
                "QPushButton:pressed { background-color: #35355a; }"
            )
            btn.clicked.connect(lambda _checked=False, m=metier: self._select_farm_metier(m))
            self._farm_job_buttons[metier] = btn
            metiers_grid.addWidget(btn, i // 3, i % 3)

        root.addLayout(metiers_grid)

        # Niveau perso + bouton démarrer
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Ton niveau dans ce métier :"))
        self._farm_level_spin = QSpinBox()
        self._farm_level_spin.setRange(1, 200)
        self._farm_level_spin.setValue(1)
        self._farm_level_spin.setMinimumWidth(80)
        self._farm_level_spin.valueChanged.connect(self._refresh_farm_resource_list)
        bottom.addWidget(self._farm_level_spin)
        bottom.addStretch(1)

        self._farm_selected_label = QLabel("<i>Choisis un métier ci-dessus</i>")
        self._farm_selected_label.setTextFormat(Qt.TextFormat.RichText)
        self._farm_selected_label.setStyleSheet("color: #4fc3f7;")
        bottom.addWidget(self._farm_selected_label)
        root.addLayout(bottom)

        # --- Sélection des ressources (sous-catégories) ---
        res_header = QHBoxLayout()
        res_label = QLabel("📋  Ressources à récolter")
        res_label.setStyleSheet("color: #b0b0b0; font-weight: 600; margin-top: 8px;")
        res_header.addWidget(res_label)
        res_header.addStretch(1)

        self._btn_farm_res_all = QPushButton("Tout cocher")
        self._btn_farm_res_all.setFlat(True)
        self._btn_farm_res_all.setStyleSheet("color: #4fc3f7; padding: 2px 8px;")
        self._btn_farm_res_all.clicked.connect(lambda: self._set_all_farm_resources(True))
        res_header.addWidget(self._btn_farm_res_all)

        self._btn_farm_res_none = QPushButton("Tout décocher")
        self._btn_farm_res_none.setFlat(True)
        self._btn_farm_res_none.setStyleSheet("color: #b0b0b0; padding: 2px 8px;")
        self._btn_farm_res_none.clicked.connect(lambda: self._set_all_farm_resources(False))
        res_header.addWidget(self._btn_farm_res_none)
        root.addLayout(res_header)

        self._farm_res_scroll = QScrollArea()
        self._farm_res_scroll.setWidgetResizable(True)
        self._farm_res_scroll.setMinimumHeight(180)
        self._farm_res_scroll.setMaximumHeight(260)
        self._farm_res_scroll.setStyleSheet(
            "QScrollArea { background: #1a1a24; border: 1px solid #3a3a4e; border-radius: 6px; }"
        )
        self._farm_res_container = QWidget()
        self._farm_res_layout = QGridLayout(self._farm_res_container)
        self._farm_res_layout.setContentsMargins(8, 8, 8, 8)
        self._farm_res_layout.setSpacing(4)
        self._farm_res_scroll.setWidget(self._farm_res_container)
        self._farm_res_checkboxes: dict[str, QCheckBox] = {}
        self._farm_res_empty = QLabel("<i>Sélectionne un métier pour voir les ressources</i>")
        self._farm_res_empty.setStyleSheet("color: #808080; padding: 12px;")
        self._farm_res_layout.addWidget(self._farm_res_empty, 0, 0)
        root.addWidget(self._farm_res_scroll)

        # --- Réglages de cadence ---
        timing_label = QLabel("⚙  Cadence (réglage fin)")
        timing_label.setStyleSheet("color: #b0b0b0; font-weight: 600; margin-top: 8px;")
        root.addWidget(timing_label)

        timing_row = QHBoxLayout()

        timing_row.addWidget(QLabel("Durée récolte :"))
        self._farm_animation_spin = QDoubleSpinBox()
        self._farm_animation_spin.setRange(0.3, 10.0)
        self._farm_animation_spin.setSingleStep(0.1)
        self._farm_animation_spin.setValue(1.5)
        self._farm_animation_spin.setSuffix(" s")
        self._farm_animation_spin.setToolTip("Temps d'animation de coupe en jeu (baisse si tu as un meilleur outil)")
        self._farm_animation_spin.setMinimumWidth(80)
        timing_row.addWidget(self._farm_animation_spin)

        timing_row.addSpacing(24)

        timing_row.addWidget(QLabel("Délai entre scans :"))
        self._farm_tick_spin = QDoubleSpinBox()
        self._farm_tick_spin.setRange(0.2, 10.0)
        self._farm_tick_spin.setSingleStep(0.1)
        self._farm_tick_spin.setValue(0.6)
        self._farm_tick_spin.setSuffix(" s")
        self._farm_tick_spin.setToolTip("Pause avant la prochaine recherche de ressource")
        self._farm_tick_spin.setMinimumWidth(80)
        timing_row.addWidget(self._farm_tick_spin)

        timing_row.addStretch(1)
        root.addLayout(timing_row)

        # --- Circuit de maps (navigation map-par-map) ---
        circuit_label = QLabel("🧭  Circuit de maps (priorité n°1 si rempli)")
        circuit_label.setStyleSheet("color: #b0b0b0; font-weight: 600; margin-top: 8px;")
        root.addWidget(circuit_label)

        circuit_row = QHBoxLayout()
        circuit_hint = QLabel("Coords à enchaîner (x,y ; x,y) :")
        circuit_hint.setStyleSheet("color: #808080; font-size: 9pt;")
        circuit_row.addWidget(circuit_hint)

        self._farm_circuit_edit = QLineEdit()
        self._farm_circuit_edit.setPlaceholderText("9,5 ; 9,6 ; 8,6 ; 8,5")
        self._farm_circuit_edit.setToolTip(
            "Liste de coords Dofus séparées par ';' ou '|'. Le bot farme chaque map\n"
            "puis enchaîne via clics de bords (haut/bas/gauche/droite).\n"
            "Laisse vide pour utiliser la rotation zaaps à la place."
        )
        circuit_row.addWidget(self._farm_circuit_edit, stretch=1)
        root.addLayout(circuit_row)

        # --- Rotation zaaps (fallback si pas de circuit) ---
        zaap_label = QLabel("🌀  Rotation zaaps (si pas de circuit)")
        zaap_label.setStyleSheet("color: #b0b0b0; font-weight: 600; margin-top: 8px;")
        root.addWidget(zaap_label)

        zaap_row = QHBoxLayout()
        zaap_hint = QLabel("Destinations séparées par virgules :")
        zaap_hint.setStyleSheet("color: #808080; font-size: 9pt;")
        zaap_row.addWidget(zaap_hint)

        self._farm_zaap_edit = QLineEdit()
        self._farm_zaap_edit.setPlaceholderText("ingalsse, astrub, village d'amakna")
        self._farm_zaap_edit.setToolTip(
            "Quand la map se vide et qu'il n'y a pas de circuit, le bot ouvre .zaap\n"
            "et tape ces noms l'un après l'autre."
        )
        zaap_row.addWidget(self._farm_zaap_edit, stretch=1)
        root.addLayout(zaap_row)

        self._farm_start_btn = _make_primary_button("🚀  LANCER LE FARM", kind="success")
        self._farm_start_btn.setEnabled(False)
        self._farm_start_btn.clicked.connect(self._start_farm)
        root.addWidget(self._farm_start_btn)

        self._farm_selected_metier: str | None = None
        self._preferred_resources_for_metier: set[str] = set()

        # Pré-sélectionne le dernier métier utilisé (si sauvegardé en prefs)
        try:
            last_metier = get_user_prefs().global_prefs.last_metier
            if last_metier and last_metier in self._farm_job_buttons:
                # Délai court pour que le widget soit visible avant le refresh
                QTimer.singleShot(100, lambda: self._select_farm_metier(last_metier))
        except Exception as exc:
            logger.debug("Pré-sélection dernier métier échouée : {}", exc)

        # Wrap dans le scroll area
        scroll.setWidget(page)
        outer_layout.addWidget(scroll)
        return outer_page

    def _select_farm_metier(self, metier: str) -> None:
        self._farm_selected_metier = metier
        # Style sélectionné vs non-sélectionné (bordure verte si actif)
        for m, btn in self._farm_job_buttons.items():
            selected = m == metier
            if selected:
                btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #1e3a1e; border: 2px solid #66bb6a; border-radius: 10px;"
                    "  color: white; font-size: 12pt; padding: 10px 16px; text-align: left;"
                    "}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #2a2a3e; border: 2px solid #3a3a4e; border-radius: 10px;"
                    "  color: #e0e0e0; font-size: 12pt; padding: 10px 16px; text-align: left;"
                    "}"
                    "QPushButton:hover { border-color: #4fc3f7; background-color: #2f2f44; }"
                )
        self._farm_selected_label.setText(f"Métier : <b>{traduire_metier(metier)}</b>")

        # --- Charge les prefs sauvegardées pour ce métier ---
        prefs = get_user_prefs().farm(metier)

        # IMPORTANT : mémoriser les IDs cochés AVANT de toucher le spinbox,
        # car setValue() déclenche valueChanged qui appelle _refresh_farm_resource_list.
        self._preferred_resources_for_metier = set(prefs.resources)

        # Bloque les signaux pendant la mise à jour pour éviter un double-refresh
        self._farm_level_spin.blockSignals(True)
        try:
            self._farm_level_spin.setValue(prefs.niveau)
        finally:
            self._farm_level_spin.blockSignals(False)

        self._farm_animation_spin.setValue(prefs.animation_duration_sec)
        self._farm_tick_spin.setValue(prefs.tick_interval_sec)
        self._farm_zaap_edit.setText(", ".join(prefs.zaap_rotation))
        self._farm_circuit_edit.setText(
            " ; ".join(f"{x},{y}" for (x, y) in prefs.circuit_maps)
        )

        self._refresh_farm_resource_list()
        self._update_farm_start_enabled()

    def _refresh_farm_resource_list(self) -> None:
        """Remplit la grille de cases à cocher en fonction du métier + niveau actuel."""
        # Purge
        while self._farm_res_layout.count():
            item = self._farm_res_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._farm_res_checkboxes.clear()

        if not self._farm_selected_metier:
            empty = QLabel("<i>Sélectionne un métier pour voir les ressources</i>")
            empty.setStyleSheet("color: #808080; padding: 12px;")
            self._farm_res_layout.addWidget(empty, 0, 0)
            return

        # Récupère les ressources disponibles au niveau + infos calibrage
        from src.services.hsv_learner import HsvLearner  # noqa: PLC0415
        from src.services.template_matcher import TemplateMatcher  # noqa: PLC0415

        catalog = get_catalog()
        niveau = self._farm_level_spin.value()
        resources = catalog.by_niveau_max(self._farm_selected_metier, niveau)

        if not resources:
            empty = QLabel(f"<i>Aucune ressource accessible au niveau {niveau}</i>")
            empty.setStyleSheet("color: #ffa726; padding: 12px;")
            self._farm_res_layout.addWidget(empty, 0, 0)
            self._update_farm_start_enabled()
            return

        # Statut calibrage
        try:
            learner = HsvLearner()
            learned_ids = set(learner.all_learned().keys())
        except Exception:
            learned_ids = set()
        try:
            matcher = TemplateMatcher()
            template_ids = set(matcher.list_templates())
        except Exception:
            template_ids = set()

        # 3 colonnes
        cols = 3
        for i, res in enumerate(resources):
            has_template = res.id in template_ids
            has_hsv = res.id in learned_ids
            if has_template:
                badge = " <span style='color:#ab47bc;'>📷 tpl</span>"
            elif has_hsv:
                badge = " <span style='color:#66bb6a;'>🎯 hsv</span>"
            else:
                badge = " <span style='color:#ff7043;'>⚠ non calibré</span>"
            cb = QCheckBox()
            cb.setText("")  # on mettra le label via rich text
            # Utilise un QLabel adjacent pour avoir du rich text (QCheckBox ne rend pas HTML)
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            row_layout.addWidget(cb)
            lbl = QLabel(f"{res.nom_fr} <span style='color:#808080;font-size:8pt;'>niv {res.niveau_requis}</span>{badge}")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("color: #e0e0e0; font-size: 10pt;")
            row_layout.addWidget(lbl)
            row_layout.addStretch(1)
            # Priorité : prefs user sauvegardées > défaut "calibré"
            preferred = getattr(self, "_preferred_resources_for_metier", None)
            if preferred is not None and preferred:
                cb.setChecked(res.id in preferred)
            else:
                cb.setChecked(has_template or has_hsv)
            cb.stateChanged.connect(lambda _s: self._update_farm_start_enabled())
            self._farm_res_checkboxes[res.id] = cb
            self._farm_res_layout.addWidget(row_widget, i // cols, i % cols)

        self._update_farm_start_enabled()

    def _set_all_farm_resources(self, checked: bool) -> None:
        for cb in self._farm_res_checkboxes.values():
            cb.setChecked(checked)

    def _update_farm_start_enabled(self) -> None:
        """Active 'LANCER' uniquement si un métier est choisi ET ≥1 ressource cochée."""
        has_metier = self._farm_selected_metier is not None
        any_checked = any(cb.isChecked() for cb in self._farm_res_checkboxes.values())
        self._farm_start_btn.setEnabled(has_metier and any_checked)

    @staticmethod
    def _parse_circuit_text(text: str) -> list[tuple[int, int]]:
        """Parse un texte de circuit en liste de coords (x, y).

        Accepte : "9,5 ; 9,6 ; 8,6" ou "9,5|9,6|8,6" ou avec parenthèses "(9,5);(9,6)".
        Ignore silencieusement les entrées mal formées.
        """
        import re  # noqa: PLC0415
        text = (text or "").strip()
        if not text:
            return []
        # Split sur ; ou | (virgule réservée aux séparateurs x,y)
        chunks = re.split(r"[;|]", text)
        result: list[tuple[int, int]] = []
        for chunk in chunks:
            chunk = chunk.strip().strip("()").strip()
            if not chunk:
                continue
            m = re.match(r"^(-?\d+)\s*,\s*(-?\d+)$", chunk)
            if m:
                result.append((int(m.group(1)), int(m.group(2))))
        return result

    def _start_farm(self) -> None:
        if self._farm_selected_metier is None:
            return
        if self._selected_window is None:
            QMessageBox.warning(self, "Fenêtre Dofus introuvable",
                                "Aucune fenêtre Dofus détectée. Ouvre le jeu puis reviens ici.")
            return
        if self._vision is None:
            QMessageBox.critical(self, "Erreur", "Service vision indisponible.")
            return

        metier_fr = traduire_metier(self._farm_selected_metier)
        niveau = self._farm_level_spin.value()
        logger.info("Démarrage farm : {} (niv {}) sur {}", metier_fr, niveau, self._selected_window.title)

        self._active_session_label = f"{METIER_ICONS.get(self._farm_selected_metier, '•')}  {metier_fr} — niveau {niveau}"
        self.session_started.emit(f"farm:{self._farm_selected_metier}:{niveau}")

        # Assure que la vision cible la bonne fenêtre
        self._sync_vision_target()

        # Focus la fenêtre Dofus et lui laisse 500ms pour prendre le focus
        self._selected_window.focus()
        QTimer.singleShot(500, self._launch_farm_worker)

        self._go_to(3)
        self._log_lines.clear()
        self._refresh_timer.start()

    def _launch_farm_worker(self) -> None:
        """Lance effectivement le thread de farm (après le focus fenêtre)."""
        selected_ids = [rid for rid, cb in self._farm_res_checkboxes.items() if cb.isChecked()]
        # Parse la rotation de zaaps
        raw_zaaps = self._farm_zaap_edit.text().strip()
        zaap_rotation = [q.strip() for q in raw_zaaps.split(",") if q.strip()] if raw_zaaps else []
        # Parse le circuit de maps (format "9,5 ; 9,6 ; 8,6" ou séparé par |)
        circuit_maps = self._parse_circuit_text(self._farm_circuit_edit.text())
        cfg = FarmConfig(
            metier=self._farm_selected_metier,
            niveau_personnage=self._farm_level_spin.value(),
            resource_ids=selected_ids if selected_ids else None,
            dofus_window_title=self._selected_window.title if self._selected_window else None,
            animation_duration_sec=self._farm_animation_spin.value(),
            tick_interval_sec=self._farm_tick_spin.value(),
            zaap_rotation=zaap_rotation,
            circuit_maps=circuit_maps,
        )

        # --- Sauvegarde les prefs pour la prochaine session ---
        try:
            prefs = get_user_prefs()
            prefs.set_farm(
                self._farm_selected_metier,
                FarmMetierPrefs(
                    niveau=self._farm_level_spin.value(),
                    resources=selected_ids,
                    circuit_maps=circuit_maps,
                    zaap_rotation=zaap_rotation,
                    animation_duration_sec=self._farm_animation_spin.value(),
                    tick_interval_sec=self._farm_tick_spin.value(),
                ),
            )
            # Mémorise le dernier métier pour le pré-sélectionner au prochain lancement
            prefs.global_prefs.last_metier = self._farm_selected_metier
            prefs.save()
        except Exception as exc:
            logger.warning("Sauvegarde prefs farm échouée : {}", exc)
        self._farm_worker = FarmWorker(
            vision=self._vision,
            input_svc=self._input,
            config=cfg,
        )
        self._farm_worker.stats_updated.connect(self._on_farm_stats)
        self._farm_worker.log_event.connect(self._on_farm_log)
        self._farm_worker.state_changed.connect(self._on_farm_state)
        self._farm_worker.stopped.connect(self._on_farm_stopped)
        self._farm_worker.start()

    # ---------------------------------------------------------------------
    # Page 2 : Wizard Combat
    # ---------------------------------------------------------------------

    def _build_combat_wizard(self) -> QWidget:
        page = QWidget()
        # Scroll pour petits écrans
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        scroll.setWidget(inner)

        back = QPushButton("← Retour")
        back.setFlat(True)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet("background: transparent; color: #b0b0b0; padding: 4px 8px;")
        back.clicked.connect(lambda: self._go_to(0))
        root.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

        title = QLabel("⚔️  Configurer le combat")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        root.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)

        # ═══════════════════════════════════════════════════
        # SECTION 1 : Classe
        # ═══════════════════════════════════════════════════
        grp_class = QGroupBox("1️⃣  Choisis ta classe")
        grp_class.setStyleSheet(_GROUPBOX_STYLE)
        grp_class_lay = QVBoxLayout(grp_class)

        hint = QLabel(
            "15 classes reconnues par l'IA via son knowledge base "
            "(sorts, portées, stratégies). La bordure verte indique que les règles "
            "heuristiques sont aussi implémentées en fallback."
        )
        hint.setStyleSheet("color: #b0b0b0;")
        hint.setWordWrap(True)
        grp_class_lay.addWidget(hint)

        # Grid des classes
        catalog = get_catalog()
        grid = QGridLayout()
        grid.setSpacing(8)
        self._combat_class_buttons: dict[str, QPushButton] = {}
        implementees = set(classes_implementees())

        for i, cls in enumerate(catalog.classes):
            btn = QPushButton(f"{CLASSE_ICONS.get(cls.id, '•')}  {cls.nom_fr}")
            btn.setMinimumSize(130, 60)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            style_extra = "border: 2px solid #66bb6a;" if cls.id in implementees else "border: 1px solid #3a3a4e;"
            btn.setStyleSheet(
                "QPushButton {"
                f"  background-color: #2a2a3e; {style_extra} border-radius: 8px;"
                "  color: #e0e0e0; font-size: 11pt; padding: 6px; }"
                "QPushButton:hover { background-color: #2f2f44; }"
                "QPushButton:checked { background-color: #1e3a1e; border: 2px solid #4fc3f7; }"
            )
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, cid=cls.id: self._select_class(cid))
            self._combat_class_buttons[cls.id] = btn
            grid.addWidget(btn, i // 6, i % 6)
        grp_class_lay.addLayout(grid)

        # Label classe sélectionnée + stratégie
        strat_row = QHBoxLayout()
        self._combat_selected_label = QLabel("<i>Aucune classe sélectionnée</i>")
        self._combat_selected_label.setTextFormat(Qt.TextFormat.RichText)
        self._combat_selected_label.setStyleSheet("color: #4fc3f7; font-size: 12pt;")
        strat_row.addWidget(self._combat_selected_label)
        strat_row.addStretch()
        strat_row.addWidget(QLabel("Stratégie :"))
        self._combat_strategy_combo = QComboBox()
        for strat in CombatStrategy:
            self._combat_strategy_combo.addItem(strat.value.capitalize(), strat.value)
        self._combat_strategy_combo.setCurrentText(CombatStrategy.BALANCED.value.capitalize())
        self._combat_strategy_combo.setFixedWidth(140)
        strat_row.addWidget(self._combat_strategy_combo)
        grp_class_lay.addLayout(strat_row)

        root.addWidget(grp_class)

        # ═══════════════════════════════════════════════════
        # SECTION 2 : IA Vision
        # ═══════════════════════════════════════════════════
        grp_ai = QGroupBox("2️⃣  IA Vision (le cerveau)")
        grp_ai.setStyleSheet(_GROUPBOX_STYLE)
        grp_ai_lay = QVBoxLayout(grp_ai)

        info_label = QLabel(
            "<span style='color:#888'>Un LLM multimodal voit ton écran et décide chaque action. "
            "Auto-détection du provider (Ollama ou LM Studio).</span>"
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        grp_ai_lay.addWidget(info_label)

        # Vision IA toujours ON (plus de toggle), provider auto-détecté
        self._chk_combat_vision = QCheckBox("")  # placeholder, caché
        self._chk_combat_vision.setChecked(True)
        self._chk_combat_vision.setVisible(False)

        # Provider + modèle sur une ligne, avec auto-détection
        llm_row = QHBoxLayout()
        llm_row.addWidget(QLabel("Provider :"))
        self._combo_llm_provider = QComboBox()
        self._combo_llm_provider.addItem("Anthropic Claude (payant ~$0.1/jour ⭐⭐ RECO)", "anthropic")
        self._combo_llm_provider.addItem("Google Gemini (cloud GRATUIT)", "gemini")
        self._combo_llm_provider.addItem("Auto-détection local", "auto")
        self._combo_llm_provider.addItem("Ollama (localhost:11434)", "ollama")
        self._combo_llm_provider.addItem("LM Studio (localhost:1234)", "lmstudio")
        self._combo_llm_provider.setFixedWidth(300)
        llm_row.addWidget(self._combo_llm_provider)

        llm_row.addWidget(QLabel("Modèle :"))
        # Dropdown éditable avec recommandation par tier VRAM DISPONIBLE
        # (= VRAM totale - conso Dofus ~2-3 GB)
        self._combat_vision_model = QComboBox()
        self._combat_vision_model.setEditable(True)
        self._combat_vision_model.addItems([
            # Anthropic Claude — RECOMMANDÉ (rapide + intelligent)
            "claude-haiku-4-5-20251001",  # ⭐⭐ rapide ~1-2s, ~$1/1M tokens
            "claude-sonnet-4-6",          # top qualité, ~$3/1M tokens
            "claude-3-5-haiku-20241022",  # legacy rapide
            # Gemini (cloud gratuit)
            "gemini-2.5-flash",      # GRATUIT, rapide, top qualité (peut être surchargé)
            "gemini-flash-latest",   # alias auto vers la dernière version stable
            "gemini-2.5-flash-lite", # rapide mais faible spatial
            "gemini-2.5-pro",        # Meilleur raisonnement (quota réduit)
            "gemini-2.0-flash",      # legacy
            # Ollama (local) — nécessite VRAM
            "qwen2.5vl:3b",
            "qwen2.5vl:7b",
            "qwen2.5vl:32b",
            "qwen2.5vl:72b",
            "minicpm-v:8b",
            "gemma3:12b",
            # LM Studio
            "qwen/qwen2.5-vl-3b-instruct",
            "qwen/qwen2.5-vl-7b-instruct",
        ])
        self._combat_vision_model.setCurrentText("claude-haiku-4-5-20251001")
        self._combat_vision_model.setMinimumWidth(220)
        self._combat_vision_model.setToolTip(
            "⭐ GEMINI (cloud gratuit) — recommandé si PC faible :\n"
            "  • gemini-2.5-flash : rapide, excellent\n"
            "  • gemini-2.5-pro : top qualité (quota réduit)\n\n"
            "🏠 OLLAMA (local) — selon VRAM libre :\n"
            "  • 8 GB (2060 Super/3060/4060) → qwen2.5vl:3b\n"
            "  • 12-16 GB → qwen2.5vl:7b\n"
            "  • 24 GB (3090/4090) → qwen2.5vl:32b\n\n"
            "Clique 🔄 pour lister les modèles réellement installés."
        )
        llm_row.addWidget(self._combat_vision_model, stretch=1)

        self._btn_refresh_models = QPushButton("🔄")
        self._btn_refresh_models.setFixedWidth(40)
        self._btn_refresh_models.setToolTip("Recharger les modèles installés dans le provider")
        self._btn_refresh_models.clicked.connect(self._refresh_llm_models)
        llm_row.addWidget(self._btn_refresh_models)

        self._combo_llm_provider.currentIndexChanged.connect(self._on_llm_provider_changed)
        grp_ai_lay.addLayout(llm_row)

        # Ligne clé API Gemini (visible seulement si provider=gemini)
        self._gemini_key_row = QHBoxLayout()
        self._lbl_gemini_key = QLabel("Clé API Gemini :")
        self._gemini_key_row.addWidget(self._lbl_gemini_key)
        self._edit_gemini_key = QLineEdit()
        self._edit_gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit_gemini_key.setPlaceholderText("AIza...")
        self._edit_gemini_key.setToolTip(
            "Obtiens ta clé gratuite en 30s :\n"
            "  1. Va sur https://aistudio.google.com/app/apikey\n"
            "  2. Connecte-toi avec ton compte Google\n"
            "  3. Clique 'Create API key' → Copy\n"
            "  4. Colle-la ici\n\n"
            "Quota gratuit : 15 requêtes/min, 1M tokens/jour (largement suffisant)"
        )
        # Pré-remplit avec la valeur sauvegardée
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            prefs = get_user_prefs()
            saved_key = getattr(prefs.global_prefs, "gemini_api_key", "") or ""
            if saved_key:
                self._edit_gemini_key.setText(saved_key)
        except Exception:
            pass
        self._gemini_key_row.addWidget(self._edit_gemini_key, stretch=1)

        self._btn_get_gemini_key = QPushButton("🌐 Obtenir une clé")
        self._btn_get_gemini_key.setToolTip("Ouvre la page Google AI Studio pour créer une clé API")
        self._btn_get_gemini_key.clicked.connect(self._open_gemini_key_page)
        self._gemini_key_row.addWidget(self._btn_get_gemini_key)

        self._gemini_key_container = QWidget()
        self._gemini_key_container.setLayout(self._gemini_key_row)
        grp_ai_lay.addWidget(self._gemini_key_container)

        # Ligne clé API Anthropic (visible seulement si provider=anthropic)
        self._anthropic_key_row = QHBoxLayout()
        self._anthropic_key_row.addWidget(QLabel("Clé API Anthropic :"))
        self._edit_anthropic_key = QLineEdit()
        self._edit_anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit_anthropic_key.setPlaceholderText("sk-ant-...")
        self._edit_anthropic_key.setToolTip(
            "Obtiens ta clé sur console.anthropic.com (1 min) :\n"
            "  1. Va sur https://console.anthropic.com/settings/keys\n"
            "  2. Crée un compte (Google/email)\n"
            "  3. Clique 'Create Key' → Copy\n"
            "  4. Colle-la ici\n\n"
            "Coût combat Dofus : ~$0.10-0.30/jour avec Haiku 4.5\n"
            "(5 combats × 20 tours × ~4k tokens ≈ 400k tokens = $0.40 MAX)"
        )
        try:
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
            prefs = get_user_prefs()
            saved_anth = getattr(prefs.global_prefs, "anthropic_api_key", "") or ""
            if saved_anth:
                self._edit_anthropic_key.setText(saved_anth)
        except Exception:
            pass
        self._anthropic_key_row.addWidget(self._edit_anthropic_key, stretch=1)

        self._btn_get_anthropic_key = QPushButton("🌐 Obtenir une clé")
        self._btn_get_anthropic_key.clicked.connect(self._open_anthropic_key_page)
        self._anthropic_key_row.addWidget(self._btn_get_anthropic_key)

        self._anthropic_key_container = QWidget()
        self._anthropic_key_container.setLayout(self._anthropic_key_row)
        grp_ai_lay.addWidget(self._anthropic_key_container)

        # Ligne bouton test + status
        test_row = QHBoxLayout()
        self._btn_test_llm = QPushButton("🧪 Tester l'IA maintenant")
        self._btn_test_llm.setToolTip(
            "Envoie une capture d'écran au LLM et affiche sa réponse dans les logs.\n"
            "À utiliser AVANT de lancer le combat pour vérifier que tout fonctionne."
        )
        self._btn_test_llm.clicked.connect(self._test_llm_now)
        test_row.addWidget(self._btn_test_llm)

        self._chk_save_debug = QCheckBox("💾 Sauvegarder captures debug")
        self._chk_save_debug.setToolTip(
            "Sauvegarde chaque capture envoyée au LLM + sa réponse JSON\n"
            "dans data/vision_debug/. Utile pour comprendre les erreurs."
        )
        test_row.addWidget(self._chk_save_debug)
        test_row.addStretch()
        grp_ai_lay.addLayout(test_row)

        # Status bar IA (rempli dynamiquement)
        self._lbl_llm_status = QLabel(
            "<span style='color:#888'>Clique 🔄 pour tester la connexion au LLM</span>"
        )
        self._lbl_llm_status.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_llm_status.setWordWrap(True)
        grp_ai_lay.addWidget(self._lbl_llm_status)

        self._combat_llm_url = QLineEdit("")
        self._combat_llm_url.setVisible(False)

        # Heuristique : caché (mode fallback interne seulement)
        self._chk_combat_ollama = QCheckBox("")
        self._chk_combat_ollama.setVisible(False)
        self._combat_ollama_model = QLineEdit("phi3:mini")
        self._combat_ollama_model.setVisible(False)

        root.addWidget(grp_ai)

        # ═══════════════════════════════════════════════════
        # SECTION 3 : Barre de sorts visuelle (1-9 slots)
        # ═══════════════════════════════════════════════════
        grp_spells = QGroupBox("3️⃣  Place tes sorts sur les touches 1-9 (comme dans Dofus)")
        grp_spells.setStyleSheet(_GROUPBOX_STYLE)
        grp_spells_lay = QVBoxLayout(grp_spells)

        self._combat_spells_hint = QLabel(
            "<span style='color:#888'>Choisis une classe puis place chaque sort sur sa touche "
            "(comme ta barre de sorts Dofus). Bouton 🪄 pour remplir auto dans l'ordre.</span>"
        )
        self._combat_spells_hint.setTextFormat(Qt.TextFormat.RichText)
        self._combat_spells_hint.setWordWrap(True)
        grp_spells_lay.addWidget(self._combat_spells_hint)

        # Barre de 9 slots façon Dofus (chaque slot = QComboBox avec liste sorts classe)
        slots_row = QHBoxLayout()
        slots_row.setSpacing(6)
        self._combat_spell_slots: dict[int, QComboBox] = {}
        for k in range(1, 10):
            slot_widget = QWidget()
            slot_lay = QVBoxLayout(slot_widget)
            slot_lay.setContentsMargins(0, 0, 0, 0)
            slot_lay.setSpacing(2)

            # Label touche (1-9)
            key_lbl = QLabel(str(k))
            key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            key_lbl.setStyleSheet(
                "background-color: #2a2a3e; color: #4fc3f7; "
                "border-top-left-radius: 6px; border-top-right-radius: 6px; "
                "font-weight: 700; padding: 3px; border: 1px solid #3a3a4e; border-bottom: none;"
            )
            slot_lay.addWidget(key_lbl)

            # Combo de sélection (éditable : l'user peut taper un sort manquant)
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItem("— vide —", "")
            combo.setMinimumWidth(110)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setStyleSheet(
                "QComboBox { background-color: #1e1e2e; color: #e0e0e0; "
                "border: 1px solid #3a3a4e; border-bottom-left-radius: 6px; "
                "border-bottom-right-radius: 6px; padding: 4px; }"
                "QComboBox:hover { border-color: #4fc3f7; }"
            )
            combo.setToolTip(
                "Choisis un sort dans la liste OU tape le nom d'un sort manquant.\n"
                "(Sera normalisé en snake_case pour matcher le knowledge base.)"
            )
            self._combat_spell_slots[k] = combo
            slot_lay.addWidget(combo)

            slots_row.addWidget(slot_widget)
        grp_spells_lay.addLayout(slots_row)

        # Bouton pré-remplir
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_autofill_spells = QPushButton("🪄 Pré-remplir auto (ordre knowledge)")
        self._btn_autofill_spells.setToolTip(
            "Place les sorts de la classe dans l'ordre du knowledge base (1→N)"
        )
        self._btn_autofill_spells.clicked.connect(self._autofill_spells)
        btn_row.addWidget(self._btn_autofill_spells)

        self._btn_clear_spells = QPushButton("✕ Vider")
        self._btn_clear_spells.setToolTip("Vide tous les slots")
        self._btn_clear_spells.clicked.connect(self._clear_spell_slots)
        btn_row.addWidget(self._btn_clear_spells)
        grp_spells_lay.addLayout(btn_row)

        # QLineEdit legacy (caché) — conservé pour compat avec _parse_spell_shortcuts
        self._combat_spells_edit = QLineEdit()
        self._combat_spells_edit.setVisible(False)

        root.addWidget(grp_spells)

        # ═══════════════════════════════════════════════════
        # SECTION 4 : Stats + Mode farm
        # ═══════════════════════════════════════════════════
        grp_stats = QGroupBox("4️⃣  Stats + mode de jeu")
        grp_stats.setStyleSheet(_GROUPBOX_STYLE)
        grp_stats_lay = QVBoxLayout(grp_stats)

        # Mode Aggro
        self._chk_aggro_mode = QCheckBox("🏹 Mode Aggro (enchaîne les combats auto)")
        self._chk_aggro_mode.setChecked(True)
        self._chk_aggro_mode.setToolTip(
            "Coché : scan la map → engage mobs → combat → rinse & repeat\n"
            "Décoché : attend passivement qu'un combat démarre"
        )
        grp_stats_lay.addWidget(self._chk_aggro_mode)

        # Ghost pour l'ancien label (on le laisse caché pour ne pas casser le code existant)
        stats_label = QLabel()
        stats_label.setVisible(False)
        grp_stats_lay.addWidget(stats_label)

        stats_row = QHBoxLayout()
        stats_row.addWidget(QLabel("PA :"))
        self._spin_combat_pa = QSpinBox()
        self._spin_combat_pa.setRange(1, 20)
        self._spin_combat_pa.setValue(6)
        self._spin_combat_pa.setMinimumWidth(90)
        self._spin_combat_pa.setToolTip("Points d'Action par tour (standard : 6, haut niveau : 10-12)")
        stats_row.addWidget(self._spin_combat_pa)

        stats_row.addSpacing(20)
        stats_row.addWidget(QLabel("PM :"))
        self._spin_combat_pm = QSpinBox()
        self._spin_combat_pm.setRange(0, 12)
        self._spin_combat_pm.setValue(3)
        self._spin_combat_pm.setMinimumWidth(90)
        self._spin_combat_pm.setToolTip("Points de Mouvement par tour (standard : 3-5)")
        stats_row.addWidget(self._spin_combat_pm)

        stats_row.addSpacing(20)
        stats_row.addWidget(QLabel("Bonus PO :"))
        self._spin_combat_po_bonus = QSpinBox()
        self._spin_combat_po_bonus.setRange(0, 15)
        self._spin_combat_po_bonus.setValue(0)
        self._spin_combat_po_bonus.setMinimumWidth(90)
        self._spin_combat_po_bonus.setToolTip(
            "Bonus de Portée (PO) de ton stuff/buff.\n"
            "Ex: si tu as +3 PO d'items, mets 3.\n"
            "S'ajoute à la portée max de tous les sorts à portée modifiable."
        )
        stats_row.addWidget(self._spin_combat_po_bonus)

        stats_row.addStretch()
        grp_stats_lay.addLayout(stats_row)

        root.addWidget(grp_stats)

        # ═══════════════════════════════════════════════════
        # BOUTON LANCER (gros, vert, en bas)
        # ═══════════════════════════════════════════════════
        self._combat_start_btn = _make_primary_button("⚔️  LANCER LE COMBAT", kind="success")
        self._combat_start_btn.setEnabled(False)
        self._combat_start_btn.clicked.connect(self._start_combat)
        root.addWidget(self._combat_start_btn)

        self._combat_selected_class: str | None = None
        self._combat_worker = None  # CombatRunnerWorker
        self._hunt_worker = None    # HuntWorker (optionnel, mode aggro)
        return page

    def _select_class(self, class_id: str) -> None:
        self._combat_selected_class = class_id
        for cid, btn in self._combat_class_buttons.items():
            btn.setChecked(cid == class_id)
        cls = next((c for c in get_catalog().classes if c.id == class_id), None)
        if cls:
            self._combat_selected_label.setText(f"Classe : <b>{cls.nom_fr}</b>")
        self._combat_start_btn.setEnabled(True)
        self._update_spells_hint(class_id)
        self._populate_spell_slots(class_id)

    def _on_llm_provider_changed(self, _idx: int) -> None:
        """Adapte le modèle par défaut quand on change de provider + affiche/cache la clé API."""
        provider = self._combo_llm_provider.currentData()
        if provider == "ollama":
            self._combat_vision_model.setCurrentText("qwen2.5vl:3b")
        elif provider == "lmstudio":
            self._combat_vision_model.setCurrentText("qwen/qwen2.5-vl-3b-instruct")
        elif provider == "gemini":
            self._combat_vision_model.setCurrentText("gemini-2.5-flash")
        elif provider == "anthropic":
            self._combat_vision_model.setCurrentText("claude-haiku-4-5-20251001")
        # Affiche les champs clé API selon provider
        if hasattr(self, "_gemini_key_container"):
            self._gemini_key_container.setVisible(provider == "gemini")
        if hasattr(self, "_anthropic_key_container"):
            self._anthropic_key_container.setVisible(provider == "anthropic")

    def _open_gemini_key_page(self) -> None:
        """Ouvre la page Google AI Studio pour créer une clé API."""
        try:
            import webbrowser  # noqa: PLC0415
            webbrowser.open("https://aistudio.google.com/app/apikey")
        except Exception as exc:
            QMessageBox.information(
                self,
                "Ouvre manuellement",
                f"Va sur : https://aistudio.google.com/app/apikey\n\nErreur : {exc}",
            )

    def _open_anthropic_key_page(self) -> None:
        """Ouvre la page Anthropic Console pour créer une clé API."""
        try:
            import webbrowser  # noqa: PLC0415
            webbrowser.open("https://console.anthropic.com/settings/keys")
        except Exception as exc:
            QMessageBox.information(
                self,
                "Ouvre manuellement",
                f"Va sur : https://console.anthropic.com/settings/keys\n\nErreur : {exc}",
            )

    @staticmethod
    def _auto_detect_llm_provider() -> tuple[str, str]:
        """Teste Ollama puis LM Studio. Retourne (provider, url) du 1er qui répond."""
        try:
            import requests  # noqa: PLC0415
        except ImportError:
            return ("ollama", "")
        # Ollama
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=1.5)
            if r.status_code == 200:
                return ("ollama", "http://localhost:11434")
        except Exception:
            pass
        # LM Studio
        try:
            r = requests.get("http://localhost:1234/v1/models", timeout=1.5)
            if r.status_code == 200:
                return ("lmstudio", "http://localhost:1234/v1")
        except Exception:
            pass
        return ("ollama", "")  # fallback

    def _refresh_llm_models(self) -> None:
        """Charge la liste des modèles installés et remplit le dropdown."""
        provider = self._combo_llm_provider.currentData()
        if provider == "auto":
            provider, _ = self._auto_detect_llm_provider()
        try:
            from src.services.llm_client import LLMClient  # noqa: PLC0415
            client = LLMClient(provider=provider)
            if not client.is_available():
                self._lbl_llm_status.setText(
                    f"<span style='color:#e53935'>✗ {provider} indisponible "
                    f"({client.base_url}). Lance-le d'abord.</span>"
                )
                return
            models = client.list_models()
            if not models:
                self._lbl_llm_status.setText(
                    f"<span style='color:#fb8c00'>⚠ {provider} OK mais aucun modèle installé. "
                    f"Pull-en un (ex: ollama pull gemma3:12b).</span>"
                )
                return
            # Remplit le dropdown
            current = self._combat_vision_model.currentText()
            self._combat_vision_model.clear()
            self._combat_vision_model.addItems(models)
            if current in models:
                self._combat_vision_model.setCurrentText(current)
            else:
                # Choisit le premier modèle multimodal si possible
                vision_hints = ("vision", "gemma3", "llava", "minicpm", "qwen2.5vl")
                pick = next(
                    (m for m in models if any(h in m.lower() for h in vision_hints)),
                    models[0],
                )
                self._combat_vision_model.setCurrentText(pick)
            self._lbl_llm_status.setText(
                f"<span style='color:#43a047'>✓ {provider} connecté — "
                f"{len(models)} modèle(s) disponibles. Sélectionné : {self._combat_vision_model.currentText()}</span>"
            )
        except Exception as exc:
            self._lbl_llm_status.setText(
                f"<span style='color:#e53935'>✗ Erreur : {exc}</span>"
            )

    def _test_llm_now(self) -> None:
        """Envoie un screenshot + un prompt de test au LLM et affiche la réponse."""
        if self._vision is None:
            QMessageBox.warning(self, "Vision indisponible", "Service vision pas prêt.")
            return
        if self._combat_selected_class is None:
            QMessageBox.information(
                self, "Choisis une classe",
                "Sélectionne d'abord ta classe pour que l'IA connaisse tes sorts.",
            )
            return

        provider = self._combo_llm_provider.currentData() or "auto"
        llm_url = ""
        if provider == "auto":
            provider, llm_url = self._auto_detect_llm_provider()

        self._lbl_llm_status.setText(
            f"<span style='color:#1976d2'>⏳ Envoi d'une capture au LLM ({provider})...</span>"
        )

        from PyQt6.QtCore import QThreadPool, QRunnable, QObject, pyqtSignal as _Sig  # noqa: PLC0415

        class _TestSig(QObject):
            done = _Sig(str, str)  # (result_text, color)

        class _TestJob(QRunnable):
            def __init__(self_inner, vision, provider, url, model, cls, shortcuts, api_key):
                super().__init__()
                self_inner.vision = vision
                self_inner.provider = provider
                self_inner.url = url
                self_inner.model = model
                self_inner.cls = cls
                self_inner.shortcuts = shortcuts
                self_inner.api_key = api_key
                self_inner.sig = _TestSig()

            def run(self_inner):
                try:
                    import time as _time  # noqa: PLC0415
                    import json as _json  # noqa: PLC0415
                    from src.services.llm_client import LLMClient  # noqa: PLC0415
                    from src.services.vision_combat_worker import (  # noqa: PLC0415
                        _load_master_prompt, _build_class_section,
                    )
                    from src.services.combat_knowledge import CombatKnowledge  # noqa: PLC0415

                    client = LLMClient(
                        provider=self_inner.provider,
                        model=self_inner.model,
                        base_url=self_inner.url or None,
                        api_key=self_inner.api_key or None,
                        timeout_sec=60.0,
                    )
                    if not client.is_available():
                        msg = f"✗ {self_inner.provider} indisponible"
                        if self_inner.provider == "gemini" and not self_inner.api_key:
                            msg += " (clé API manquante)"
                        self_inner.sig.done.emit(msg, "#e53935")
                        return

                    frame = self_inner.vision.capture()
                    h, w = frame.shape[:2]
                    kb = CombatKnowledge()
                    class_hdr, sorts_desc = _build_class_section(kb, self_inner.cls)
                    tpl = _load_master_prompt()
                    system = (tpl or "Tu es un joueur Dofus.").replace("{width}", str(w)) \
                        .replace("{height}", str(h)) \
                        .replace("{class_info}", class_hdr) \
                        .replace("{sorts_description}", sorts_desc)

                    shortcuts_str = ", ".join(
                        f"{k}={v}" for k, v in sorted(self_inner.shortcuts.items())
                    )
                    user = (
                        f"TEST : analyse l'écran actuel. Classe : {self_inner.cls}. "
                        f"Sorts : {shortcuts_str or '(aucun)'}. Réponds en JSON."
                    )

                    t0 = _time.time()
                    decision = client.ask_json(user, system=system, image_bgr=frame, fallback={})
                    elapsed = _time.time() - t0

                    if not decision:
                        self_inner.sig.done.emit(
                            f"⚠ LLM a répondu mais JSON invalide ({elapsed:.1f}s)",
                            "#fb8c00",
                        )
                        return
                    summary = _json.dumps(decision, ensure_ascii=False, indent=2)[:500]
                    self_inner.sig.done.emit(
                        f"✓ Réponse en {elapsed:.1f}s\n{summary}",
                        "#43a047",
                    )
                except Exception as exc:
                    self_inner.sig.done.emit(f"✗ Erreur : {exc}", "#e53935")

        shortcuts = self._collect_spell_shortcuts()
        # Récupère la clé API selon provider (depuis UI, sinon prefs)
        api_key = ""
        if provider == "gemini":
            api_key = self._edit_gemini_key.text().strip()
            if not api_key:
                try:
                    from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
                    api_key = getattr(get_user_prefs().global_prefs, "gemini_api_key", "") or ""
                except Exception:
                    pass
        elif provider == "anthropic":
            api_key = self._edit_anthropic_key.text().strip()
            if not api_key:
                try:
                    from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
                    api_key = getattr(get_user_prefs().global_prefs, "anthropic_api_key", "") or ""
                except Exception:
                    pass
        job = _TestJob(
            vision=self._vision,
            provider=provider,
            url=llm_url,
            model=self._combat_vision_model.currentText().strip() or "gemini-2.5-flash",
            cls=self._combat_selected_class,
            shortcuts=shortcuts,
            api_key=api_key,
        )

        def _on_done(text: str, color: str) -> None:
            safe = text.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            self._lbl_llm_status.setText(f"<pre style='color:{color}'>{safe}</pre>")

        job.sig.done.connect(_on_done)
        QThreadPool.globalInstance().start(job)

    def _populate_spell_slots(self, class_id: str) -> None:
        """Met à jour les 9 QComboBox avec les sorts de la classe choisie."""
        if not hasattr(self, "_combat_spell_slots"):
            return
        try:
            from src.services.combat_knowledge import CombatKnowledge  # noqa: PLC0415
            kb = CombatKnowledge()
            cls_kb = kb.get_class(class_id)
        except Exception:
            cls_kb = None
        sorts = cls_kb.sorts if cls_kb else []
        for k, combo in self._combat_spell_slots.items():
            current_spell_id = combo.currentData()  # pour préserver la sélection user
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("— vide —", "")
            for s in sorts:
                display = s.get("nom", s.get("id", "?"))
                sid = s.get("id", "")
                combo.addItem(display, sid)
            # Restaure la sélection si possible
            if current_spell_id:
                idx = combo.findData(current_spell_id)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _autofill_spells(self) -> None:
        """Place les sorts de la classe dans les slots 1-N dans l'ordre du knowledge."""
        if self._combat_selected_class is None:
            QMessageBox.information(
                self, "Choisis une classe",
                "Sélectionne d'abord ta classe pour que je sache quoi pré-remplir.",
            )
            return
        try:
            from src.services.combat_knowledge import CombatKnowledge  # noqa: PLC0415
            kb = CombatKnowledge()
            cls_kb = kb.get_class(self._combat_selected_class)
            if cls_kb is None or not cls_kb.sorts:
                QMessageBox.warning(
                    self, "Classe sans knowledge",
                    f"Aucun knowledge base pour '{self._combat_selected_class}'.",
                )
                return
            # Place chaque sort dans le slot correspondant (1→N)
            for i, sort in enumerate(cls_kb.sorts[:9], 1):
                spell_id = sort.get("id", "")
                combo = self._combat_spell_slots.get(i)
                if combo is None:
                    continue
                idx = combo.findData(spell_id)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            self._on_farm_log(
                f"🪄 {min(len(cls_kb.sorts), 9)} sorts pré-remplis — ajuste si tes touches Dofus sont différentes",
                "info",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", f"Impossible de pré-remplir : {exc}")

    def _clear_spell_slots(self) -> None:
        """Remet tous les slots à 'vide'."""
        if not hasattr(self, "_combat_spell_slots"):
            return
        for combo in self._combat_spell_slots.values():
            combo.setCurrentIndex(0)

    def _collect_spell_shortcuts(self) -> dict[int, str]:
        """Lit les 9 slots et retourne {touche: spell_id}.

        Gère 2 cas :
          - item choisi dans la liste → retourne sa data (spell_id canonique)
          - texte tapé manuellement → normalise en snake_case
        """
        import unicodedata  # noqa: PLC0415
        result: dict[int, str] = {}
        if not hasattr(self, "_combat_spell_slots"):
            return result
        for k, combo in self._combat_spell_slots.items():
            current_text = combo.currentText().strip()
            if not current_text or current_text == "— vide —":
                continue
            # Cherche d'abord une data correspondant à ce texte
            idx = combo.findText(current_text)
            if idx >= 0:
                data = combo.itemData(idx)
                if data:
                    result[k] = data
                    continue
            # Sinon : normalise le texte tapé
            nfkd = unicodedata.normalize("NFKD", current_text)
            ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
            spell_id = (
                ascii_str.lower()
                .replace("'", "")
                .replace("-", "_")
                .replace(" ", "_")
            )
            while "__" in spell_id:
                spell_id = spell_id.replace("__", "_")
            spell_id = spell_id.strip("_")
            if spell_id:
                result[k] = spell_id
        return result

    def _update_spells_hint(self, class_id: str) -> None:
        """Met à jour l'aide des sorts avec les spell_id du knowledge base."""
        if not hasattr(self, "_combat_spells_hint"):
            return
        try:
            from src.services.combat_knowledge import CombatKnowledge  # noqa: PLC0415
            kb = CombatKnowledge()
            cls_kb = kb.get_class(class_id)
            if cls_kb is None:
                self._combat_spells_hint.setText(
                    f"<i>Pas de knowledge pour {class_id} — règles génériques utilisées.</i>"
                )
                return
            lines = [
                f"<b>Sorts connus de {cls_kb.nom_fr}</b> (utilise ces noms ou les id) :"
            ]
            for s in cls_kb.sorts:
                pa = s.get("pa", "?")
                po = f"{s.get('po_min','?')}-{s.get('po_max','?')}"
                lines.append(
                    f"  • {s.get('nom','?')} "
                    f"<span style='color:#808080'>(id=<code>{s.get('id')}</code>, "
                    f"{pa} PA, PO {po})</span>"
                )
            self._combat_spells_hint.setText("<br>".join(lines))
            self._combat_spells_hint.setTextFormat(Qt.TextFormat.RichText)
        except Exception as exc:
            logger.debug("Update spells hint échoué : {}", exc)

    def _start_combat(self) -> None:
        if self._combat_selected_class is None:
            return
        if self._selected_window is None:
            QMessageBox.warning(self, "Fenêtre Dofus introuvable",
                                "Aucune fenêtre Dofus détectée. Ouvre le jeu puis reviens ici.")
            return
        if self._vision is None:
            QMessageBox.critical(self, "Erreur", "Service vision indisponible.")
            return

        cls = next((c for c in get_catalog().classes if c.id == self._combat_selected_class), None)
        nom = cls.nom_fr if cls else self._combat_selected_class
        strat = self._combat_strategy_combo.currentData()
        logger.info("Démarrage combat : {} ({}) sur {}", nom, strat, self._selected_window.title)

        # Lit les 9 slots de la barre de sorts (plus de parsing texte)
        spell_shortcuts = self._collect_spell_shortcuts()
        if not spell_shortcuts:
            QMessageBox.warning(
                self, "Aucun sort configuré",
                "Place au moins un sort dans la barre 1-9.\n"
                "Clique 🪄 Pré-remplir ou sélectionne manuellement.",
            )
            return

        self._active_session_label = f"{CLASSE_ICONS.get(self._combat_selected_class, '•')}  {nom} — {strat}"
        self.session_started.emit(f"combat:{self._combat_selected_class}:{strat}")

        self._selected_window.focus()

        # Vision IA systématique — auto-détection provider si "auto"
        if self._chk_combat_vision.isChecked():
            from src.services.vision_combat_worker import (  # noqa: PLC0415
                VisionCombatConfig, VisionCombatWorker,
            )
            provider = self._combo_llm_provider.currentData() or "ollama"
            llm_url = ""
            if provider == "auto":
                provider, llm_url = self._auto_detect_llm_provider()
                self._on_farm_log(
                    f"🔍 Auto-détection LLM : provider={provider} url={llm_url or '(défaut)'}",
                    "info",
                )
            # Clé API (Gemini ou Anthropic selon provider)
            llm_api_key = ""
            if provider == "gemini":
                llm_api_key = self._edit_gemini_key.text().strip()
                if not llm_api_key:
                    QMessageBox.warning(
                        self, "Clé API manquante",
                        "Gemini nécessite une clé API gratuite.\n"
                        "Clique sur '🌐 Obtenir une clé' pour l'obtenir en 30s.",
                    )
                    return
                try:
                    from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
                    prefs = get_user_prefs()
                    if hasattr(prefs.global_prefs, "gemini_api_key"):
                        prefs.global_prefs.gemini_api_key = llm_api_key
                        prefs.save()
                except Exception as exc:
                    logger.debug("Save gemini key échec : {}", exc)
            elif provider == "anthropic":
                llm_api_key = self._edit_anthropic_key.text().strip()
                if not llm_api_key:
                    QMessageBox.warning(
                        self, "Clé API manquante",
                        "Anthropic Claude nécessite une clé API (sk-ant-...).\n"
                        "Clique sur '🌐 Obtenir une clé' sur console.anthropic.com.\n"
                        "Coût : ~$0.1/jour avec Haiku 4.5.",
                    )
                    return
                try:
                    from src.services.user_prefs import get_user_prefs  # noqa: PLC0415
                    prefs = get_user_prefs()
                    if hasattr(prefs.global_prefs, "anthropic_api_key"):
                        prefs.global_prefs.anthropic_api_key = llm_api_key
                        prefs.save()
                except Exception as exc:
                    logger.debug("Save anthropic key échec : {}", exc)

            vcfg = VisionCombatConfig(
                class_name=self._combat_selected_class,
                spell_shortcuts=spell_shortcuts,
                llm_provider=provider,
                llm_model=self._combat_vision_model.currentText().strip() or "claude-haiku-4-5-20251001",
                llm_url=llm_url,
                llm_api_key=llm_api_key,
                dofus_window_title=self._selected_window.title,
                starting_pa=self._spin_combat_pa.value(),
                starting_pm=self._spin_combat_pm.value(),
                po_bonus=self._spin_combat_po_bonus.value(),
                save_debug_images=self._chk_save_debug.isChecked(),
            )
            worker = VisionCombatWorker(
                vision=self._vision, input_svc=self._input, config=vcfg,
            )
            self._on_farm_log(
                f"🧠 IA Vision activée ({provider}) — le LLM voit l'écran et joue",
                "info",
            )
        else:
            from src.services.combat_runner_worker import CombatConfig, CombatRunnerWorker  # noqa: PLC0415
            cfg = CombatConfig(
                class_name=self._combat_selected_class,
                spell_shortcuts=spell_shortcuts,
                use_ollama=self._chk_combat_ollama.isChecked(),
                ollama_model=self._combat_ollama_model.text().strip() or "phi3:mini",
                dofus_window_title=self._selected_window.title,
                starting_pa=self._spin_combat_pa.value(),
                starting_pm=self._spin_combat_pm.value(),
            )
            worker = CombatRunnerWorker(
                vision=self._vision, input_svc=self._input, config=cfg,
            )

        worker.log_event.connect(self._on_farm_log)
        worker.state_changed.connect(self._on_farm_state)
        worker.stopped.connect(self._on_combat_stopped)
        self._combat_worker = worker
        worker.start()

        # HuntWorker (scan + engage) : UNIQUEMENT en mode heuristique (pas Vision IA).
        # En Vision IA, le LLM gère tout le cycle : scan mob → engage → combat → rinse.
        # Lancer les 2 en parallèle crée des conflits (HuntWorker re-engage pendant un tour).
        is_vision_ai = self._chk_combat_vision.isChecked()
        if self._chk_aggro_mode.isChecked() and not is_vision_ai:
            from src.services.hunt_worker import HuntConfig, HuntWorker  # noqa: PLC0415
            hunt_cfg = HuntConfig()
            hunt = HuntWorker(
                vision=self._vision, input_svc=self._input, config=hunt_cfg,
            )
            hunt.log_event.connect(self._on_farm_log)
            hunt.state_changed.connect(self._on_farm_state)
            hunt.stopped.connect(self._on_hunt_stopped)
            self._hunt_worker = hunt
            hunt.start()
            self._on_farm_log("🏹 Mode Aggro (heuristique) : scan + engage via HSV", "info")
        elif self._chk_aggro_mode.isChecked() and is_vision_ai:
            self._on_farm_log(
                "🏹 Mode Aggro via IA Vision : le LLM scanne et engage directement "
                "(pas de HuntWorker en parallèle)",
                "info",
            )

        self._go_to(3)
        self._log_lines.clear()
        self._refresh_timer.start()

    def _on_combat_stopped(self) -> None:
        self._combat_worker = None
        # Si le hunt tourne encore, arrête-le aussi
        if self._hunt_worker is not None:
            try:
                self._hunt_worker.request_stop()
            except Exception:
                pass
        self._refresh_timer.stop()
        self.session_stopped.emit()
        self._go_to(0)

    def _on_hunt_stopped(self) -> None:
        self._hunt_worker = None

    @staticmethod
    def _parse_spell_shortcuts(text: str) -> dict[int, str]:
        """Parse 'clé=nom, clé=nom' → dict {int: spell_id}.

        Le nom est normalisé en spell_id canonique (lowercase + underscores)
        pour matcher le knowledge base : "Griffe Iop" → "griffe_iop".
        Accent/apostrophes retirés pour robustesse.
        """
        import unicodedata  # noqa: PLC0415
        result: dict[int, str] = {}
        for chunk in (text or "").split(","):
            chunk = chunk.strip()
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            try:
                key = int(k.strip())
                if 1 <= key <= 9:
                    # Normalisation : retire accents, minuscules, espaces→_
                    raw = v.strip()
                    nfkd = unicodedata.normalize("NFKD", raw)
                    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
                    spell_id = (
                        ascii_str.lower()
                        .replace("'", "")
                        .replace("-", "_")
                        .replace(" ", "_")
                    )
                    # Collapse multiple underscores
                    while "__" in spell_id:
                        spell_id = spell_id.replace("__", "_")
                    result[key] = spell_id.strip("_")
            except ValueError:
                continue
        return result

    # ---------------------------------------------------------------------
    # Page 3 : En cours
    # ---------------------------------------------------------------------

    def _build_running(self) -> QWidget:
        from PyQt6.QtWidgets import QPlainTextEdit  # noqa: PLC0415

        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        self._running_icon = QLabel("🟢")
        self._running_icon.setFont(QFont("Segoe UI", 48))
        self._running_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._running_icon)

        self._running_label = QLabel("En cours…")
        self._running_label.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self._running_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._running_label)

        self._running_stats = QLabel("Actions : 0   |   Changements de map : 0   |   Runtime : 00:00:00")
        self._running_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._running_stats.setStyleSheet("color: #b0b0b0; font-size: 12pt;")
        root.addWidget(self._running_stats)

        # Journal live
        journal_title = QLabel("📜 Journal :")
        journal_title.setStyleSheet("color: #b0b0b0; font-size: 10pt; margin-top: 10px;")
        root.addWidget(journal_title)

        self._running_log = QPlainTextEdit()
        self._running_log.setReadOnly(True)
        self._running_log.setStyleSheet(
            "QPlainTextEdit { background-color: #16161f; color: #c0c0c0; "
            "font-family: Consolas, monospace; font-size: 10pt; border: 1px solid #333; border-radius: 6px; padding: 8px; }"
        )
        self._running_log.setMaximumHeight(220)
        self._running_log.setPlainText("(le bot va démarrer…)")
        root.addWidget(self._running_log)

        stop_btn = _make_primary_button("⏹  ARRÊTER", kind="danger")
        stop_btn.setMinimumHeight(64)
        stop_btn.clicked.connect(self._stop_session)
        root.addWidget(stop_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        return page

    def _refresh_running_view(self) -> None:
        stats = self._last_farm_stats
        if stats is None:
            runtime_str = "00:00:00"
            actions = 0
            maps = 0
            errors = 0
        else:
            hh, rem = divmod(int(stats.runtime_sec), 3600)
            mm, ss = divmod(rem, 60)
            runtime_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
            actions = stats.actions_count
            maps = stats.map_changes
            errors = stats.errors

        self._running_label.setText(f"En cours : {self._active_session_label}")
        self._running_stats.setText(
            f"Actions : {actions}   |   Changements de map : {maps}   |   Erreurs : {errors}   |   Runtime : {runtime_str}"
        )
        # Met à jour le journal si visible
        if hasattr(self, "_running_log") and self._log_lines:
            self._running_log.setPlainText("\n".join(self._log_lines[-12:]))

    def _on_farm_stats(self, stats: object) -> None:
        if isinstance(stats, FarmStats):
            self._last_farm_stats = stats

    def _on_farm_log(self, msg: str, level: str) -> None:
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        self._log_lines.append(f"[{ts}] {msg}")
        # Garde la liste bornée
        self._log_lines = self._log_lines[-200:]

    def _on_farm_state(self, state: str) -> None:
        icons = {"scanning": "🔍", "harvesting": "🌲", "moving": "🗺", "stopped": "⏹"}
        self._running_icon.setText(icons.get(state, "🟢"))

    def _on_farm_stopped(self) -> None:
        self._farm_worker = None
        self._refresh_timer.stop()
        self.session_stopped.emit()
        # Retour à l'accueil automatique
        QTimer.singleShot(500, lambda: self._go_to(0))

    def _stop_session(self) -> None:
        logger.info("Session arrêtée par l'utilisateur")
        if self._farm_worker is not None:
            self._farm_worker.request_stop()
            if not self._farm_worker.wait(3000):
                logger.warning("FarmWorker n'a pas terminé en 3s, abandon forcé")
                self._farm_worker.terminate()
            self._farm_worker = None
        if self._combat_worker is not None:
            self._combat_worker.request_stop()
            if not self._combat_worker.wait(3000):
                logger.warning("CombatWorker n'a pas terminé en 3s, abandon forcé")
                self._combat_worker.terminate()
            self._combat_worker = None
        if self._hunt_worker is not None:
            self._hunt_worker.request_stop()
            if not self._hunt_worker.wait(3000):
                logger.warning("HuntWorker n'a pas terminé en 3s, abandon forcé")
                self._hunt_worker.terminate()
            self._hunt_worker = None
        self._refresh_timer.stop()
        self.session_stopped.emit()
        self._go_to(0)

    # ---------------------------------------------------------------------
    # Fenêtre Dofus
    # ---------------------------------------------------------------------

    def _auto_detect_window(self) -> None:
        best = self._detector.best()
        self._selected_window = best
        self._update_window_footer()
        self._sync_vision_target()

    def _sync_vision_target(self) -> None:
        """Propage la fenêtre sélectionnée au service vision pour la capture."""
        if self._vision is None:
            return
        if self._selected_window is None:
            self._vision.set_target_window(None)
        else:
            self._vision.set_target_window(self._selected_window)

    def _update_window_footer(self) -> None:
        if self._selected_window:
            w = self._selected_window
            self._window_footer.setText(
                f"<b>Fenêtre Dofus :</b> {w.title} "
                f"<span style='color:#b0b0b0'>({w.width}×{w.height}, score {w.score:.0f})</span>"
            )
        else:
            self._window_footer.setText(
                "<span style='color:#ef5350'>⚠️ Aucune fenêtre Dofus détectée.</span> "
                "Ouvre le jeu en mode fenêtré puis reviens."
            )

    def _open_window_picker(self) -> None:
        dlg = WindowPickerDialog(self._settings.dofus_window_title, parent=self)
        if dlg.exec() == WindowPickerDialog.DialogCode.Accepted and dlg.selected_window:
            self._selected_window = dlg.selected_window
            self._selected_window.focus()
            self._update_window_footer()
            self._sync_vision_target()

    def _go_to(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        if index == 0:
            self._auto_detect_window()
