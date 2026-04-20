"""Service de téléportation via le menu zaap de Dofus (commande `.zaap`).

Flow type :
    zaap = ZaapService(vision, input_svc, chat_svc, map_locator)
    result = zaap.teleport_to("ingalsse")   # tape dans la search bar
    if result.success:
        print("Arrivé à", result.new_map)

Technique :
    1. Envoie `.zaap` via ChatService → ouvre le menu
    2. Attend ~1.5 s que le menu s'affiche
    3. Clique la barre de recherche (position relative à la fenêtre Dofus)
    4. Tape la query → la liste se filtre
    5. Clique la première ligne de résultat (couleur distinctive si sélectionnée)
    6. Clique "Se téléporter"
    7. Attend que la map change via `MapLocator.locate()` en boucle

Les positions UI sont des **ratios relatifs à la fenêtre Dofus** (menu centré,
taille proportionnelle à la fenêtre). Si les ratios ne conviennent pas, ils
peuvent être surchargés via `ZaapUiRatios`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from src.services.chat_service import ChatService
from src.services.input_service import InputService
from src.services.map_locator import MapInfo, MapLocator
from src.services.vision import MssVisionService


class ZaapOutcome(str, Enum):
    SUCCESS = "success"
    FAIL_NO_WINDOW = "no_window"
    FAIL_MENU_NOT_OPEN = "menu_not_open"
    FAIL_NO_MAP_CHANGE = "no_map_change"
    FAIL_EXCEPTION = "exception"


@dataclass
class ZaapResult:
    outcome: ZaapOutcome
    message: str = ""
    before_map: MapInfo | None = None
    after_map: MapInfo | None = None

    @property
    def success(self) -> bool:
        return self.outcome == ZaapOutcome.SUCCESS


@dataclass
class ZaapUiRatios:
    """Coordonnées UI exprimées en ratios de la fenêtre Dofus.

    Le menu zaap est centré sur la fenêtre. Double-clic sur la première ligne
    de résultat = téléportation directe (pas besoin du bouton "Se téléporter").
    """
    # Première ligne de résultat (sous les headers de colonne)
    first_row_x: float = 0.360
    first_row_y: float = 0.334


class ZaapService:
    """Téléporte via le menu Zaap (requiert zaap de poche ou équivalent)."""

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        chat_svc: ChatService,
        map_locator: MapLocator,
        window_title: str | None = None,
        ratios: ZaapUiRatios | None = None,
        open_menu_wait_sec: float = 1.5,
        filter_wait_sec: float = 0.6,
        post_click_wait_sec: float = 0.35,
        map_change_timeout_sec: float = 25.0,
        map_poll_interval_sec: float = 1.0,
    ) -> None:
        self._vision = vision
        self._input = input_svc
        self._chat = chat_svc
        self._locator = map_locator
        self._window_title = window_title
        self._ratios = ratios or ZaapUiRatios()
        self._open_menu_wait = open_menu_wait_sec
        self._filter_wait = filter_wait_sec
        self._post_click_wait = post_click_wait_sec
        self._map_change_timeout = map_change_timeout_sec
        self._map_poll = map_poll_interval_sec

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def teleport_to(
        self,
        destination_query: str,
        expected_coords: tuple[int, int] | None = None,
    ) -> ZaapResult:
        """Téléporte vers la destination matchant `destination_query`.

        Args:
            destination_query: texte tapé dans la search bar (ex: "ingalsse").
                Peut être un bout du nom, un nom de région, ou des coords.
            expected_coords: si fourni, valide que le personnage arrive bien
                sur ces coords après le loading. Sinon : considère succès dès
                que la map change.
        """
        try:
            return self._do_teleport(destination_query, expected_coords)
        except Exception as exc:
            logger.exception("ZaapService : exception pendant teleport_to")
            return ZaapResult(
                outcome=ZaapOutcome.FAIL_EXCEPTION,
                message=str(exc),
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _do_teleport(
        self,
        destination_query: str,
        expected_coords: tuple[int, int] | None,
    ) -> ZaapResult:
        # 1. Position avant
        before = self._locator.locate()
        if before is not None and before.is_valid:
            logger.info("ZaapService : position avant TP = {}", before)

        # 2. Récupère la bounding box de la fenêtre Dofus
        win = self._get_window_rect()
        if win is None:
            return ZaapResult(
                outcome=ZaapOutcome.FAIL_NO_WINDOW,
                message="Fenêtre Dofus introuvable",
                before_map=before,
            )
        wx, wy, ww, wh = win
        r = self._ratios

        click_focus = (wx + ww // 2, wy + wh // 2)
        first_row = (wx + int(ww * r.first_row_x), wy + int(wh * r.first_row_y))

        # 3. Ouvre le menu zaap
        # À l'ouverture, la search bar est directement active — on peut taper
        # tout de suite sans clic ni Espace supplémentaire.
        logger.info("ZaapService : envoi .zaap (focus click {})", click_focus)
        self._chat.send_command(".zaap", click_at=click_focus)
        time.sleep(self._open_menu_wait)

        # 4. Tape la query directement (search bar auto-focused à l'ouverture)
        logger.info("ZaapService : tape '{}'", destination_query)
        self._input.type_text(destination_query)
        time.sleep(self._filter_wait)

        # 5. Double-clic sur la première ligne = téléportation directe
        logger.info("ZaapService : double-clic première ligne {}", first_row)
        self._input.double_click(first_row[0], first_row[1], button="left", jitter=False)

        # 6. Attend le changement de map
        return self._wait_for_map_change(before, expected_coords)

    def _wait_for_map_change(
        self,
        before: MapInfo | None,
        expected_coords: tuple[int, int] | None,
    ) -> ZaapResult:
        start = time.time()
        # Laisse le temps au loading de commencer
        time.sleep(1.0)
        while time.time() - start < self._map_change_timeout:
            current = self._locator.locate()
            if current is not None and current.is_valid:
                # Si on a des coords attendues, on les attend explicitement
                if expected_coords is not None:
                    if current.coords == expected_coords:
                        logger.info("ZaapService : arrivé à {} ✓", current)
                        return ZaapResult(
                            outcome=ZaapOutcome.SUCCESS,
                            message=f"Arrivé à {current}",
                            before_map=before,
                            after_map=current,
                        )
                else:
                    # Sinon : on accepte tout changement de coords
                    before_coords = before.coords if before else None
                    if before_coords is None or current.coords != before_coords:
                        logger.info("ZaapService : map changée → {} ✓", current)
                        return ZaapResult(
                            outcome=ZaapOutcome.SUCCESS,
                            message=f"Map changée : {current}",
                            before_map=before,
                            after_map=current,
                        )
            time.sleep(self._map_poll)

        return ZaapResult(
            outcome=ZaapOutcome.FAIL_NO_MAP_CHANGE,
            message=f"Timeout {self._map_change_timeout:.0f}s — map n'a pas changé",
            before_map=before,
            after_map=current if "current" in dir() else None,
        )

    def _get_window_rect(self) -> tuple[int, int, int, int] | None:
        """Retourne (left, top, width, height) de la fenêtre Dofus ou None."""
        if not self._window_title:
            return None
        try:
            import pygetwindow as gw  # noqa: PLC0415
            matches = gw.getWindowsWithTitle(self._window_title)
            if not matches:
                return None
            w = matches[0]
            if w.isMinimized:
                w.restore()
            try:
                w.activate()
            except Exception:
                pass
            return (int(w.left), int(w.top), int(w.width), int(w.height))
        except Exception as exc:
            logger.warning("ZaapService : get window rect échoué — {}", exc)
            return None
