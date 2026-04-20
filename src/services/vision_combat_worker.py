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

    def _ensure_dofus_focus(self) -> None:
        """Active la fenêtre Dofus AVANT chaque action pour que keys/clics arrivent bien.

        Sans ça, press_key('2') envoie la touche à la fenêtre active (PowerShell, IDE...)
        au lieu de Dofus → le sort n'est jamais lancé.
        """
        try:
            import pygetwindow as gw  # noqa: PLC0415
        except ImportError:
            return
        title_substring = (self._config.dofus_window_title or "dofus").lower()
        try:
            for w in gw.getAllWindows():
                if not w.title:
                    continue
                if title_substring in w.title.lower():
                    if not w.isActive:
                        try:
                            w.activate()
                        except Exception:
                            # Windows parfois refuse activate → minimize/restore
                            try:
                                w.minimize()
                                w.restore()
                            except Exception:
                                pass
                    return
        except Exception:
            pass

    def _tick(self) -> None:
        """Un cycle : capture → LLM → action."""
        try:
            frame = self._vision.capture()
        except Exception as exc:
            self.log_event.emit(f"Capture échec : {exc}", "error")
            return

        user_prompt = self._build_user_prompt()
        self.log_event.emit("👁 → LLM (analyse image)...", "info")

        t0 = time.time()
        decision = self._llm.ask_json(
            user_prompt,
            system=self._system_prompt,
            image_bgr=frame,
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

        self.log_event.emit(
            f"🧠 [{elapsed:.1f}s] phase={phase} | 👁 {observation[:80]}",
            "info",
        )
        if reasoning:
            self.log_event.emit(f"   💭 {reasoning[:120]}", "info")

        self._execute_action(action, phase)

        if self._config.save_debug_images:
            self._save_debug(frame, decision)

    def _build_user_prompt(self) -> str:
        shortcuts = ", ".join(
            f"touche {k}={name}"
            for k, name in sorted(self._config.spell_shortcuts.items())
        )
        return (
            f"Analyse la capture d'écran fournie. Je joue un **{self._config.class_name}**.\n"
            f"Mes raccourcis clavier sont : {shortcuts or '(aucun configuré)'}.\n"
            f"J'ai au maximum **{self._config.starting_pa} PA** et **{self._config.starting_pm} PM** par tour.\n\n"
            f"Observe attentivement l'image (phase de jeu, position de mon perso à l'anneau rouge, "
            f"position des ennemis à l'anneau bleu, état des boutons UI, popups éventuels) "
            f"puis décide UNE action à exécuter MAINTENANT.\n\n"
            f"Rappel : réponds UNIQUEMENT en JSON valide avec les champs "
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

    def _do_cast(self, key: str, xy: list) -> None:
        """Presse la touche sort puis clique sur les coords LLM."""
        try:
            x, y = int(xy[0]), int(xy[1])
        except (ValueError, TypeError, IndexError):
            self.log_event.emit(f"⚠ target_xy invalide : {xy}", "warn")
            return
        self.log_event.emit(f"→ Cast touche {key} sur ({x},{y})", "info")
        try:
            self._input.press_key(key)
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
