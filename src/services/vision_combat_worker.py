"""Worker combat piloté par un LLM multimodal (Llama 3.2 Vision / MiniCPM-V).

Flow "vraie IA" :
  1. Capture écran Dofus
  2. Envoie image + prompt détaillé au LLM vision
  3. LLM comprend la scène (phase, ennemis, PA, sort disponible...) et décide
     UNE action avec coordonnées de clic (x, y) précises
  4. Le worker exécute (touche clavier + clic aux coords fournies par l'IA)
  5. Attend animation
  6. Boucle

Contrairement au CombatRunnerWorker "heuristique", le LLM :
  - Reconnaît la phase (placement / combat / popup / hors combat)
  - Voit les mobs directement, sans HSV
  - Connaît les sorts de la classe
  - Gère les popups, menus, situations inattendues

Modèles recommandés (via Ollama) :
  - llama3.2-vision:11b (7.9 GB, excellent raisonnement, plus lent ~8s/tour)
  - minicpm-v:8b (5 GB, rapide ~4s/tour, bon sur UI)
  - llava:7b (4.7 GB, le plus rapide, moins précis)
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
from src.services.llm_client import LLMClient
from src.services.vision import MssVisionService


@dataclass
class VisionCombatConfig:
    class_name: str = "ecaflip"
    spell_shortcuts: dict[int, str] = field(default_factory=dict)
    # Provider LLM : "ollama", "lmstudio", "gemini"
    llm_provider: str = "gemini"         # default : Gemini (cloud gratuit, pas de VRAM)
    llm_model: str = "gemini-flash-latest"  # alias toujours pointé vers la version stable
    llm_url: str = ""                    # override optionnel (défaut : selon provider)
    llm_api_key: str = ""                # clé API Gemini (obligatoire pour provider=gemini)
    # Timings optimisés pour vitesse (v0.1.8) :
    #   - scan 0.3s entre cycles (avant 1.5s = 80% de temps perdu)
    #   - post_action 1.0s (avant 2.0s ; animation sort < 1s en général)
    #   - key_to_click 0.25s (avant 0.45s)
    scan_interval_sec: float = 0.3
    post_action_delay_sec: float = 1.0
    key_to_click_delay_sec: float = 0.25
    starting_pa: int = 6
    starting_pm: int = 3
    max_actions_per_turn: int = 6
    request_timeout_sec: float = 90.0  # Gemini peut être lent + retry interne sur 503
    dofus_window_title: str | None = None
    # Sauvegarder chaque capture envoyée au LLM (debug)
    save_debug_images: bool = False


@dataclass
class VisionCombatStats:
    turns_played: int = 0
    actions_taken: int = 0
    llm_calls: int = 0
    llm_errors: int = 0


_PROMPT_MASTER_CACHE: str | None = None


def _load_master_prompt() -> str:
    """Charge le prompt master depuis data/knowledge/system_prompt_dofus.md."""
    global _PROMPT_MASTER_CACHE
    if _PROMPT_MASTER_CACHE is not None:
        return _PROMPT_MASTER_CACHE
    try:
        here = Path(__file__).resolve().parent.parent.parent
        path = here / "data" / "knowledge" / "system_prompt_dofus.md"
        if path.exists():
            _PROMPT_MASTER_CACHE = path.read_text(encoding="utf-8")
        else:
            _PROMPT_MASTER_CACHE = ""
    except Exception:
        _PROMPT_MASTER_CACHE = ""
    return _PROMPT_MASTER_CACHE or ""


def _build_class_section(knowledge: CombatKnowledge, class_name: str) -> tuple[str, str]:
    cls = knowledge.get_class(class_name)
    if cls is None:
        return (class_name, "Aucune info sur cette classe.")
    header = f"{cls.nom_fr} ({cls.class_id}) — {cls.archetype}"
    sorts_lines = [f"Sorts disponibles de {cls.nom_fr} :"]
    for s in cls.sorts:
        sorts_lines.append(
            f"  - {s.get('nom','?')} : {s.get('pa','?')} PA, "
            f"portee {s.get('po_min','?')}-{s.get('po_max','?')}, "
            f"{s.get('type','?')}. {s.get('note','')}"
        )
    return (header, "\n".join(sorts_lines))


class VisionCombatWorker(QThread):
    """Worker combat piloté par un LLM vision (vraie IA multimodale)."""

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: VisionCombatConfig,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._knowledge = CombatKnowledge()
        self._state_reader = CombatStateReader(vision)
        self._llm = LLMClient(
            provider=config.llm_provider,
            model=config.llm_model,
            base_url=config.llm_url or None,
            api_key=config.llm_api_key or None,
            temperature=0.2,
            max_tokens=2000,   # élevé pour permettre observation+raisonnement+JSON complet
            timeout_sec=config.request_timeout_sec,
        )
        self._stats = VisionCombatStats()
        self._stop_requested = False
        self._system_prompt: str = ""
        self._debug_dir = Path("data/vision_debug")
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5
        self._latencies: list[float] = []
        # Anti-boucle : détecte si le LLM répète la même action sans changement
        self._last_action_key: str = ""
        self._same_action_count: int = 0
        self._last_phase: str = ""

    def request_stop(self) -> None:
        self._stop_requested = True

    def average_latency_sec(self) -> float:
        """Latence moyenne du LLM sur les 20 dernières requêtes."""
        if not self._latencies:
            return 0.0
        return sum(self._latencies) / len(self._latencies)

    def run(self) -> None:
        self.log_event.emit(
            f"🧠 VisionCombatWorker démarré "
            f"(provider={self._config.llm_provider}, modèle={self._config.llm_model})",
            "info",
        )
        self.state_changed.emit("scanning")

        if not self._llm.is_available():
            self.log_event.emit(
                f"✗ {self._config.llm_provider} indisponible à {self._llm.base_url}. "
                f"Lance-le et réessaie.",
                "error",
            )
            self.stopped.emit()
            return

        if not self._llm.has_model():
            models = ", ".join(self._llm.list_models()[:5]) or "aucun"
            self.log_event.emit(
                f"⚠ Modèle '{self._config.llm_model}' pas trouvé. "
                f"Modèles dispo : {models}. On tente quand même.",
                "warn",
            )

        # Prépare le system prompt (charge depuis le fichier master)
        class_header, sorts_desc = _build_class_section(
            self._knowledge, self._config.class_name,
        )
        try:
            frame = self._vision.capture()
            h, w = frame.shape[:2]
        except Exception:
            w, h = 1920, 1080

        master_tpl = _load_master_prompt()
        if not master_tpl:
            self.log_event.emit(
                "⚠ system_prompt_dofus.md manquant — prompt minimal utilisé",
                "warn",
            )
            master_tpl = (
                "Tu es un joueur expert de Dofus 2.64. Réponds en JSON avec"
                " observation/phase/raisonnement/action."
            )

        # Remplace les placeholders {width}, {height}, {class_info}, {sorts_description}
        self._system_prompt = (
            master_tpl
            .replace("{width}", str(w))
            .replace("{height}", str(h))
            .replace("{class_info}", class_header)
            .replace("{sorts_description}", sorts_desc)
        )

        self.log_event.emit(
            f"✓ Prêt : classe={self._config.class_name}, "
            f"sorts={len(self._config.spell_shortcuts)} raccourcis",
            "info",
        )

        if self._config.save_debug_images:
            self._debug_dir.mkdir(parents=True, exist_ok=True)

        # Boucle principale : une décision = une action
        while not self._stop_requested:
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Erreur VisionCombat tick")
                self.log_event.emit(f"⚠ Erreur : {exc}", "error")

            self.stats_updated.emit(self._stats)
            if not self._stop_requested:
                self.msleep(int(self._config.scan_interval_sec * 1000))

        self.log_event.emit("⏹ VisionCombatWorker arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    @staticmethod
    def _scale_action_coords(action: dict, img_scale: float) -> dict:
        """Convertit les coords LLM (dans l'espace de l'image envoyée) en coords écran.

        `img_scale` = ratio de redimensionnement (ex: 0.5 si image 1280 / écran 2560).
        Pour retrouver pixels écran : coord_écran = coord_llm / img_scale.

        Retourne une copie du dict avec `target_xy` mise à l'échelle.
        """
        if img_scale == 1.0 or img_scale == 0:
            return action
        out = dict(action)
        xy = out.get("target_xy") or out.get("xy")
        if xy and len(xy) == 2:
            try:
                x = int(round(xy[0] / img_scale))
                y = int(round(xy[1] / img_scale))
                if "target_xy" in out:
                    out["target_xy"] = [x, y]
                elif "xy" in out:
                    out["xy"] = [x, y]
            except (TypeError, ValueError):
                pass
        return out

    def _ensure_dofus_focus(self) -> None:
        """Force la fenêtre Dofus au premier plan via Win32 (contournement anti-stealing).

        Windows bloque `SetForegroundWindow` depuis un process qui n'a pas l'input focus
        (anti-vol de focus). Contournement : `AttachThreadInput` + `SetForegroundWindow`.
        """
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        title_substring = (self._config.dofus_window_title or "dofus").lower()

        # 1. Trouve le HWND Dofus
        user32 = ctypes.windll.user32
        hwnd_dofus = None

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum_cb(hwnd, _lparam):
            nonlocal hwnd_dofus
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            if title_substring in buff.value.lower():
                hwnd_dofus = hwnd
                return False   # stop enum
            return True

        user32.EnumWindows(_enum_cb, 0)
        if not hwnd_dofus:
            return

        # 2. Force foreground via AttachThreadInput (bypass l'anti-vol de focus)
        try:
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            target_thread = user32.GetWindowThreadProcessId(hwnd_dofus, None)
            if current_thread != target_thread:
                user32.AttachThreadInput(current_thread, target_thread, True)
            # Si minimise, restore
            if user32.IsIconic(hwnd_dofus):
                SW_RESTORE = 9
                user32.ShowWindow(hwnd_dofus, SW_RESTORE)
            user32.BringWindowToTop(hwnd_dofus)
            user32.SetForegroundWindow(hwnd_dofus)
            user32.SetFocus(hwnd_dofus)
            if current_thread != target_thread:
                user32.AttachThreadInput(current_thread, target_thread, False)
        except Exception as exc:
            logger.debug("Focus Dofus Win32 échec : {}", exc)
            # Fallback pygetwindow
            try:
                import pygetwindow as gw  # noqa: PLC0415
                for w in gw.getAllWindows():
                    if w.title and title_substring in w.title.lower():
                        try:
                            w.activate()
                        except Exception:
                            w.minimize()
                            w.restore()
                        return
            except Exception:
                pass

    def _annotate_frame_with_detections(self, frame):
        """Dessine des boîtes + labels "MOB @ (x, y)" sur les cercles bleus détectés.

        Le LLM n'a plus à deviner : il lit les coords affichées directement.
        """
        try:
            import cv2  # noqa: PLC0415
            snap = self._state_reader.read()
            out = frame.copy()
            # Perso en rouge
            if snap.perso is not None:
                p = snap.perso
                cv2.rectangle(out, (p.x - 50, p.y - 50), (p.x + 50, p.y + 50),
                              (0, 0, 255), 3)
                cv2.putText(out, f"PERSO ({p.x},{p.y})",
                            (p.x - 70, p.y - 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            # Ennemis en bleu avec coords explicites
            for i, e in enumerate(snap.ennemis, 1):
                cv2.rectangle(out, (e.x - 60, e.y - 60), (e.x + 60, e.y + 60),
                              (255, 100, 0), 4)
                label = f"MOB{i} ({e.x},{e.y})"
                cv2.putText(out, label,
                            (e.x - 80, e.y - 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 100, 0), 2)
            return out, snap
        except Exception as exc:
            logger.debug("Annotation frame échec : {}", exc)
            return frame, None

    def _tick(self) -> None:
        """Un cycle : capture → annotation HSV → LLM → action."""
        try:
            raw_frame = self._vision.capture()
        except Exception as exc:
            self.log_event.emit(f"Capture échec : {exc}", "error")
            return

        # Annote la frame avec les positions détectées (perso + mobs) AVANT envoi LLM.
        # Le LLM lit les coords directement au lieu de deviner → ciblage précis.
        annotated_frame, snap = self._annotate_frame_with_detections(raw_frame)
        if snap is not None and snap.ennemis:
            detections_summary = ", ".join(
                f"MOB{i}=({e.x},{e.y})" for i, e in enumerate(snap.ennemis, 1)
            )
            self.log_event.emit(f"🔍 Détections HSV : {detections_summary}", "info")

        user_prompt = self._build_user_prompt(snap)
        self.log_event.emit("👁 → LLM (analyse image)...", "info")

        t0 = time.time()
        decision = self._llm.ask_json(
            user_prompt,
            system=self._system_prompt,
            image_bgr=annotated_frame,
            fallback={},
        )
        elapsed = time.time() - t0
        self._stats.llm_calls += 1

        # Check stop juste après l'appel LLM (peut être long, l'utilisateur a pu demander stop entre temps)
        if self._stop_requested:
            return

        # Suivi latence (rolling window 20 dernières)
        self._latencies.append(elapsed)
        if len(self._latencies) > 20:
            self._latencies.pop(0)

        if not decision:
            self._stats.llm_errors += 1
            self._consecutive_errors += 1
            self.log_event.emit(
                f"⚠ LLM erreur {self._consecutive_errors}/"
                f"{self._max_consecutive_errors} ({elapsed:.1f}s)",
                "warn",
            )
            if self._consecutive_errors >= self._max_consecutive_errors:
                self.log_event.emit(
                    "✗ Trop d'erreurs LLM consécutives — arrêt automatique. "
                    "Vérifie que Ollama/LM Studio tourne.",
                    "error",
                )
                self._stop_requested = True
                return
            self._execute_action({"type": "wait"}, "erreur_llm")
            return

        # Reset compteur d'erreurs après succès
        self._consecutive_errors = 0

        phase = decision.get("phase", "?")
        observation = decision.get("observation") or decision.get("situation", "")
        reasoning = decision.get("raisonnement") or decision.get("reasoning", "")
        action = decision.get("action", {}) or {}

        # Scale factor de l'image envoyée au LLM.
        # Si le LLM a vu une image 1280×720 mais l'écran fait 2560×1440, scale=0.5.
        # On doit diviser les coords LLM par ce scale pour retrouver les pixels écran.
        img_scale = float(decision.get("_image_scale", 1.0)) or 1.0
        if action and img_scale != 1.0:
            scaled = self._scale_action_coords(action, img_scale)
            if scaled != action:
                self.log_event.emit(
                    f"   📐 Scale {img_scale:.2f} : coords LLM {action.get('target_xy')} "
                    f"→ écran {scaled.get('target_xy')}",
                    "info",
                )
                action = scaled

        self.log_event.emit(
            f"🧠 [{elapsed:.1f}s] phase={phase} | 👁 {observation[:80]}",
            "info",
        )
        if reasoning:
            self.log_event.emit(f"   💭 {reasoning[:120]}", "info")

        self._execute_action(action, phase)

        if self._config.save_debug_images:
            self._save_debug(frame, decision)

    def _build_user_prompt(self, snap=None) -> str:
        shortcuts = ", ".join(
            f"touche {k}={name}"
            for k, name in sorted(self._config.spell_shortcuts.items())
        )
        # Précalcul du scale qui sera appliqué à l'image envoyée au LLM.
        # Le LLM verra une image redimensionnée à max 2048px → on lui donne
        # les coords HSV dans CET espace image (pour qu'il copie sans calcul).
        img_scale = 1.0
        try:
            frame = self._vision.capture()
            h, w = frame.shape[:2]
            if max(h, w) > 2048:
                img_scale = 2048 / max(h, w)
        except Exception:
            pass

        detections_block = ""
        if snap is not None:
            lines = []
            if snap.perso:
                px = int(snap.perso.x * img_scale)
                py = int(snap.perso.y * img_scale)
                lines.append(f"  • PERSO (toi, {self._config.class_name}) : ({px}, {py})")
            for i, e in enumerate(snap.ennemis, 1):
                ex = int(e.x * img_scale)
                ey = int(e.y * img_scale)
                lines.append(f"  • MOB{i} (ennemi à CIBLER) : ({ex}, {ey})")
            if lines:
                detections_block = (
                    "\n\n⭐ COORDONNÉES DANS L'IMAGE QUE TU RECOIS "
                    "(utilise DIRECTEMENT ces valeurs, pas de devinette) :\n"
                    + "\n".join(lines)
                    + "\n\n**Pour cibler un MOB avec cast_spell, recopie EXACTEMENT ses coords dans target_xy.** "
                    + "Ex: pour cibler MOB1, fais `target_xy: [x1, y1]` avec les valeurs exactes ci-dessus."
                )
        return (
            f"Analyse la capture d'écran fournie. Je joue un **{self._config.class_name}**.\n"
            f"Mes raccourcis clavier sont : {shortcuts or '(aucun configuré)'}.\n"
            f"J'ai au maximum **{self._config.starting_pa} PA** et **{self._config.starting_pm} PM** par tour.\n"
            f"{detections_block}\n\n"
            f"Observe l'image (phase, mon perso entouré d'un rectangle rouge, "
            f"mobs entourés de rectangles bleus avec label 'MOB{{n}} (x,y)', UI, popups) "
            f"puis décide UNE action à exécuter MAINTENANT.\n\n"
            f"Réponds UNIQUEMENT en JSON valide avec les champs "
            f"observation/phase/raisonnement/action. Aucun texte avant ou après."
        )

    def _execute_action(self, action: dict, phase: str) -> None:
        atype = str(action.get("type", "")).lower()
        self.state_changed.emit("playing")

        # Active la fenêtre Dofus avant TOUTE action (sinon keys/clics partent ailleurs)
        if atype in ("cast_spell", "spell", "click_xy", "press_key", "end_turn", "close_popup"):
            self._ensure_dofus_focus()
            # Court délai pour laisser Windows prendre le focus
            self.msleep(80)

        if atype in ("cast_spell", "spell"):
            key = action.get("spell_key") or action.get("key")
            xy = action.get("target_xy") or action.get("target")
            if not key or not xy:
                self.log_event.emit("⚠ cast_spell sans key/xy → skip", "warn")
                return
            self._do_cast(str(key), xy)

        elif atype == "click_xy":
            xy = action.get("target_xy") or action.get("xy")
            if xy and len(xy) == 2:
                self.log_event.emit(f"→ Clic ({xy[0]},{xy[1]})", "info")
                self._input.click(int(xy[0]), int(xy[1]), button="left")
                self._stats.actions_taken += 1

        elif atype == "press_key":
            key = str(action.get("key", "")).lower()
            if key:
                self.log_event.emit(f"→ Touche '{key}'", "info")
                self._input.press_key(key)
                self._stats.actions_taken += 1

        elif atype == "end_turn":
            # Presse F1 ou Espace = raccourci "Terminer le tour" (configurable Dofus)
            self.log_event.emit("→ Fin de tour (touche F1)", "info")
            try:
                self._input.press_key("f1")
            except Exception:
                pass
            self._stats.actions_taken += 1

        elif atype == "close_popup":
            self.log_event.emit("→ Ferme popup (Escape)", "info")
            self._input.press_key("escape")
            self._stats.actions_taken += 1

        elif atype == "wait":
            self.log_event.emit("→ Attente", "info")

        else:
            self.log_event.emit(f"⚠ Action inconnue : {atype}", "warn")
            return

        # Attend que l'animation se finisse avant le prochain tick
        self.msleep(int(self._config.post_action_delay_sec * 1000))

    # Mapping des slots numériques 1-9 vers les touches AZERTY (rangée du haut sans Shift).
    # Dofus utilise `&éçà…` comme raccourcis sur clavier AZERTY — presser "1"-"9" ne marche pas.
    _AZERTY_SLOT_KEYS = {
        "1": "&", "2": "é", "3": '"', "4": "'", "5": "(",
        "6": "-", "7": "è", "8": "_", "9": "ç", "0": "à",
    }

    def _send_spell_hotkey(self, slot: str) -> bool:
        """Presse la touche correspondant au slot 1-9.

        IMPORTANT : Dofus utilise DirectInput (jeu bas-niveau). Il n'attrape PAS
        les événements SendInput Unicode (typewrite) — il n'attrape QUE les scan codes
        hardware. Donc on utilise pydirectinput.press(chiffre) qui envoie le scan code
        physique de la touche chiffre 1-9, et Windows + clavier AZERTY produisent
        naturellement &éç… pour Dofus.

        Ordre :
          1. pydirectinput.press(slot) — scan code physique VK_1..VK_9 → DirectInput OK
          2. ctypes SendInput scan code direct (fallback ultra bas-niveau)
          3. typewrite AZERTY char — dernière chance (pas fiable pour Dofus)
        """
        # Tentative 1 : pydirectinput scan code (LE fix pour Dofus)
        try:
            import pydirectinput as _pdi  # noqa: PLC0415
            _pdi.press(str(slot))
            return True
        except Exception as exc:
            logger.debug("pydirectinput scan code échec ({}) : {}", slot, exc)

        # Tentative 2 : ctypes SendInput avec scan code bas-niveau
        try:
            if self._send_raw_scancode(str(slot)):
                return True
        except Exception as exc:
            logger.debug("ctypes SendInput échec : {}", exc)

        # Tentative 3 : typewrite Unicode (ne marche pas pour Dofus DirectInput, mais on essaye)
        azerty = self._AZERTY_SLOT_KEYS.get(str(slot), str(slot))
        try:
            import pyautogui as _pg  # noqa: PLC0415
            _pg.typewrite(azerty, interval=0)
            return True
        except Exception as exc:
            logger.debug("typewrite AZERTY échec ({}) : {}", azerty, exc)

        return False

    @staticmethod
    def _send_raw_scancode(digit: str) -> bool:
        """Envoie le scan code hardware d'une touche chiffre via ctypes SendInput.

        Les scan codes AZERTY/QWERTY sont les MÊMES pour la rangée du haut :
          1 = 0x02, 2 = 0x03, 3 = 0x04, ..., 9 = 0x0A, 0 = 0x0B

        Ça bypass complètement Windows/pyautogui/pydirectinput et envoie directement
        au driver clavier — c'est le seul moyen garanti pour les jeux DirectInput stricts.
        """
        if not digit.isdigit():
            return False
        scan_codes = {
            "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
            "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
        }
        sc = scan_codes.get(digit)
        if sc is None:
            return False
        try:
            import ctypes  # noqa: PLC0415
            import time  # noqa: PLC0415

            KEYEVENTF_KEYUP = 0x0002
            KEYEVENTF_SCANCODE = 0x0008

            extra = ctypes.c_ulong(0)

            class KeyBdInput(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.c_ushort),
                    ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class _InputI(ctypes.Union):
                _fields_ = [("ki", KeyBdInput)]

            class Input(ctypes.Structure):
                _fields_ = [
                    ("type", ctypes.c_ulong),
                    ("ii", _InputI),
                ]

            def _send(flags: int) -> None:
                ii = _InputI()
                ii.ki = KeyBdInput(0, sc, flags, 0, ctypes.pointer(extra))
                inp = Input(1, ii)   # INPUT_KEYBOARD = 1
                ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

            # Press
            _send(KEYEVENTF_SCANCODE)
            time.sleep(0.03)
            # Release
            _send(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
            return True
        except Exception as exc:
            logger.debug("Raw scan code échec : {}", exc)
            return False

    def _do_cast(self, key: str, xy: list) -> None:
        """Presse la touche sort (AZERTY-aware) puis clique sur les coords LLM."""
        try:
            x, y = int(xy[0]), int(xy[1])
        except (ValueError, TypeError, IndexError):
            self.log_event.emit(f"⚠ target_xy invalide : {xy}", "warn")
            return
        azerty_equiv = self._AZERTY_SLOT_KEYS.get(str(key), str(key))
        self.log_event.emit(
            f"→ Cast slot {key} (touche AZERTY '{azerty_equiv}') sur ({x},{y})",
            "info",
        )
        try:
            ok = self._send_spell_hotkey(str(key))
            if not ok:
                self.log_event.emit("⚠ Échec envoi touche sort", "warn")
                return
            self.msleep(int(self._config.key_to_click_delay_sec * 1000))
            self._input.click(x, y, button="left")
            self._stats.actions_taken += 1
        except Exception as exc:
            self.log_event.emit(f"⚠ Cast échec : {exc}", "error")

    def _save_debug(self, frame, decision: dict) -> None:
        try:
            import cv2  # noqa: PLC0415
            import json as _json  # noqa: PLC0415
            from datetime import datetime  # noqa: PLC0415
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            cv2.imwrite(str(self._debug_dir / f"{ts}.jpg"), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 70])
            (self._debug_dir / f"{ts}.json").write_text(
                _json.dumps(decision, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
