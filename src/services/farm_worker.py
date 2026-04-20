"""Worker thread qui exécute une session de farm automatique.

Boucle :
    1. Capture de la fenêtre Dofus
    2. Scan des ressources via ColorShape (filtré HSV catalogue)
    3. Si ressource détectée :
         - Sélectionne la plus proche du centre
         - Clic droit dessus (récolte Dofus)
         - Attend l'animation
    4. Sinon : échec compté ; après N échecs consécutifs → tente de changer de map
       en cliquant sur un bord de la map (haut/bas/gauche/droite en rotation)
    5. Émet des signaux `stats_updated` et `log_event` pour l'UI

Thread-safe via QThread + flag `_stop_requested`. Aucun blocage de la UI.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.data.catalog import HsvRange, ResourceEntry, get_catalog
from src.models.detection import DetectedObject, Region
from src.services.chat_service import ChatService
from src.services.hsv_learner import HsvLearner, LearnedHsv
from src.services.input_service import InputService
from src.services.map_locator import MapLocator
from src.services.map_navigator import MapNavigator
from src.services.template_matcher import TemplateMatcher
from src.services.vision import MssVisionService
from src.services.zaap_service import ZaapService


@dataclass
class FarmConfig:
    metier: str                # "lumberjack", "farmer", ...
    niveau_personnage: int
    # Si None : toutes les ressources accessibles au niveau. Si liste d'IDs fournie :
    # ne cible QUE ces ressources (ex: ["ble"] pour ne pas récolter lin/houblon/chanvre).
    resource_ids: list[str] | None = None
    tick_interval_sec: float = 0.6
    max_no_target_ticks: int = 5   # nb de scans vides avant de tenter un changement de map
    animation_duration_sec: float = 1.5  # attente après clic récolte (ajuste selon ton outil/niveau)
    # Si True : détecte popups captcha/trade via OCR. Trop de faux positifs avec le chat Dofus → off par défaut.
    enable_popup_detection: bool = False
    # Debug : log détaillé du nombre de candidats par masque HSV
    verbose_scan: bool = True
    # Titre de la fenêtre Dofus — le worker va la remettre au premier plan avant chaque clic.
    dofus_window_title: str | None = None
    # Si > 0 : seuil de candidats bruts au-dessus duquel on considère que la détection est du bruit
    # et on SKIP le clic (évite de cliquer au hasard quand les HSV sont mal calés).
    noise_threshold: int = 80
    # Bouton utilisé pour récolter ("left" ou "right"). Sur la plupart des serveurs Dofus 2.x,
    # clic gauche = récolte directe ; clic droit = menu contextuel.
    harvest_button: str = "left"
    # Zones UI à exclure du scan (pixels depuis les bords). Évite de cliquer sur chat, HUD, etc.
    ui_margin_top: int = 50      # barre de menu haut
    ui_margin_bottom: int = 180  # barre de sorts + inventaire bas
    ui_margin_left: int = 10
    ui_margin_right: int = 10
    # Batch-click : clique toutes les ressources visibles dans l'ordre (plus proche → plus loin).
    # Dofus les met en file d'attente et le perso les enchaîne automatiquement.
    batch_click: bool = True
    batch_click_max: int = 8           # nb max de ressources cliquées par tick
    batch_click_delay_sec: float = 0.35  # pause entre deux clics (pour que Dofus enregistre)
    # Rotation zaaps : liste de requêtes à taper dans la search bar du menu zaap
    # (ex: ["ingalsse", "astrub"]). Quand la map est vide → TP vers le zaap suivant.
    # Si vide : fallback sur le vieux comportement "clic sur un bord de map".
    zaap_rotation: list[str] = field(default_factory=list)
    # Circuit de maps : liste de coords (x, y) à enchaîner via clics de bords.
    # Ex: [(9,5), (9,6), (8,6), (8,5)] → le bot farme chaque map puis passe à
    # la suivante via MapNavigator. Quand il arrive à la dernière, il reboucle
    # sur la première. Prioritaire sur zaap_rotation si non vide.
    circuit_maps: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class FarmStats:
    runtime_sec: float = 0.0
    actions_count: int = 0
    scans_count: int = 0
    no_target_ticks: int = 0
    map_changes: int = 0
    errors: int = 0


class FarmWorker(QThread):
    """Thread dédié à l'exécution d'une session de farm automatique."""

    # Signaux pour l'UI
    stats_updated = pyqtSignal(object)   # FarmStats
    log_event = pyqtSignal(str, str)     # (message, level: "info"/"warn"/"error")
    state_changed = pyqtSignal(str)       # "scanning" / "harvesting" / "moving" / "stopped"
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: FarmConfig,
        hsv_learner: HsvLearner | None = None,
        template_matcher: TemplateMatcher | None = None,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._learner = hsv_learner or HsvLearner()
        self._templates = template_matcher or TemplateMatcher()
        self._stats = FarmStats()
        self._stop_requested = False
        self._start_time = 0.0
        self._direction_index = 0  # pour rotation des bords de map
        # Index courant dans la rotation des zaaps (avance d'un cran à chaque map vide)
        self._zaap_index = 0
        # Lazy-init du ZaapService (seulement si config.zaap_rotation est non-vide)
        self._zaap_svc: ZaapService | None = None
        # Circuit de maps (coords x,y) → navigation map-par-map
        self._circuit_index = 0
        self._navigator: MapNavigator | None = None
        # Dernière position connue du perso (évite de redépendre d'un OCR flaky).
        # Initialisée à circuit[0] si le circuit est défini — l'user est censé
        # placer son perso sur la première map du circuit avant de lancer.
        self._last_known_pos: tuple[int, int] | None = (
            config.circuit_maps[0] if config.circuit_maps else None
        )

    # ---------- API publique ----------

    def request_stop(self) -> None:
        self._stop_requested = True

    def current_stats(self) -> FarmStats:
        return self._stats

    # ---------- Boucle principale ----------

    def run(self) -> None:
        self._start_time = time.time()
        self._stats = FarmStats()
        self.log_event.emit(f"🚀 Farm {self._config.metier} démarré (niv {self._config.niveau_personnage})", "info")
        self.state_changed.emit("scanning")

        catalog = get_catalog()
        available_resources = catalog.by_niveau_max(self._config.metier, self._config.niveau_personnage)
        # Filtrage par liste blanche si précisé
        if self._config.resource_ids:
            wanted = set(self._config.resource_ids)
            available_resources = [r for r in available_resources if r.id in wanted]

        # Sépare les ressources par méthode de détection
        with_templates = [r for r in available_resources if self._templates.has_template(r.id)]
        without_templates = [r for r in available_resources if not self._templates.has_template(r.id)]
        resources_hsv = self._compute_hsv_masks(without_templates)

        total = len(with_templates) + len(resources_hsv)
        if total == 0:
            self.log_event.emit(
                f"❌ Aucune ressource disponible au niveau {self._config.niveau_personnage}", "error"
            )
            self.stopped.emit()
            return

        if with_templates:
            self.log_event.emit(
                f"📷 {len(with_templates)} ressource(s) via template (précis) : "
                f"{', '.join(r.nom_fr for r in with_templates)}",
                "info",
            )
        self.log_event.emit(f"📋 {total} ressource(s) à scanner", "info")

        while not self._stop_requested:
            try:
                self._tick(resources_hsv, with_templates)
            except Exception as exc:
                logger.exception("Erreur dans la boucle farm")
                self.log_event.emit(f"⚠️ Erreur : {exc}", "error")
                self._stats.errors += 1

            # Met à jour runtime + signal
            self._stats.runtime_sec = time.time() - self._start_time
            self.stats_updated.emit(self._stats)

            # Attente avant prochain tick
            if not self._stop_requested:
                # Utilise msleep plutôt que time.sleep pour pouvoir réveiller
                self.msleep(int(self._config.tick_interval_sec * 1000))

        self.log_event.emit("⏹ Farm arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    # ---------- Tick ----------

    def _tick(
        self,
        resources_hsv: list[tuple[ResourceEntry, HsvRange, bool]],
        with_templates: list[ResourceEntry],
    ) -> None:
        self.state_changed.emit("scanning")
        frame = self._vision.capture()
        self._stats.scans_count += 1

        # Détection popup OFF par défaut (trop de faux positifs sur le chat Dofus)
        if self._config.enable_popup_detection:
            popup = self._vision.detect_popup_typed(frame)
            if popup is not None and popup.popup_type in ("captcha", "moderation"):
                # Seules les popups bloquantes justifient une pause
                self.log_event.emit(f"⏸ Popup critique : {popup.popup_type} — pause 10s", "warn")
                self.msleep(10000)
                return

        # 1) Template matching en priorité (beaucoup plus précis que HSV)
        template_candidates: list[tuple[ResourceEntry, DetectedObject]] = []
        for res in with_templates:
            matches = self._templates.find(frame, res.id)
            # Filtre zones UI
            matches = [
                m for m in matches
                if self._is_in_playable_area(m.box.x + m.box.w // 2, m.box.y + m.box.h // 2, frame.shape)
            ]
            for m in matches:
                template_candidates.append((res, m))

        # 2) HSV fallback pour les ressources sans template
        hsv_candidates = self._detect_resources(frame, resources_hsv)

        # Fusion : priorité aux templates (confiance plus élevée)
        candidates = template_candidates + hsv_candidates
        if self._config.verbose_scan:
            self.log_event.emit(
                f"🔬 Scan : {self._stats.scans_count} | candidats bruts : {len(candidates)}",
                "info",
            )

        # Si trop de candidats ET aucune détection via HSV apprise → probable bruit → skip
        # Quand la HSV est apprise (confidence >= 0.7), on fait confiance même avec beaucoup
        # de candidats — c'est juste une grosse zone de ressource fragmentée.
        if (
            self._config.noise_threshold > 0
            and len(candidates) > self._config.noise_threshold
            and not self._has_learned_detection(candidates)
        ):
            self.log_event.emit(
                f"⚠️ {len(candidates)} candidats non calibrés — skip "
                "(calibre la ressource pour activer le clic).",
                "warn",
            )
            return

        # Si beaucoup de candidats avec HSV apprise, on sélectionne le plus GROS blob
        # (probablement le plus gros cluster de ressources) plutôt que le plus proche du centre.
        if self._has_learned_detection(candidates) and len(candidates) > 10:
            # Trie par aire décroissante, garde le top 5, pick le plus proche du centre parmi ceux-là
            candidates.sort(key=lambda p: p[1].box.w * p[1].box.h, reverse=True)
            candidates = candidates[:5]

        if not candidates:
            self._stats.no_target_ticks += 1
            self.log_event.emit(f"🔍 Aucune ressource visible ({self._stats.no_target_ticks}/{self._config.max_no_target_ticks})", "info")
            if self._stats.no_target_ticks >= self._config.max_no_target_ticks:
                self._change_map(frame.shape)
                self._stats.no_target_ticks = 0
            return

        # Reset counter si on a trouvé
        self._stats.no_target_ticks = 0

        # Tri par distance au personnage (pas au centre géométrique) :
        # le perso est dans la zone jouable, donc plus bas que le centre quand l'UI
        # du bas est grosse — on vise la ressource la plus proche de lui, pas la plus
        # centrale à l'écran. Réduit les allers-retours visibles.
        char_fx, char_fy = self._character_frame_pos(frame.shape)
        candidates.sort(key=lambda p: (
            (p[1].box.x + p[1].box.w // 2 - char_fx) ** 2
            + (p[1].box.y + p[1].box.h // 2 - char_fy) ** 2
        ))

        self.state_changed.emit("harvesting")

        # Focus Dofus AVANT le batch (une seule fois — inutile de re-focus entre chaque clic)
        focused = self._ensure_dofus_focused()
        focus_info = "✓ focus" if focused else "✗ focus échoué"

        if self._config.batch_click:
            self._batch_harvest(candidates, focus_info)
        else:
            self._single_harvest(candidates[0], focus_info)

    def _single_harvest(
        self,
        candidate: tuple[ResourceEntry, DetectedObject],
        focus_info: str,
    ) -> None:
        """Ancien comportement : clique une seule ressource puis attend l'animation complète."""
        res, obj = candidate
        fx = obj.box.x + obj.box.w // 2
        fy = obj.box.y + obj.box.h // 2
        cx, cy = self._frame_to_screen(fx, fy)
        self.log_event.emit(
            f"🌲 Récolte {res.nom_fr} à écran=({cx},{cy}) frame=({fx},{fy}) {focus_info}",
            "info",
        )
        try:
            self._input.click(cx, cy, button=self._config.harvest_button)
            self._stats.actions_count += 1
            self.msleep(int(self._config.animation_duration_sec * 1000))
        except Exception as exc:
            self.log_event.emit(f"⚠️ Échec clic : {exc}", "error")
            self._stats.errors += 1

    def _batch_harvest(
        self,
        candidates: list[tuple[ResourceEntry, DetectedObject]],
        focus_info: str,
    ) -> None:
        """Clique toutes les ressources visibles (du plus proche au plus loin).

        Dofus file les actions : le perso enchaîne les récoltes tout seul. On attend
        ensuite la durée cumulée des animations avant le prochain scan.
        """
        batch = candidates[: self._config.batch_click_max]
        self.log_event.emit(
            f"🌾 Batch : {len(batch)} ressource(s) enfilées (Dofus enchaîne) {focus_info}",
            "info",
        )

        clicked = 0
        for res, obj in batch:
            if self._stop_requested:
                break
            fx = obj.box.x + obj.box.w // 2
            fy = obj.box.y + obj.box.h // 2
            cx, cy = self._frame_to_screen(fx, fy)
            try:
                self._input.click(cx, cy, button=self._config.harvest_button)
                self._stats.actions_count += 1
                clicked += 1
                self.log_event.emit(
                    f"  • {res.nom_fr} → ({cx},{cy})", "info",
                )
                # Petite pause pour que Dofus enregistre la file d'attente
                self.msleep(int(self._config.batch_click_delay_sec * 1000))
            except Exception as exc:
                self.log_event.emit(f"⚠️ Clic raté : {exc}", "error")
                self._stats.errors += 1

        if clicked == 0:
            return

        # Attend la fin de la file : ~animation_duration × nb de clics (légère décote
        # car Dofus pipeline un peu les déplacements).
        wait_sec = self._config.animation_duration_sec * clicked * 0.9
        wait_sec = min(wait_sec, 60.0)  # plafond de sécurité
        self.log_event.emit(
            f"⏳ Attente file Dofus : {wait_sec:.1f} s ({clicked} récoltes)", "info",
        )
        # Découpe le wait en tranches pour rester réactif au stop
        slice_ms = 200
        total_ms = int(wait_sec * 1000)
        elapsed = 0
        while elapsed < total_ms and not self._stop_requested:
            step = min(slice_ms, total_ms - elapsed)
            self.msleep(step)
            elapsed += step

    def _frame_to_screen(self, fx: int, fy: int) -> tuple[int, int]:
        """Convertit des coords (x,y) relatives au frame capturé → coords écran absolues.

        Utilise la région de la dernière capture exposée par la vision.
        """
        region = getattr(self._vision, "last_capture_region", None)
        if region is None:
            return fx, fy
        return region.x + fx, region.y + fy

    def _ensure_dofus_focused(self) -> bool:
        """Remet la fenêtre Dofus au premier plan avant un clic. Retourne True si succès."""
        title = self._config.dofus_window_title
        if not title:
            return True
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
                # Petite pause pour laisser Windows appliquer le focus
                self.msleep(100)
                return True
            except Exception:
                # Windows bloque parfois activate() — fallback via win32
                try:
                    import ctypes  # noqa: PLC0415
                    hwnd = w._hWnd
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    self.msleep(100)
                    return True
                except Exception:
                    return False
        except Exception as exc:
            logger.debug("Focus Dofus échoué : {}", exc)
            return False

    # ---------- Détection HSV ----------

    def _compute_hsv_masks(
        self, resources: list[ResourceEntry]
    ) -> list[tuple[ResourceEntry, HsvRange, bool]]:
        """Retourne les tuples (resource, hsv, is_learned) à scanner.

        Priorité : HSV apprise (via HsvLearner) > HSV estimée du catalogue.
        Le flag `is_learned` permet d'appliquer des plages plus strictes
        quand on a des valeurs précises (moins de faux positifs).
        """
        out = []
        learned_count = 0
        for r in resources:
            learned = self._learner.get(r.id)
            if learned is not None:
                out.append((r, HsvRange(h=learned.h, s=learned.s, v=learned.v, tolerance=learned.tolerance), True))
                learned_count += 1
            elif r.hsv_estime is not None:
                out.append((r, r.hsv_estime, False))
        if learned_count > 0:
            self.log_event.emit(
                f"🎯 {learned_count}/{len(out)} ressource(s) calibrée(s) — détection précise",
                "info",
            )
        elif out:
            self.log_event.emit(
                "⚠️ Aucune ressource calibrée (HSV estimées du catalogue). "
                "Utilise 'Calibrer' dans l'onglet Debug pour fiabiliser.",
                "warn",
            )
        return out

    def _detect_resources(
        self,
        frame: np.ndarray,
        resources_hsv: list[tuple[ResourceEntry, HsvRange, bool]],
    ) -> list[tuple[ResourceEntry, DetectedObject]]:
        """Applique un masque HSV par ressource, extrait les contours, retourne les candidats.

        Plages de saturation/valeur adaptatives :
          - HSV apprise (précise) → bande serrée ±25 S, ±35 V
          - HSV estimée (catalogue) → bande large ±70 S, ±70 V
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        results: list[tuple[ResourceEntry, DetectedObject]] = []

        for res, hsv_range, is_learned in resources_hsv:
            # Normalise H : si > 180 c'est en degrés 0-360, sinon déjà OpenCV
            h_raw = hsv_range.h
            h_center = h_raw // 2 if h_raw > 180 else h_raw
            h_center = max(0, min(179, h_center))

            # Plages serrées si la HSV est apprise → moins de faux positifs
            if is_learned:
                sv_pad_s = 25
                sv_pad_v = 35
                min_area = 400
                min_wh = 15
                close_kernel = 21  # merge agressif pour faire des gros blobs
            else:
                sv_pad_s = 70
                sv_pad_v = 70
                min_area = 200
                min_wh = 10
                close_kernel = 11

            lo = np.array([max(0, h_center - hsv_range.tolerance),
                           max(0, hsv_range.s - sv_pad_s),
                           max(0, hsv_range.v - sv_pad_v)])
            hi = np.array([min(179, h_center + hsv_range.tolerance),
                           min(255, hsv_range.s + sv_pad_s),
                           min(255, hsv_range.v + sv_pad_v)])
            mask = cv2.inRange(hsv, lo, hi)
            # Morpho open (nettoie bruit isolé) puis close (fusionne fragments proches)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            mask = cv2.morphologyEx(
                mask, cv2.MORPH_CLOSE, np.ones((close_kernel, close_kernel), np.uint8)
            )
            # Dilation supplémentaire pour regrouper les blobs voisins (< 15 px de séparation)
            if is_learned:
                mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=2)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                area = cv2.contourArea(c)
                if area < min_area:
                    continue
                x, y, w, h = cv2.boundingRect(c)
                if w < min_wh or h < min_wh:
                    continue
                # Filtre zones UI : centre du contour doit être dans la zone jouable
                if not self._is_in_playable_area(x + w // 2, y + h // 2, hsv.shape):
                    continue
                obj = DetectedObject(
                    label=res.nom_fr,
                    box=Region(x=x, y=y, w=w, h=h),
                    confidence=0.8 if is_learned else 0.5,
                )
                results.append((res, obj))

        return results

    def _is_in_playable_area(self, x: int, y: int, frame_shape: tuple) -> bool:
        """Vérifie qu'un point (x,y) frame est dans la zone jouable (hors UI)."""
        h, w = frame_shape[:2]
        cfg = self._config
        if y < cfg.ui_margin_top:
            return False
        if y > h - cfg.ui_margin_bottom:
            return False
        if x < cfg.ui_margin_left:
            return False
        if x > w - cfg.ui_margin_right:
            return False
        return True

    @staticmethod
    def _has_learned_detection(candidates: list[tuple[ResourceEntry, DetectedObject]]) -> bool:
        """True si au moins une détection vient d'une HSV apprise (confidence >= 0.7)."""
        return any(obj.confidence >= 0.7 for _, obj in candidates)

    def _character_frame_pos(self, frame_shape: tuple) -> tuple[int, int]:
        """Position approximative du personnage dans la frame.

        Le perso est centré sur la zone jouable (pas sur l'image entière) : l'UI du
        bas mange ~180 px, donc le vrai centre Y est plus haut que h/2.
        """
        h, w = frame_shape[:2]
        cfg = self._config
        cx = (cfg.ui_margin_left + (w - cfg.ui_margin_right)) // 2
        cy = (cfg.ui_margin_top + (h - cfg.ui_margin_bottom)) // 2
        return cx, cy

    # ---------- Changement de map ----------

    def _change_map(self, frame_shape: tuple) -> None:
        """Map vide → passe à la map suivante selon la stratégie configurée.

        Priorité :
          1. `circuit_maps` non vide → clics de bords vers la prochaine coord
          2. `zaap_rotation` non vide → TP via zaap
          3. Fallback : clic sur un bord de map en rotation cardinale
        """
        self.state_changed.emit("moving")

        if self._config.circuit_maps:
            self._circuit_next_map()
        elif self._config.zaap_rotation:
            self._zaap_rotate()
        else:
            self._edge_click_change_map(frame_shape)

    def _circuit_next_map(self) -> None:
        """Navigue vers la map suivante dans `config.circuit_maps` via clics de bords.

        Stratégie anti-OCR flaky :
          - Si `_last_known_pos` connu → on fait confiance (pas d'OCR initial)
          - Sinon → OCR une fois, puis tombe sur une valeur fallback si raté
        """
        circuit = self._config.circuit_maps

        # Essaie d'OCR pour recaler si le coord réel est dans le circuit
        locator = MapLocator(self._vision, log_callback=self.log_event.emit)
        current_info = locator.locate()
        if current_info is not None and current_info.is_valid:
            real_coords = current_info.coords
            self._last_known_pos = real_coords  # update
            try:
                idx_of_current = circuit.index(real_coords)
                self._circuit_index = (idx_of_current + 1) % len(circuit)
            except ValueError:
                pass  # pas dans le circuit → on garde l'index

        target = circuit[self._circuit_index % len(circuit)]

        # Si on est déjà à target (cas après TP ou recalage), skip nav
        if self._last_known_pos == target:
            self._circuit_index = (self._circuit_index + 1) % len(circuit)
            target = circuit[self._circuit_index % len(circuit)]

        self.log_event.emit(
            f"🧭 Circuit : {self._last_known_pos} → {target} ({self._circuit_index + 1}/{len(circuit)})",
            "info",
        )

        try:
            nav = self._get_navigator()
            # Passe le start_pos connu pour éviter de redépendre de l'OCR initial
            result = nav.go_to(target, start_pos=self._last_known_pos)
            if result.success:
                self._stats.map_changes += 1
                self._last_known_pos = target  # la nav a confirmé l'arrivée
                self.log_event.emit(
                    f"✓ Arrivé en {target} ({result.hops} hops)", "info",
                )
                self._circuit_index = (self._circuit_index + 1) % len(circuit)
                self.msleep(1200)
            else:
                self.log_event.emit(
                    f"✗ Navigation échouée ({result.outcome.value}) : {result.message}",
                    "error",
                )
                self._stats.errors += 1
                # On reste sur la même cible au prochain tour — ne pas avancer l'index
                # (peut-être qu'on était bloqué par un monstre ou un obstacle)
        except Exception as exc:
            logger.exception("Erreur circuit navigation")
            self.log_event.emit(f"⚠️ Erreur nav : {exc}", "error")
            self._stats.errors += 1

    def _get_navigator(self) -> MapNavigator:
        """Lazy-init du MapNavigator (avec ratios user-calibrés si disponibles)."""
        if self._navigator is None:
            from src.services.map_navigator import EdgeRatios  # noqa: PLC0415
            from src.services.user_prefs import get_user_prefs  # noqa: PLC0415

            locator = MapLocator(self._vision, log_callback=self.log_event.emit)
            # Charge les ratios calibrés par l'user si présents
            edge_ratios = None
            try:
                gp = get_user_prefs().global_prefs
                if gp.edge_ratios:
                    edge_ratios = EdgeRatios.from_dict(gp.edge_ratios)
                    self.log_event.emit("🧭 Utilise ratios de bord calibrés par user", "info")
            except Exception:
                pass

            self._navigator = MapNavigator(
                vision=self._vision,
                input_svc=self._input,
                map_locator=locator,
                window_title=self._config.dofus_window_title,
                edge_ratios=edge_ratios,
                log_callback=self.log_event.emit,
            )
        return self._navigator

    def _zaap_rotate(self) -> None:
        """Téléporte vers le zaap suivant dans la rotation."""
        rotation = self._config.zaap_rotation
        query = rotation[self._zaap_index % len(rotation)]
        self._zaap_index += 1

        self.log_event.emit(
            f"🌀 Téléportation vers '{query}' (rotation {self._zaap_index}/{len(rotation)})",
            "info",
        )

        try:
            zaap = self._get_zaap_service()
            result = zaap.teleport_to(query)
            if result.success:
                self._stats.map_changes += 1
                self.log_event.emit(f"✓ Arrivé à {result.after_map}", "info")
                # Met à jour la position connue (le ZaapService a OCR-confirmé l'arrivée)
                if result.after_map is not None and result.after_map.is_valid:
                    self._last_known_pos = result.after_map.coords
                # Petite pause pour que la map se stabilise avant le prochain scan
                self.msleep(1500)
            else:
                self.log_event.emit(
                    f"✗ TP échoué ({result.outcome.value}) : {result.message}",
                    "error",
                )
                self._stats.errors += 1
                # Fallback : essaie le clic sur bord de map
                frame = self._vision.capture()
                self._edge_click_change_map(frame.shape)
        except Exception as exc:
            logger.exception("Erreur pendant zaap rotation")
            self.log_event.emit(f"⚠️ Erreur TP : {exc}", "error")
            self._stats.errors += 1

    def _get_zaap_service(self) -> ZaapService:
        """Lazy-init du ZaapService."""
        if self._zaap_svc is None:
            chat = ChatService(self._input)
            locator = MapLocator(self._vision)
            self._zaap_svc = ZaapService(
                vision=self._vision,
                input_svc=self._input,
                chat_svc=chat,
                map_locator=locator,
                window_title=self._config.dofus_window_title,
            )
        return self._zaap_svc

    def _edge_click_change_map(self, frame_shape: tuple) -> None:
        """Fallback : clique sur un bord de map dans la direction courante.

        Rotation : haut → droite → bas → gauche → haut …
        """
        h, w = frame_shape[:2]
        directions = [
            ("haut",    w // 2, 20),
            ("droite",  w - 20, h // 2),
            ("bas",     w // 2, h - 40),
            ("gauche",  20,     h // 2),
        ]
        name, fx, fy = directions[self._direction_index % len(directions)]
        self._direction_index += 1
        fx += random.randint(-30, 30)
        fy += random.randint(-20, 20)
        cx, cy = self._frame_to_screen(fx, fy)
        self._ensure_dofus_focused()
        self.log_event.emit(
            f"🗺 Aucune ressource — changement de map bord : {name} écran=({cx},{cy})",
            "warn",
        )
        try:
            self._input.click(cx, cy, button="left")
            self._stats.map_changes += 1
            self.msleep(3500)
        except Exception as exc:
            self.log_event.emit(f"⚠️ Échec clic map : {exc}", "error")
            self._stats.errors += 1
