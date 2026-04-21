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

from src.services.combat_decision_engine import (
    CombatDecisionEngine, DecisionContext, EngineConfig,
)
from src.services.combat_knowledge import CombatKnowledge
from src.services.combat_state_reader import CombatStateReader
from src.services.combat_stats_tracker import get_tracker
from src.services.debug_visualizer import DebugSnapshot, save_debug_image
from src.services.input_service import InputService
from src.services.llm_client import LLMClient
from src.services.phase_detector import detect_phase
from src.services.pm_cell_detector import detect_pm_cells
from src.services.vision import MssVisionService


@dataclass
class VisionCombatConfig:
    class_name: str = "ecaflip"
    spell_shortcuts: dict[int, str] = field(default_factory=dict)
    # Provider LLM : "ollama", "lmstudio", "gemini"
    # v0.4.0 : défaut sur Anthropic Claude Haiku 4.5.
    # Meilleur raisonnement spatial + ~1-2s/décision vs 4-24s sur Gemini flash.
    # Coût : ~$0.1/jour en usage normal (farm 5 combats).
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_url: str = ""                    # override optionnel (défaut : selon provider)
    llm_api_key: str = ""                # clé API Gemini (obligatoire pour provider=gemini)
    # Timings optimisés pour vitesse (v0.3.1) :
    #   - scan 0.15s entre cycles (quasi instant, l'essentiel du coût est le LLM)
    #   - post_action 0.6s (animation courte ; 1.0s était trop conservateur)
    #   - key_to_click 0.12s (suffisant pour que Dofus affiche la zone de sort)
    scan_interval_sec: float = 0.15
    post_action_delay_sec: float = 0.6
    key_to_click_delay_sec: float = 0.12
    starting_pa: int = 6
    starting_pm: int = 3
    # Bonus de portée (PO) appliqué à tous les sorts "à portée modifiable".
    # Ex: stuff/buff donnant +3 PO → po_bonus = 3, tous les sorts concernés
    # ont leur portée_max augmentée d'autant dans le prompt LLM.
    po_bonus: int = 0
    max_actions_per_turn: int = 6
    # Timeout réduit : 20s max par requête LLM (avant : 90s = blocage en cas de surcharge).
    # Anthropic Haiku répond typiquement en 1-2s, on a donc 10× de marge.
    request_timeout_sec: float = 20.0
    # Mode de décision :
    #   - "hybrid" (défaut v0.5.0) : moteur règles d'abord, LLM seulement si ambigu
    #   - "llm"    : tout au LLM (comportement v0.4.x)
    #   - "rules"  : tout aux règles (pas d'appel LLM, gratuit mais moins adaptatif)
    decision_mode: str = "hybrid"
    # Active le raycasting pixel pour LoS (v0.6.0)
    use_pixel_los: bool = True
    # Humanise les mouvements souris (Bézier + jitter + délais log-normal).
    # Moins détectable mais ~200ms plus lent par clic. Recommandé.
    humanize_input: bool = True
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
            temperature=0.1,
            # 400 suffit largement : observation(80) + phase(10) + raisonnement(80) + action(40) ≈ 250
            # max_tokens haut = Claude génère plus lentement (v0.4.6 : +60% vitesse).
            max_tokens=400,
            timeout_sec=config.request_timeout_sec,
        )
        self._stats = VisionCombatStats()
        self._stop_requested = False
        self._system_prompt: str = ""
        self._debug_dir = Path("data/vision_debug")
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5
        self._latencies: list[float] = []
        # Moteur de décision déterministe (règles avant LLM)
        self._engine = CombatDecisionEngine(
            EngineConfig(
                class_name=config.class_name,
                spell_shortcuts=dict(config.spell_shortcuts),
                starting_pa=config.starting_pa,
                starting_pm=config.starting_pm,
                po_bonus=config.po_bonus,
                use_pixel_los=config.use_pixel_los,
            ),
            self._knowledge,
        )
        # Anti-boucle : détecte si le LLM répète la même action sans changement
        self._last_action_key: str = ""
        self._same_action_count: int = 0
        self._last_phase: str = ""
        # Historique des 3 derniers casts (pour détecter cible morte / mur / LoS bloquée)
        #   (slot, target_x, target_y)
        self._cast_history: list[tuple[str, int, int]] = []
        self._invalid_cast_streak: int = 0
        # Compteur anti-boucle : combien de fois on a déjà override ce tour
        self._stuck_overrides: int = 0
        # PA restants ce tour (mis à jour à chaque cast, reset au changement de tour)
        self._pa_remaining: int = config.starting_pa
        # Cache coût PA par slot (résolu via knowledge DB)
        self._spell_cost_cache: dict[str, int] = {}
        # Compteur de tour (incrémenté à chaque transition autre→mon_tour)
        self._turn_number: int = 1
        # Buffs déjà cast ce combat (reset au nouveau combat)
        self._buffs_cast: set[int] = set()
        # Tracker stats (singleton partagé)
        self._stats_tracker = get_tracker()
        # Track si le tracker a démarré un combat pour éviter double start
        self._combat_tracked: bool = False
        # Nb d'ennemis détectés au tick précédent (pour compter les kills approximatifs)
        self._prev_enemy_count: int = 0

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
        """Un cycle : capture → HSV → moteur règles → LLM si nécessaire → action."""
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

        # Détection phase rapide par analyse d'image (~10ms)
        # Court-circuite le LLM si popup victoire/défaite détectée
        phase_result = detect_phase(raw_frame)

        # Tracking : démarre un combat si on détecte le début
        if not self._combat_tracked and snap is not None and snap.ennemis:
            self._stats_tracker.on_combat_start(self._config.class_name)
            self._combat_tracked = True

        # Tracking : compte les kills (diminution du nb ennemis entre 2 ticks)
        current_enemy_count = len(snap.ennemis) if snap else 0
        if current_enemy_count < self._prev_enemy_count:
            for _ in range(self._prev_enemy_count - current_enemy_count):
                self._stats_tracker.on_kill()
        self._prev_enemy_count = current_enemy_count

        if (phase_result.phase == "popup_victoire"
                and phase_result.confidence > 0.5
                and self._config.decision_mode in ("hybrid", "rules")):
            self.log_event.emit(
                f"🏆 Popup détectée → close ({phase_result.reason})", "info",
            )
            # Finalise le combat (victoire)
            if self._combat_tracked:
                snapshot = self._stats_tracker.on_combat_end("victory")
                self.log_event.emit(
                    f"📊 Combat #{self._stats_tracker.get_global_stats().total_combats} terminé : "
                    f"{snapshot.get('kills_estimated', 0)} kills, "
                    f"{snapshot.get('turns_played', 0)} tours, "
                    f"{snapshot.get('duration_sec', 0):.0f}s",
                    "info",
                )
                self._combat_tracked = False
                self._prev_enemy_count = 0
            self._execute_action({"type": "close_popup"}, "popup_victoire")
            return

        # Essai moteur déterministe v0.6.0 (LoS pixel + targeting + playbooks)
        if self._config.decision_mode in ("hybrid", "rules"):
            ctx = DecisionContext(
                snap=snap,
                pa_remaining=self._pa_remaining,
                cast_history=list(self._cast_history),
                stuck_overrides=self._stuck_overrides,
                turn_number=self._turn_number,
                frame_bgr=raw_frame,
                buffs_cast_this_fight=self._buffs_cast,
            )
            rule_action = self._engine.decide(ctx)
            rtype = str(rule_action.get("type", "")).lower()
            rreason = rule_action.get("reason", "")

            # Le moteur donne une action déterministe → on l'exécute direct
            if rtype != "defer_to_llm":
                self.log_event.emit(
                    f"⚙ Moteur règles : {rtype} — {rreason}",
                    "info",
                )
                # Track les buffs cast pour ne pas les refaire
                if "_buff_slot" in rule_action:
                    self._buffs_cast.add(rule_action["_buff_slot"])
                # Stats
                self._stats_tracker.on_decision("rules", latency_ms=0.1)
                if rtype in ("cast_spell", "spell"):
                    self._stats_tracker.on_cast(str(rule_action.get("spell_key", "?")))
                # Debug visual si activé
                if self._config.save_debug_images:
                    self._save_decision_debug(raw_frame, snap, rule_action, phase_result.phase)
                self._execute_action(rule_action, "mon_tour")
                return

            # Sinon, si mode rules strict, on fait wait (pas de LLM)
            if self._config.decision_mode == "rules":
                self.log_event.emit(
                    f"⚙ Règles incertaines ({rreason}) — wait (mode rules)",
                    "info",
                )
                self._execute_action({"type": "wait"}, "inconnu")
                return
            # Sinon (mode hybrid), on continue vers le LLM
            self.log_event.emit(
                f"🤔 Règles ambiguës ({rreason}) → recours au LLM",
                "info",
            )

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
        # Stats session
        self._stats_tracker.on_decision("llm", latency_ms=elapsed * 1000)

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

        # Si l'API a renvoyé une erreur, l'afficher clairement
        if decision.get("_error"):
            err = decision.get("_error", "")
            self.log_event.emit(
                f"❌ ERREUR API LLM : {err[:300]}",
                "error",
            )

        phase = decision.get("phase", "?")
        observation = decision.get("observation") or decision.get("situation", "")
        reasoning = decision.get("raisonnement") or decision.get("reasoning", "")
        action = decision.get("action", {}) or {}

        # Si le dict est quasi-vide (pas de phase/action), log la réponse brute
        # pour comprendre ce que le LLM a renvoyé.
        if phase == "?" or not action:
            raw_debug = decision.get("_raw_text", "") or ""
            self.log_event.emit(
                f"🔍 DEBUG brut LLM : {raw_debug[:250] or '(vide)'}",
                "warn",
            )

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

        # Reset l'historique du tour si on revient en mon_tour depuis autre chose
        # (nouveau tour Dofus = PA rechargés, cibles repositionnées).
        if phase == "mon_tour" and self._last_phase and self._last_phase != "mon_tour":
            self._cast_history.clear()
            self._stuck_overrides = 0
            self._pa_remaining = self._config.starting_pa
            self._turn_number += 1
        # Reset complet à chaque popup victoire/défaite (fin du combat)
        if phase in ("popup_victoire", "popup_defaite"):
            self._turn_number = 1
            self._buffs_cast.clear()
        self._last_phase = phase

        # Override mécanique si le LLM re-cast sur une cible déjà visée (boucle).
        action = self._override_if_stuck(action, snap)

        self._execute_action(action, phase)

        if self._config.save_debug_images:
            self._save_debug(frame, decision)

    def _get_spell_cost(self, slot: str) -> int:
        """Résout le coût PA d'un slot via spell_shortcuts + knowledge DB.
        Fallback à 3 PA si pas trouvé."""
        if slot in self._spell_cost_cache:
            return self._spell_cost_cache[slot]
        cost = 3
        try:
            slot_int = int(slot)
            spell_name = self._config.spell_shortcuts.get(slot_int, "").strip().lower()
            if spell_name:
                cls = self._knowledge.get_class(self._config.class_name)
                if cls:
                    for s in cls.sorts:
                        sname = str(s.get("nom", "")).strip().lower()
                        sid = str(s.get("id", "")).strip().lower()
                        if sname == spell_name or sid == spell_name:
                            cost = int(s.get("pa", 3))
                            break
        except Exception:
            pass
        self._spell_cost_cache[slot] = cost
        return cost

    def _override_if_stuck(self, action: dict, snap) -> dict:
        """Si le LLM re-propose un cast déjà effectué sur la même cible (±50px),
        on l'override mécaniquement :
          - 1er stuck → click_xy sur une case qui contourne (perpendiculaire à l'axe)
          - 2e stuck → end_turn (pas d'angle viable)

        Cette fonction garantit qu'on ne reste JAMAIS bloqué en boucle infinie,
        même si le LLM ne comprend pas la ligne de vue.
        """
        atype = str(action.get("type", "")).lower()
        if atype not in ("cast_spell", "spell"):
            return action
        if not self._cast_history:
            return action

        xy = action.get("target_xy") or action.get("target")
        key = action.get("spell_key") or action.get("key")
        if not xy or len(xy) != 2 or key is None:
            return action
        try:
            tx, ty = int(xy[0]), int(xy[1])
            slot = str(key)
        except (TypeError, ValueError):
            return action

        # Cherche un cast identique dans l'historique (même slot ± 50px)
        already = any(
            s == slot and abs(hx - tx) <= 50 and abs(hy - ty) <= 50
            for s, hx, hy in self._cast_history
        )
        if not already:
            return action

        self._stuck_overrides += 1

        # 2e override du tour → end_turn sans appel
        if self._stuck_overrides >= 2:
            self.log_event.emit(
                "🚨 Bot coincé 2x sur la même cible → FIN DE TOUR FORCÉE (LoS bloquée ou cible morte)",
                "warn",
            )
            return {"type": "end_turn"}

        # 1er override → on doit bouger, mais 2 stratégies selon distance :
        #   - mob LOIN (>3 cases de mouvement possibles) → s'APPROCHER vers lui
        #   - mob PROCHE → case PERPENDICULAIRE pour contourner un mur
        perso_xy: tuple[int, int] | None = None
        if snap is not None and snap.perso:
            perso_xy = (snap.perso.x, snap.perso.y)
        else:
            perso_xy = (1280, 720)

        dx, dy = tx - perso_xy[0], ty - perso_xy[1]
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        # Distance approx en cases iso (86/43)
        dist_cases = max(abs(dx) / 86, abs(dy) / 43)

        if dist_cases > 4:
            # MOB LOIN : on va directement vers lui (avance de ~3 cases)
            ADVANCE = 220  # ~3 cases Dofus
            bypass_x = int(perso_xy[0] + (dx / length) * ADVANCE)
            bypass_y = int(perso_xy[1] + (dy / length) * ADVANCE)
            strategy = "APPROCHE"
        else:
            # MOB PROCHE + re-cast raté = mur → perpendiculaire
            nx, ny = -dy / length, dx / length
            OFFSET = 140
            bypass_x = int(perso_xy[0] + nx * OFFSET + (dx / length) * 60)
            bypass_y = int(perso_xy[1] + ny * OFFSET + (dy / length) * 60)
            strategy = "CONTOURNEMENT"

        bypass_x = max(50, min(bypass_x, 2500))
        bypass_y = max(50, min(bypass_y, 1400))

        self.log_event.emit(
            f"🚨 Override {strategy} : re-cast sur ({tx},{ty}) détecté "
            f"(dist={dist_cases:.0f}c) → je bouge vers ({bypass_x},{bypass_y})",
            "warn",
        )
        return {"type": "click_xy", "target_xy": [bypass_x, bypass_y]}

    def _build_user_prompt(self, snap=None) -> str:
        shortcuts = ", ".join(
            f"touche {k}={name}"
            for k, name in sorted(self._config.spell_shortcuts.items())
        )
        allowed_slots = sorted(self._config.spell_shortcuts.keys())
        if allowed_slots:
            allowed_block = (
                f"\nSLOTS UTILISABLES (liste fermée) : {allowed_slots}. "
                f"Tout autre slot est VIDE."
            )
        else:
            allowed_block = (
                "\nAUCUN SORT CONFIGURÉ → cast_spell interdit. "
                "Utilise click_xy / end_turn / wait."
            )
        # Précalcul du scale qui sera appliqué à l'image envoyée au LLM.
        # Le LLM verra une image redimensionnée à max 2048px → on lui donne
        # les coords HSV dans CET espace image (pour qu'il copie sans calcul).
        img_scale = 1.0
        try:
            frame = self._vision.capture()
            h, w = frame.shape[:2]
            # Doit correspondre à `max_side` dans LLMClient._encode_image_b64
            MAX_SIDE = 1024
            if max(h, w) > MAX_SIDE:
                img_scale = MAX_SIDE / max(h, w)
        except Exception:
            pass

        detections_block = ""
        if snap is not None:
            # Dofus isométrique : 1 case ≈ 86px horizontal, 43px vertical.
            # Distance en cases ≈ max(|dx|/86, |dy|/43).
            CELL_X, CELL_Y = 86, 43
            lines = []
            perso_px_screen = None
            if snap.perso:
                # Coords dans l'espace image envoyé au LLM (il voit la même chose).
                px_img = int(snap.perso.x * img_scale)
                py_img = int(snap.perso.y * img_scale)
                perso_px_screen = (snap.perso.x, snap.perso.y)
                lines.append(f"  PERSO en ({px_img},{py_img})")
            for i, e in enumerate(snap.ennemis, 1):
                ex_img = int(e.x * img_scale)
                ey_img = int(e.y * img_scale)
                line = f"  MOB{i} en ({ex_img},{ey_img})"
                if perso_px_screen:
                    dist_cases = max(
                        abs(e.x - perso_px_screen[0]) / CELL_X,
                        abs(e.y - perso_px_screen[1]) / CELL_Y,
                    )
                    line += f" — dist={dist_cases:.0f} cases"
                lines.append(line)
            if lines:
                detections_block = (
                    "\nPositions sur l'image que tu reçois (coords en pixels image) :\n"
                    + "\n".join(lines)
                    + "\n→ target_xy = coords dans CETTE image "
                    "(notre code les reconvertira automatiquement en pixels écran)."
                )
        po_bonus_line = ""
        if self._config.po_bonus > 0:
            po_bonus_line = (
                f"\nBonus portée stuff : +{self._config.po_bonus} PO "
                f"(ajoute à la portée_max des sorts modifiables, ex: 1-5 devient 1-{5 + self._config.po_bonus})."
            )

        # Historique des casts récents + signal anti-boucle.
        # Les coords dans _cast_history sont en ECRAN (stockées par _do_cast),
        # on les convertit en coords IMAGE pour rester cohérent avec le reste du prompt.
        history_block = ""
        if self._cast_history:
            hist_parts = []
            for s, hx, hy in self._cast_history:
                hx_img = int(hx * img_scale)
                hy_img = int(hy * img_scale)
                hist_parts.append(f"slot{s}→({hx_img},{hy_img})")
            history_block = f"\nCasts ce tour : {' ; '.join(hist_parts)}"
            if snap is not None and snap.ennemis:
                last_slot, lx, ly = self._cast_history[-1]
                for e in snap.ennemis:
                    if abs(e.x - lx) <= 40 and abs(e.y - ly) <= 40:
                        lx_img = int(lx * img_scale)
                        ly_img = int(ly * img_scale)
                        history_block += (
                            f" ⚠ ALERTE : MOB toujours à ({lx_img},{ly_img}) après slot {last_slot} "
                            f"→ LoS BLOQUÉE, ne re-cast PAS, bouge ou change de cible."
                        )
                        break

        # Coût des sorts configurés (aide le LLM à décider s'il peut encore cast)
        costs_hint = ""
        if self._config.spell_shortcuts:
            pieces = []
            for k in sorted(self._config.spell_shortcuts.keys()):
                pieces.append(f"slot{k}={self._get_spell_cost(str(k))}PA")
            costs_hint = f" Coûts: {', '.join(pieces)}."

        pa_rem = self._pa_remaining
        min_cost = min(
            (self._get_spell_cost(str(k)) for k in self._config.spell_shortcuts),
            default=99,
        )
        continue_rule = ""
        if pa_rem >= min_cost:
            continue_rule = (
                f" TU AS ENCORE {pa_rem} PA → si un mob est à portée et LoS ok, "
                f"CONTINUE à cast (ne fais PAS end_turn avec des PA inutilisés)."
            )

        return (
            f"Classe: {self._config.class_name}. "
            f"Raccourcis: {shortcuts or '(aucun)'}.{costs_hint} "
            f"PA restants: {pa_rem}/{self._config.starting_pa}. "
            f"PM max: {self._config.starting_pm}."
            f"{po_bonus_line}"
            f"{allowed_block}"
            f"{detections_block}"
            f"{history_block}"
            f"{continue_rule}\n"
            f"Décide 1 action. Réponds en JSON strict (observation/phase/raisonnement/action)."
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
            # Garde-fou : refuse de cast un slot non configuré (halluciné par le LLM)
            try:
                slot_int = int(str(key).strip())
            except (ValueError, TypeError):
                slot_int = -1
            allowed = set(self._config.spell_shortcuts.keys())
            if allowed and slot_int not in allowed:
                self.log_event.emit(
                    f"🚫 Slot {key} non configuré (autorisés : {sorted(allowed)}) → cast ignoré",
                    "warn",
                )
                self._invalid_cast_streak = getattr(self, "_invalid_cast_streak", 0) + 1
                if self._invalid_cast_streak >= 2:
                    self.log_event.emit(
                        "→ Trop de casts invalides consécutifs → fin de tour automatique",
                        "warn",
                    )
                    self._input.press_key("f1")
                    self._cast_history.clear()
                    self._invalid_cast_streak = 0
                    self._stats.actions_taken += 1
                self.msleep(int(self._config.post_action_delay_sec * 1000))
                return
            self._invalid_cast_streak = 0
            self._do_cast(str(key), xy)

        elif atype == "click_xy":
            xy = action.get("target_xy") or action.get("xy")
            if xy and len(xy) == 2:
                self.log_event.emit(f"→ Clic ({xy[0]},{xy[1]})", "info")
                self._do_click(int(xy[0]), int(xy[1]))
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
            self._cast_history.clear()
            self._stuck_overrides = 0
            self._pa_remaining = self._config.starting_pa

        elif atype == "close_popup":
            self.log_event.emit("→ Ferme popup (Escape)", "info")
            self._input.press_key("escape")
            self._stats.actions_taken += 1
            self._cast_history.clear()
            self._stuck_overrides = 0

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
            self._do_click(x, y)
            self._stats.actions_taken += 1
            self._cast_history.append((str(key), x, y))
            if len(self._cast_history) > 3:
                self._cast_history.pop(0)
            # Décrémente les PA restants selon le coût du sort
            cost = self._get_spell_cost(str(key))
            self._pa_remaining = max(0, self._pa_remaining - cost)
        except Exception as exc:
            self.log_event.emit(f"⚠ Cast échec : {exc}", "error")

    def _do_click(self, x: int, y: int, *, button: str = "left") -> None:
        """Clic avec humanisation optionnelle (Bézier + jitter + délais)."""
        if self._config.humanize_input:
            try:
                from src.services.human_input import human_click  # noqa: PLC0415
                human_click(self._input, x, y, button=button)
                return
            except Exception as exc:
                logger.debug("human_click échec, fallback classic : {}", exc)
        self._input.click(x, y, button=button)

    def _save_decision_debug(
        self, frame, snap, action: dict, phase: str,
    ) -> None:
        """Sauve une image annotée montrant ce que le bot a vu et décidé."""
        try:
            pm_centers = []
            try:
                cells = detect_pm_cells(frame)
                pm_centers = [(c.x, c.y) for c in cells]
            except Exception:
                pass

            enemies = []
            chosen_target_xy = None
            if snap and snap.ennemis:
                for e in snap.ennemis:
                    enemies.append({
                        "x": e.x, "y": e.y,
                        "hp_pct": e.hp_pct,
                    })
                # Cible = la première triée par score (approximation : plus proche)
                if action.get("target_xy"):
                    chosen_target_xy = tuple(action["target_xy"])

            perso_xy = None
            if snap and snap.perso:
                perso_xy = (snap.perso.x, snap.perso.y)

            movement_xy = None
            if action.get("type") in ("click_xy",):
                movement_xy = tuple(action.get("target_xy", [0, 0]))

            dbg = DebugSnapshot(
                frame_bgr=frame,
                perso_xy=perso_xy,
                enemies=enemies,
                pm_cells=pm_centers,
                chosen_target_xy=chosen_target_xy,
                movement_target_xy=movement_xy,
                action_type=str(action.get("type", "")),
                action_reason=str(action.get("reason", "")),
                turn_number=self._turn_number,
                pa_remaining=self._pa_remaining,
                phase=phase,
            )
            save_debug_image(dbg, self._debug_dir)
        except Exception as exc:
            logger.debug("save_decision_debug échec : {}", exc)

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
