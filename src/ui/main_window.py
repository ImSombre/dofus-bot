"""Main window — QMainWindow with 5 tabs wired to real widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from src.ui.widgets.config_widget import ConfigWidget
from src.ui.widgets.dashboard_widget import DashboardWidget
from src.ui.widgets.debug_widget import DebugWidget
from src.ui.widgets.discord_widget import DiscordWidget
from src.ui.widgets.metiers_widget import MetiersWidget
from src.ui.widgets.simple_dashboard import SimpleDashboardWidget
from src.ui.widgets.stats_widget import StatsWidget

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.handlers.state_machine import BotStateMachine
    from src.models.enums import BotState
    from src.models.game_state import GameState
    from src.services.persistence import PersistenceService
    from src.services.vision import MssVisionService


class MainWindow(QMainWindow):
    """Top-level window with 5 tabs (Dashboard, Debug, Config, Stats, Discord)."""

    def __init__(
        self,
        state_machine: "BotStateMachine",
        persistence: "PersistenceService",
        settings: "Settings",
        vision: "MssVisionService | None" = None,
    ) -> None:
        super().__init__()
        self._sm = state_machine
        self._persistence = persistence
        self._settings = settings
        self._vision = vision

        self.setWindowTitle(f"Dofus Bot — v{settings.version}")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)

        self._tabs = QTabWidget(self)
        self.setCentralWidget(self._tabs)

        # Nouveau dashboard simplifié (wizard)
        self._simple_dashboard = SimpleDashboardWidget(
            state_machine=state_machine,
            settings=settings,
            vision=vision,
            parent=self,
        )
        # Onglets avancés (gardés accessibles mais moins mis en avant)
        self._dashboard = self._make_dashboard()       # avancé
        self._debug = self._make_debug()
        self._metiers = MetiersWidget(parent=self)
        self._config = ConfigWidget(settings=settings, parent=self)
        self._stats = StatsWidget(persistence=persistence, parent=self)
        self._discord = DiscordWidget(settings=settings, parent=self)

        # Ordre onglets : simple d'abord, technique ensuite
        self._tabs.addTab(self._simple_dashboard, "  🏠  Accueil  ")
        self._tabs.addTab(self._metiers, "  📖  Catalogue métiers  ")
        self._tabs.addTab(self._debug, "  🔍  Debug vision  ")
        self._tabs.addTab(self._stats, "  📊  Statistiques  ")
        self._tabs.addTab(self._dashboard, "  ⚙️  Avancé  ")
        self._tabs.addTab(self._config, "  🔧  Configuration  ")
        self._tabs.addTab(self._discord, "  💬  Discord  ")

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Prêt  |  F5 : Démarrer  F7 : Pause  F6 : Arrêter  Ctrl+Shift+P : Arrêt d'urgence")

        self._install_shortcuts()

    # ---------- tab factory methods ----------

    def _make_dashboard(self) -> DashboardWidget:
        return DashboardWidget(
            state_machine=self._sm,
            settings=self._settings,
            parent=self,
        )

    def _make_debug(self) -> QWidget:
        if self._vision is not None:
            return DebugWidget(vision=self._vision, parent=self)
        # Fallback placeholder when vision is not injected
        from PyQt6.QtWidgets import QLabel, QVBoxLayout  # noqa: PLC0415

        w = QWidget(self)
        lay = QVBoxLayout(w)
        lbl = QLabel(
            "Vision non disponible (injectez MssVisionService via MainWindow(vision=...)).", w
        )
        lay.addWidget(lbl)
        return w

    # ---------- shortcuts ----------

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, activated=self._shortcut_start)
        QShortcut(QKeySequence("F6"), self, activated=self._shortcut_stop)
        QShortcut(QKeySequence("F7"), self, activated=self._shortcut_pause)
        QShortcut(QKeySequence("Ctrl+Shift+P"), self, activated=self._shortcut_panic)

    def _shortcut_start(self) -> None:
        from src.models.enums import BotMode  # noqa: PLC0415

        self._sm.request_start(BotMode.FARM, self._settings.default_zone)
        self.statusBar().showMessage("Démarrage demandé (F5)")

    def _shortcut_stop(self) -> None:
        self._sm.request_stop()
        self.statusBar().showMessage("Arrêt demandé (F6)")

    def _shortcut_pause(self) -> None:
        self._sm.request_pause()
        self.statusBar().showMessage("Pause (F7)")

    def _shortcut_panic(self) -> None:
        logger.warning("PANIC stop triggered via Ctrl+Shift+P")
        self._sm.request_stop()
        self.statusBar().showMessage("PANIC — arrêt forcé")

    # ---------- slots wired from BotWorker signals ----------

    def on_state_changed(self, old: "BotState", new: "BotState") -> None:
        self.statusBar().showMessage(f"État : {new.name}")
        self._dashboard.on_state_changed(old, new)

    def on_stats_updated(self, gs: "GameState") -> None:
        self.statusBar().showMessage(
            f"État : {gs.state.name}  |  XP : {gs.xp_gained}  |  Actions : {gs.actions_count}"
        )
        self._dashboard.on_stats_updated(gs)
