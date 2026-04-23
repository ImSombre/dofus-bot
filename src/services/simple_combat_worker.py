"""Worker combat SIMPLE et FIABLE — stratégie bulldozer.

Pas de LLM. Pas de règles complexes. Logique minimale qui MARCHE :

Chaque tour :
  1. Scan HSV → positions perso + mobs
  2. Pick le mob le plus proche
  3. Pour chaque slot configuré (dans l'ordre de priorité) :
       - Si sort en portée ET assez de PA → cast sur le mob
       - Sinon skip
  4. Si pas de cast possible et distance > portée max → click vers mob (approche)
  5. Si plus de PA pour aucun sort → end_turn

L'ordre des slots est important : les plus gros sorts d'abord (slot 1 = prioritaire).
Le user met ses meilleurs sorts sur les petits slots.

Avantage vs version LLM :
  - Pas d'hallucination : on cast ce qui est à portée, point.
  - Ultra rapide : décision en <10ms
  - Gratuit : zéro appel API
  - Prévisible : tu sais exactement ce que le bot va faire

Compromis :
  - Pas de contournement intelligent de murs (mais détection LoS marche)
  - Pas de priorisation "finish-kill" sophistiquée (prend juste le mob le + proche)
  - Pas de gestion des combos multi-sorts
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.combat_knowledge import CombatKnowledge
from src.services.combat_state_reader import CombatStateReader
from src.services.input_service import InputService
from src.services.vision import MssVisionService


CELL_PX_X = 86
CELL_PX_Y = 43


@dataclass
class SimpleCombatConfig:
    class_name: str = "ecaflip"
    spell_shortcuts: dict[int, str] = field(default_factory=dict)
    starting_pa: int = 6
    starting_pm: int = 3
    po_bonus: int = 0
    dofus_window_title: str | None = None
    scan_interval_sec: float = 0.5
    post_action_delay_sec: float = 0.7
    key_to_click_delay_sec: float = 0.15
    max_actions_per_turn: int = 10


@dataclass
class SimpleCombatStats:
    turns_played: int = 0
    spells_cast: int = 0
    mobs_seen: int = 0


class SimpleCombatWorker(QThread):
    """Worker combat rule-based pur. Pas de LLM."""

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    # AZERTY mapping slot → touche physique (identique à vision_combat_worker)
    _AZERTY_SLOT_KEYS = {
        "1": "&", "2": "é", "3": '"', "4": "'", "5": "(",
        "6": "-", "7": "è", "8": "_", "9": "ç", "0": "à",
    }

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: SimpleCombatConfig,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._knowledge = CombatKnowledge()
        self._state_reader = CombatStateReader(vision)
        self._stats = SimpleCombatStats()
        self._stop_requested = False
        self._pa_remaining = config.starting_pa
        self._actions_this_turn = 0
        self._last_cast_signature = None  # (slot, x, y) pour anti-boucle
        self._last_cast_repeats = 0
        # Cache infos sorts
        self._spell_info: dict[int, dict] = {}

    def request_stop(self) -> None:
        self._stop_requested = True

    # ---------- Knowledge helpers ----------

    def _get_spell_info(self, slot: int) -> dict:
        if slot in self._spell_info:
            return self._spell_info[slot]
        info = {}
        try:
            spell_ref = str(self._config.spell_shortcuts.get(slot, "")).strip().lower()
            if spell_ref:
                cls = self._knowledge.get_class(self._config.class_name)
                if cls:
                    for s in cls.sorts:
                        if (str(s.get("nom", "")).lower() == spell_ref
                                or str(s.get("id", "")).lower() == spell_ref):
                            info = {
                                "nom": s.get("nom", ""),
                                "pa": int(s.get("pa", 3)),
                                "po_min": int(s.get("po_min", 1)),
                                "po_max": int(s.get("po_max", 5)),
                                "role": str(s.get("role", "offensif")).lower(),
                                "portee_modifiable": bool(s.get("portee_modifiable", True)),
                            }
                            break
        except Exception as exc:
            logger.debug("get_spell_info échec : {}", exc)
        self._spell_info[slot] = info
        return info

    def _effective_max_range(self, info: dict) -> int:
        base = info.get("po_max", 5)
        if info.get("portee_modifiable", True):
            return base + max(0, self._config.po_bonus)
        return base

    # ---------- Distance iso Dofus ----------

    @staticmethod
    def _dist_cases(a_xy: tuple[int, int], b_xy: tuple[int, int]) -> float:
        dx = abs(a_xy[0] - b_xy[0])
        dy = abs(a_xy[1] - b_xy[1])
        return max(dx / CELL_PX_X, dy / CELL_PX_Y)

    # ---------- Main loop ----------

    def run(self) -> None:
        self.log_event.emit(
            f"⚡ SimpleCombatWorker démarré (classe={self._config.class_name}, "
            f"slots={len(self._config.spell_shortcuts)})",
            "info",
        )
        self.state_changed.emit("running")

        while not self._stop_requested:
            try:
                self._tick()
            except Exception as exc:
                logger.exception("SimpleCombat tick erreur")
                self.log_event.emit(f"⚠ Erreur : {exc}", "error")
            self.stats_updated.emit(self._stats)
            if not self._stop_requested:
                self.msleep(int(self._config.scan_interval_sec * 1000))

        self.log_event.emit("⏹ SimpleCombatWorker arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    def _tick(self) -> None:
        # 1. Scan HSV
        snap = self._state_reader.read()

        # 2. Pas de mobs → on fait rien (probablement hors combat ou tour ennemi)
        if not snap.ennemis:
            self.msleep(500)
            return

        # 3. Pas de perso détecté → on fait rien (attend que HSV voie le perso)
        if not snap.perso:
            self.msleep(500)
            return

        # Log les détections pour debug
        mobs_summary = ", ".join(
            f"MOB{i}=({e.x},{e.y})" for i, e in enumerate(snap.ennemis[:5], 1)
        )
        self.log_event.emit(f"🔍 HSV : PERSO=({snap.perso.x},{snap.perso.y}) {mobs_summary}", "info")
        self._stats.mobs_seen = len(snap.ennemis)

        # 4. Anti-spam : force end_turn si trop d'actions
        if self._actions_this_turn >= self._config.max_actions_per_turn:
            self.log_event.emit(
                f"🚫 Limite {self._config.max_actions_per_turn} actions/tour → end_turn",
                "warn",
            )
            self._end_turn()
            return

        # 5. Pick mob le plus proche
        perso_xy = (snap.perso.x, snap.perso.y)
        target = min(
            snap.ennemis,
            key=lambda e: (e.x - perso_xy[0]) ** 2 + (e.y - perso_xy[1]) ** 2,
        )
        target_xy = (target.x, target.y)
        dist = self._dist_cases(perso_xy, target_xy)

        # 6. Pour chaque slot, check si cast possible (ordre = slot 1 d'abord)
        for slot in sorted(self._config.spell_shortcuts.keys()):
            info = self._get_spell_info(slot)
            if not info:
                continue  # sort inconnu, skip
            if info.get("role", "offensif") not in ("offensif", "degats", ""):
                continue  # on ne cast que les sorts offensifs en v1
            cost = info.get("pa", 99)
            if cost > self._pa_remaining:
                continue
            max_r = self._effective_max_range(info)
            min_r = info.get("po_min", 1)
            if not (min_r <= dist <= max_r):
                continue

            # Anti-boucle : si on a déjà cast ce slot sur cette cible au tick
            # précédent et que le mob est toujours là, essaie un autre slot.
            sig = (slot, target_xy[0], target_xy[1])
            if self._last_cast_signature == sig:
                self._last_cast_repeats += 1
                if self._last_cast_repeats >= 2:
                    self.log_event.emit(
                        f"⚠ Cast slot{slot} répété sans effet → on essaie un autre sort",
                        "warn",
                    )
                    continue  # essaie le slot suivant
            else:
                self._last_cast_signature = sig
                self._last_cast_repeats = 0

            # Cast !
            self._do_cast(slot, target_xy)
            self._pa_remaining -= cost
            self._actions_this_turn += 1
            self._stats.spells_cast += 1
            return  # une action par tick, on laisse le jeu respirer

        # 7. Aucun sort utilisable à cette distance → bouge vers le mob
        # (ou end_turn si on a déjà beaucoup bougé)
        if self._actions_this_turn >= 3:
            self.log_event.emit(
                f"🏁 Plus de sort utile + déjà {self._actions_this_turn} actions → end_turn",
                "info",
            )
            self._end_turn()
            return

        # Approche d'1-2 cases vers le mob
        dx = target_xy[0] - perso_xy[0]
        dy = target_xy[1] - perso_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        step_px = 1.5 * CELL_PX_X
        approach_xy = (
            int(perso_xy[0] + (dx / length) * step_px),
            int(perso_xy[1] + (dy / length) * step_px),
        )
        self.log_event.emit(
            f"🚶 Pas de sort à dist {dist:.0f}c → approche vers ({approach_xy[0]},{approach_xy[1]})",
            "info",
        )
        self._click(approach_xy[0], approach_xy[1])
        self._actions_this_turn += 1
        self.msleep(int(self._config.post_action_delay_sec * 1000))

    def _end_turn(self) -> None:
        self._ensure_dofus_focus()
        self.msleep(80)
        try:
            self._input.press_key("f1")
        except Exception as exc:
            logger.debug("press f1 échec : {}", exc)
        self.log_event.emit("→ Fin de tour (F1)", "info")
        self._stats.turns_played += 1
        self._pa_remaining = self._config.starting_pa
        self._actions_this_turn = 0
        self._last_cast_signature = None
        self._last_cast_repeats = 0
        self.msleep(int(self._config.post_action_delay_sec * 1000))

    def _do_cast(self, slot: int, target_xy: tuple[int, int]) -> None:
        info = self._get_spell_info(slot)
        azerty = self._AZERTY_SLOT_KEYS.get(str(slot), str(slot))
        self.log_event.emit(
            f"→ Cast slot {slot} '{info.get('nom', '?')}' (touche '{azerty}') "
            f"sur ({target_xy[0]},{target_xy[1]})",
            "info",
        )
        self._ensure_dofus_focus()
        self.msleep(80)
        try:
            # Presse la touche du slot via pydirectinput (scan code AZERTY)
            self._send_spell_key(str(slot))
            self.msleep(int(self._config.key_to_click_delay_sec * 1000))
            self._click(target_xy[0], target_xy[1])
        except Exception as exc:
            self.log_event.emit(f"⚠ Cast échec : {exc}", "error")
        self.msleep(int(self._config.post_action_delay_sec * 1000))

    def _send_spell_key(self, slot: str) -> None:
        """Envoie la touche slot via pydirectinput (Dofus-friendly)."""
        try:
            import pydirectinput  # noqa: PLC0415
            pydirectinput.press(slot)
        except Exception:
            # Fallback : input_service classique
            self._input.press_key(slot)

    def _click(self, x: int, y: int) -> None:
        self._input.click(int(x), int(y), button="left")

    def _ensure_dofus_focus(self) -> None:
        """Force la fenêtre Dofus au premier plan."""
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
        title = (self._config.dofus_window_title or "dofus").lower()
        try:
            user32 = ctypes.windll.user32
            hwnd = None
            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def cb(h, _):
                nonlocal hwnd
                if not user32.IsWindowVisible(h):
                    return True
                n = user32.GetWindowTextLengthW(h)
                if n == 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, buf, n + 1)
                if title in buf.value.lower():
                    hwnd = h
                    return False
                return True
            user32.EnumWindows(cb, 0)
            if hwnd:
                current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
                target_tid = user32.GetWindowThreadProcessId(hwnd, None)
                user32.AttachThreadInput(current_tid, target_tid, True)
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(current_tid, target_tid, False)
        except Exception:
            pass
