"""Worker qui joue les combats automatiquement (IA locale Ollama + règles expert).

Flow :
    1. Scan jusqu'à détecter un combat (CombatDetector.is_in_combat())
    2. Dès qu'il est notre tour :
       a. Lit l'état visuel complet (perso, ennemis, PA/PM/HP)
       b. Construit un prompt enrichi (règles Dofus + stratégie classe + état)
       c. Demande la décision à Ollama (si dispo) ou utilise stratégie classe fallback
       d. Exécute l'action : touche clavier (sort) + clic sur case cible + fin tour
    3. Répète jusqu'à la fin du combat
    4. Retourne en mode scan

Configuration minimale :
    - `class_name` : classe du perso ("ecaflip", "iop", ...) — pour le prompt LLM
    - `spell_shortcuts` : dict {1: "griffe_iop", 2: "pile_ou_face", ...}
      → raccourcis clavier 1-0 déjà configurés en jeu (id sort canonique dans la valeur)
    - `use_ollama` : bool, si True tente Ollama ; sinon stratégie classe (règles expert)
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import numpy as np
from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from src.services.combat_detector import CombatDetector, CombatSnapshot
from src.services.combat_knowledge import CombatKnowledge, TurnState
from src.services.combat_state_reader import CombatStateReader, CombatStateSnapshot
from src.services.input_service import InputService
from src.services.ollama_client import OllamaClient
from src.services.vision import MssVisionService


@dataclass
class CombatConfig:
    class_name: str = "ecaflip"         # id classe (ex: "ecaflip", "iop")
    # Raccourcis clavier vers sorts : {1: "Sort 1", 2: "Sort 2"}
    spell_shortcuts: dict[int, str] = field(default_factory=dict)
    # Mode IA
    use_ollama: bool = False
    ollama_model: str = "phi3:mini"
    ollama_url: str = "http://localhost:11434"
    # Timings (optimisés pour laisser l'animation Dofus finir avant next action)
    scan_interval_sec: float = 1.0        # entre scans hors combat
    turn_poll_interval_sec: float = 0.8   # entre scans en combat (attente tour)
    key_to_click_delay_sec: float = 0.45  # entre touche sort et clic cible
    post_action_delay_sec: float = 1.6    # animation du sort + re-observation
    action_delay_sec: float = 0.3         # legacy
    max_actions_per_turn: int = 6         # garde-fou anti-boucle
    dofus_window_title: str | None = None
    # Bouton "Terminer le tour" : ratio de position pour clic (fallback si HSV échoue)
    end_turn_btn_ratio_x: float = 0.79
    end_turn_btn_ratio_y: float = 0.89
    # Estimation des PA/PM au début du tour (ajuste selon ton perso)
    starting_pa: int = 6
    starting_pm: int = 3
    # Budget PA pour arrêter la boucle quand on ne peut plus rien faire
    min_pa_to_act: int = 2


@dataclass
class CombatStats:
    fights_done: int = 0
    turns_played: int = 0
    actions_taken: int = 0
    ollama_decisions: int = 0
    rule_decisions: int = 0


class CombatRunnerWorker(QThread):
    """Thread qui joue les combats (LLM ou règles)."""

    log_event = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)   # "idle" / "waiting_combat" / "playing" / "stopped"
    stats_updated = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(
        self,
        vision: MssVisionService,
        input_svc: InputService,
        config: CombatConfig,
    ) -> None:
        super().__init__()
        self._vision = vision
        self._input = input_svc
        self._config = config
        self._detector = CombatDetector(vision)
        self._state_reader = CombatStateReader(vision)
        self._knowledge = CombatKnowledge()
        self._ollama: OllamaClient | None = None
        self._stats = CombatStats()
        self._stop_requested = False
        self._last_turn_played_at: float = 0.0
        self._turn_counter: int = 0
        self._last_state: CombatStateSnapshot | None = None
        self._system_prompt_cache: str | None = None

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        self.log_event.emit(
            f"⚔ CombatRunner démarré : classe={self._config.class_name}, "
            f"ollama={'ON' if self._config.use_ollama else 'OFF'}",
            "info",
        )
        self.state_changed.emit("waiting_combat")

        # Check knowledge base
        if self._knowledge.has_class(self._config.class_name):
            self.log_event.emit(
                f"📚 Connaissance classe chargée : {self._config.class_name}",
                "info",
            )
        else:
            available = ", ".join(self._knowledge.available_classes()) or "aucune"
            self.log_event.emit(
                f"⚠ Pas de knowledge pour '{self._config.class_name}'. "
                f"Disponibles : {available}. Stratégie générique utilisée.",
                "warn",
            )

        if self._config.use_ollama:
            self._ollama = OllamaClient(
                model=self._config.ollama_model,
                base_url=self._config.ollama_url,
                temperature=0.2,
                num_predict=400,
            )
            if self._ollama.is_available():
                self.log_event.emit(
                    f"✓ Ollama détecté ({self._config.ollama_model})", "info",
                )
                if not self._ollama.has_model():
                    self.log_event.emit(
                        f"⚠ Modèle '{self._config.ollama_model}' pas trouvé — "
                        f"pull-le via `ollama pull {self._config.ollama_model}`. "
                        f"Je continue avec les règles expert en attendant.",
                        "warn",
                    )
            else:
                self.log_event.emit(
                    "⚠ Ollama indisponible — stratégie classe expert utilisée", "warn",
                )
                self._ollama = None

        # Pré-calcule le system prompt (réutilisé à chaque tour)
        self._system_prompt_cache = self._knowledge.build_system_prompt(
            self._config.class_name,
        )

        while not self._stop_requested:
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Erreur CombatRunner tick")
                self.log_event.emit(f"⚠ Erreur tick : {exc}", "error")

            self.stats_updated.emit(self._stats)
            if not self._stop_requested:
                self.msleep(int(self._config.scan_interval_sec * 1000))

        self.log_event.emit("⏹ CombatRunner arrêté", "info")
        self.state_changed.emit("stopped")
        self.stopped.emit()

    # ---------- Tick principal ----------

    def _tick(self) -> None:
        # En combat si on détecte perso (cercle rouge) ET au moins 1 ennemi
        # À PORTÉE (pas éparpillé sur la map). Filtre crucial pour ne pas jouer
        # hors combat alors que des mobs lointains sont visibles.
        COMBAT_RADIUS_PX = 600
        state = self._state_reader.read()
        detector_snap = self._detector.snapshot()

        has_perso = state.perso is not None
        nearby_enemies = 0
        if has_perso:
            px, py = state.perso.x, state.perso.y
            for e in state.ennemis:
                if float(np.hypot(e.x - px, e.y - py)) <= COMBAT_RADIUS_PX:
                    nearby_enemies += 1
        else:
            nearby_enemies = len(state.ennemis)  # fallback sans perso

        button_visible = detector_snap.in_combat

        # En combat = perso + ennemi proche (+ éventuellement bouton visible)
        in_combat = has_perso and nearby_enemies > 0

        if not in_combat:
            return  # on scanne

        # Anti-double-play
        now = time.time()
        if now - self._last_turn_played_at < 2.0:
            return

        self.state_changed.emit("playing")
        self._turn_counter += 1
        self.log_event.emit(
            f"🎯 Tour #{self._turn_counter} — "
            f"perso={'✓' if has_perso else '✗'} "
            f"ennemis_proches={nearby_enemies}/{len(state.ennemis)} total "
            f"btn={'✓' if button_visible else '✗'}",
            "info",
        )
        # Réutilise le state qu'on vient de lire pour éviter une re-capture
        self._last_state = state
        self._play_turn(state=state)
        self._last_turn_played_at = time.time()
        self._stats.turns_played += 1

    # ---------- Jouer un tour ----------

    def _play_turn(self, state: CombatStateSnapshot | None = None) -> None:
        """Joue un tour en boucle observation-décision-action-observation.

        Flow comme un vrai joueur :
          1. Echap pour s'assurer qu'aucun sort n'est sélectionné en parasite
          2. Observe l'état
          3. Décide LA meilleure action (1 seule à la fois)
          4. Exécute
          5. Attend animation Dofus (~1.2s)
          6. Re-observe pour voir les changements
          7. Répète jusqu'à plus de PA ou plus d'ennemis
          8. Fin de tour
        """
        # Start propre : annule toute sélection de sort en cours
        try:
            self._input.press_key("escape")
            self.msleep(200)
        except Exception:
            pass

        # État initial
        if state is None:
            state = self._state_reader.read()
        self._last_state = state
        self._log_state_summary(state)

        # Si zone de portée détectée (trop de cercles bleus), Escape encore et re-lit
        if state.suspected_spell_overlay:
            self.log_event.emit(
                "⚠ Zone de portée sort détectée au début du tour → Escape",
                "warn",
            )
            try:
                self._input.press_key("escape")
                self.msleep(400)
            except Exception:
                pass
            state = self._state_reader.read()
            self._last_state = state

        if not state.ennemis:
            self.log_event.emit("⚠ Aucun ennemi détecté — skip tour", "warn")
            self._last_turn_played_at = 0.0
            self._turn_counter -= 1
            return

        # Tracker PA/PM localement (OCR souvent peu fiable)
        pa_restants = state.pa_restants if state.pa_restants else self._config.starting_pa
        pm_restants = state.pm_restants if state.pm_restants else self._config.starting_pm
        pa_initial = pa_restants
        pm_initial = pm_restants

        # Suivi du nombre d'ennemis pour détecter les morts
        enemies_count_start = len(state.ennemis)

        actions_played = 0
        last_action_id: str | None = None
        repeat_count = 0

        self.log_event.emit(
            f"   ➤ Démarrage tour : {pa_restants} PA / {pm_restants} PM, "
            f"{enemies_count_start} ennemi(s) visible(s)",
            "info",
        )

        while actions_played < self._config.max_actions_per_turn:
            if self._stop_requested:
                break
            if pa_restants < self._config.min_pa_to_act:
                self.log_event.emit(f"   PA épuisés ({pa_restants}), fin des actions", "info")
                break

            # Re-observe (sauf au 1er tour où on a déjà state)
            if actions_played > 0:
                new_state = self._state_reader.read()
                # Si overlay sort détecté : Escape et re-lit
                if new_state.suspected_spell_overlay:
                    try:
                        self._input.press_key("escape")
                        self.msleep(300)
                    except Exception:
                        pass
                    new_state = self._state_reader.read()
                prev_count = len(state.ennemis)
                new_count = len(new_state.ennemis)
                state = new_state
                if new_count < prev_count:
                    killed = prev_count - new_count
                    self.log_event.emit(
                        f"   💀 {killed} ennemi(s) mort(s) — reste {new_count}",
                        "info",
                    )
                if not state.ennemis:
                    self.log_event.emit("   ✓ Tous les ennemis morts !", "info")
                    break

            # Décide UNE action
            action = self._decide_single_action(state, pa_restants)
            if action is None:
                self.log_event.emit("   Aucune action disponible, fin du tour", "info")
                break

            # Garde-fou anti-spam même sort (si cooldown pas géré)
            action_id = action.get("spell_id") or str(action.get("key"))
            if action_id == last_action_id:
                repeat_count += 1
                if repeat_count >= 3:
                    self.log_event.emit(
                        f"   Même action 3× de suite ({action_id}) — stop",
                        "warn",
                    )
                    break
            else:
                repeat_count = 0
            last_action_id = action_id

            # Exécute
            pa_cost = int(action.get("pa_cost", 3))
            self._execute_action(action, state)
            pa_restants -= pa_cost
            actions_played += 1

            # Attends animation (observation fiable ensuite)
            self.msleep(int(self._config.post_action_delay_sec * 1000))

        enemies_killed = enemies_count_start - len(state.ennemis)
        self.log_event.emit(
            f"🏁 Tour fini : {actions_played} actions, "
            f"{pa_initial - pa_restants}/{pa_initial} PA utilisés, "
            f"{enemies_killed}/{enemies_count_start} ennemis tués",
            "info",
        )

        # Fin du tour (localise le bouton dynamiquement)
        self._click_end_turn()

    def _decide_single_action(
        self,
        state: CombatStateSnapshot,
        pa_restants: int,
    ) -> dict | None:
        """Choisit UNE action pour le tour courant.

        Par défaut : règles expert (fiable et déterministe).
        Si Ollama activé et disponible : demande conseil, mais fallback règles.
        """
        # Règles expert : choisit le meilleur sort dans l'ordre de priorité classe
        cls = self._knowledge.get_class(self._config.class_name)
        if cls is None:
            return self._fallback_basic_action(state, pa_restants)

        enemy = state.enemy_nearest()
        if enemy is None:
            return None

        # Distance approximative en cases (~60px par case à la résolution standard)
        dist_px = None
        if state.perso is not None:
            dist_px = int(np.hypot(enemy.x - state.perso.x, enemy.y - state.perso.y))
        dist_cases = max(1, dist_px // 60) if dist_px else 3

        shortcuts_by_id = {name: k for k, name in self._config.spell_shortcuts.items()}

        # Parcourt les sorts du knowledge base dans l'ordre (plus gros PA d'abord)
        # mais filtre ceux qui n'ont pas de raccourci ou qui ne matchent pas la portée
        candidates = sorted(cls.sorts, key=lambda s: -int(s.get("pa", 0)))

        for s in candidates:
            spell_id = s.get("id")
            cost = int(s.get("pa", 99))
            if cost > pa_restants:
                continue
            if cost < self._config.min_pa_to_act:
                continue
            # Sort self_buff seulement au 1er tour
            if s.get("type") == "self_buff" and self._turn_counter > 1:
                continue
            # Portée
            po_min = int(s.get("po_min", 1))
            po_max = int(s.get("po_max", 99))
            if not (po_min <= dist_cases <= po_max):
                continue
            # Raccourci configuré ?
            key = shortcuts_by_id.get(spell_id)
            if key is None:
                continue
            return {
                "type": "spell",
                "spell_id": spell_id,
                "key": key,
                "target": "enemy_nearest",
                "pa_cost": cost,
                "reasoning": f"{s.get('nom', spell_id)} ({cost}PA, portée {po_min}-{po_max}, dist {dist_cases})",
            }

        # Rien de dispo dans les sorts connus → fallback sur la touche 1
        return self._fallback_basic_action(state, pa_restants)

    def _fallback_basic_action(
        self,
        state: CombatStateSnapshot,
        pa_restants: int,
    ) -> dict | None:
        """Fallback : cast la touche 1 sur le plus proche."""
        if not self._config.spell_shortcuts:
            return None
        key = sorted(self._config.spell_shortcuts.keys())[0]
        spell_id = self._config.spell_shortcuts.get(key, "spell_1")
        return {
            "type": "spell",
            "spell_id": spell_id,
            "key": key,
            "target": "enemy_nearest",
            "pa_cost": 3,  # estimation par défaut
            "reasoning": f"Fallback : touche {key}",
        }

    def _log_state_summary(self, state: CombatStateSnapshot) -> None:
        parts = []
        if state.pa_restants is not None:
            parts.append(f"PA={state.pa_restants}")
        if state.pm_restants is not None:
            parts.append(f"PM={state.pm_restants}")
        if state.hp_pct is not None:
            parts.append(f"HP={int(state.hp_pct)}%")
        parts.append(f"ennemis={len(state.ennemis)}")
        if state.distance_ennemi_proche is not None:
            parts.append(f"dist={state.distance_ennemi_proche}px")
        self.log_event.emit("👁 État : " + " ".join(parts), "info")

    def _decide_actions(self, state: CombatStateSnapshot) -> dict:
        """Décide les actions à jouer. Ollama > règles classe expert."""
        # Ollama enrichi avec knowledge base
        if self._ollama is not None and self._ollama.is_available():
            result = self._decide_via_ollama(state)
            if result is not None:
                self._stats.ollama_decisions += 1
                result["source"] = "ollama"
                return result

        # Fallback : stratégie classe expert (à partir du knowledge base)
        self._stats.rule_decisions += 1
        return self._decide_via_class_strategy(state)

    def _decide_via_ollama(self, state: CombatStateSnapshot) -> dict | None:
        """Prompt enrichi (règles + classe + état live)."""
        if self._ollama is None:
            return None

        # Inverse le mapping : touche → id de sort (on veut id → touche pour l'IA)
        shortcuts_by_id = {name: k for k, name in self._config.spell_shortcuts.items()}

        turn_state = TurnState(
            pa_restants=state.pa_restants,
            pm_restants=state.pm_restants,
            hp_perso=state.hp_perso,
            hp_perso_max=state.hp_perso_max,
            hp_pourcent=state.hp_pct,
            distance_ennemi_proche=state.distance_ennemi_proche,
            tour_numero=self._turn_counter,
            ennemis=[
                {"distance": int(np.hypot(e.x - (state.perso.x if state.perso else 0),
                                          e.y - (state.perso.y if state.perso else 0)))}
                for e in state.ennemis
            ] if state.perso else [{"pos": (e.x, e.y)} for e in state.ennemis],
            allies=[{"pos": (a.x, a.y)} for a in state.allies],
            spell_shortcuts=self._config.spell_shortcuts,
        )
        user_prompt = self._knowledge.build_turn_prompt(
            self._config.class_name, turn_state,
        )

        response = self._ollama.decide_json(
            user_prompt,
            system=self._system_prompt_cache,
            fallback={"actions": []},
        )
        if not response.get("actions"):
            return None
        # Résout les spell_id → touche clavier
        for act in response.get("actions", []):
            if act.get("type") == "spell":
                spell_id = act.get("spell_id", "")
                if "key" not in act:
                    key = shortcuts_by_id.get(spell_id)
                    if key is not None:
                        act["key"] = key
        return response

    def _decide_via_class_strategy(self, state: CombatStateSnapshot) -> dict:
        """Stratégie fallback : utilise le knowledge base de la classe pour choisir.

        Heuristique : parcourt les sorts dans l'ordre de priorité de la classe,
        prend le premier qui respecte le coût PA et la distance de l'ennemi.
        """
        cls = self._knowledge.get_class(self._config.class_name)
        pa = state.pa_restants if state.pa_restants is not None else 6
        enemy = state.enemy_nearest()
        shortcuts_by_id = {name: k for k, name in self._config.spell_shortcuts.items()}

        # Distance approximative en "cases" (~60px par case à la résolution standard)
        dist_cases = None
        if state.distance_ennemi_proche is not None:
            dist_cases = max(1, state.distance_ennemi_proche // 60)

        actions: list[dict] = []
        pa_left = pa

        if cls is not None and enemy is not None:
            # Essaie de caster les sorts dans l'ordre des priorités jusqu'à épuiser PA
            available_spells = sorted(
                cls.sorts,
                key=lambda s: -int(s.get("pa", 99)),  # plus gros PA d'abord
            )
            while pa_left >= 2:
                casted = False
                for s in available_spells:
                    cost = int(s.get("pa", 99))
                    if cost > pa_left:
                        continue
                    if s.get("type") == "self_buff" and self._turn_counter > 1:
                        continue
                    if dist_cases is not None:
                        po_min = int(s.get("po_min", 1))
                        po_max = int(s.get("po_max", 99))
                        if not (po_min <= dist_cases <= po_max):
                            continue
                    spell_id = s.get("id")
                    key = shortcuts_by_id.get(spell_id)
                    if key is None:
                        continue
                    actions.append({
                        "type": "spell",
                        "spell_id": spell_id,
                        "key": key,
                        "target": "enemy_nearest",
                    })
                    pa_left -= cost
                    casted = True
                    break
                if not casted:
                    break

        if not actions and self._config.spell_shortcuts:
            # Vraiment rien de dispo : spam la touche 1
            key = sorted(self._config.spell_shortcuts.keys())[0]
            actions.append({"type": "spell", "key": key, "target": "enemy_nearest"})

        return {
            "source": "rule_expert",
            "reasoning": (
                f"Stratégie classe ({self._config.class_name}) — "
                f"{len(actions)} action(s), "
                f"{pa - pa_left}/{pa} PA utilisés, "
                f"dist={dist_cases}"
            ),
            "actions": actions,
        }

    # ---------- Exécution ----------

    def _execute_action(self, action: dict, state: CombatStateSnapshot) -> None:
        atype = action.get("type", "")
        if atype == "spell":
            key = action.get("key")
            target = action.get("target", "enemy_nearest")
            self._cast_spell(key, target, state)
        elif atype == "move":
            self._move(action.get("direction", ""), state)
        elif atype == "wait":
            self.msleep(500)
        else:
            self.log_event.emit(f"⚠ Action inconnue : {action}", "warn")

    def _cast_spell(self, key, target: str, state: CombatStateSnapshot) -> None:
        """Appuie sur la touche raccourci puis clique précisément sur la cible."""
        if key is None:
            return
        key_str = str(key)
        target_pos = self._resolve_target(target, state)
        if target_pos is None:
            self.log_event.emit(f"  ✗ Pas de cible pour '{target}' — skip", "warn")
            return
        self.log_event.emit(
            f"  → Cast touche {key_str} sur {target} @ {target_pos}", "info",
        )
        try:
            self._input.press_key(key_str)
            self.msleep(int(self._config.key_to_click_delay_sec * 1000))
            self._input.click(target_pos[0], target_pos[1], button="left")
            self._stats.actions_taken += 1
        except Exception as exc:
            self.log_event.emit(f"⚠ Cast échec : {exc}", "error")

    def _move(self, direction: str, state: CombatStateSnapshot) -> None:
        """Déplacement : clic sur une case selon la direction (vers/loin ennemi)."""
        if state.perso is None:
            return
        enemy = state.enemy_nearest()
        px, py = state.perso.x, state.perso.y

        if direction == "toward_enemy" and enemy is not None:
            # Avance de ~1 case (~60px) vers l'ennemi
            vx, vy = enemy.x - px, enemy.y - py
            norm = max((vx ** 2 + vy ** 2) ** 0.5, 1.0)
            tx = int(px + 60 * vx / norm)
            ty = int(py + 60 * vy / norm)
        elif direction == "away_enemy" and enemy is not None:
            vx, vy = px - enemy.x, py - enemy.y
            norm = max((vx ** 2 + vy ** 2) ** 0.5, 1.0)
            tx = int(px + 60 * vx / norm)
            ty = int(py + 60 * vy / norm)
        else:
            return

        self.log_event.emit(f"  → Move vers ({tx},{ty})", "info")
        try:
            self._input.click(tx, ty, button="left")
            self.msleep(400)  # laisse le perso bouger
        except Exception as exc:
            self.log_event.emit(f"⚠ Move échec : {exc}", "error")

    def _resolve_target(
        self,
        target: str,
        state: CombatStateSnapshot,
    ) -> tuple[int, int] | None:
        """Convertit une clé logique en position écran (x, y) absolue.

        Clique sur le **sprite** du mob (au-dessus du cercle), pas sur le cercle
        au sol — sinon le jeu vise la case vide sous le perso/mob.
        """
        def sprite_pos(e) -> tuple[int, int]:
            # fitEllipse donne le centre exact de l'anneau = case du mob en Dofus.
            # On clique directement dessus, aucun offset nécessaire.
            return (e.x, e.y)

        if target in ("self", "me") and state.perso is not None:
            return sprite_pos(state.perso)

        if target in ("enemy_nearest", "nearest", "enemy"):
            e = state.enemy_nearest()
            if e is not None:
                return sprite_pos(e)

        if target == "enemy_weakest":
            e = state.enemy_weakest()
            if e is not None:
                return sprite_pos(e)

        # Fallback : centre du plateau
        if state.perso is not None:
            return (state.perso.x + 80, state.perso.y - 20)

        # Dernier recours : centre absolu de l'écran
        try:
            frame = self._vision.capture()
            h, w = frame.shape[:2]
            region = getattr(self._vision, "last_capture_region", None)
            if region is None:
                return (w // 2, h // 2)
            return (region.x + w // 2, region.y + int(h * 0.45))
        except Exception:
            return None

    def _click_end_turn(self) -> None:
        """Clique sur 'Terminer le tour'.

        Tente d'abord une localisation dynamique du bouton par HSV (précis),
        puis retombe sur le ratio configuré si la détection échoue.
        """
        try:
            frame = self._vision.capture()
            h, w = frame.shape[:2]
            region = getattr(self._vision, "last_capture_region", None)
            offset_x = region.x if region else 0
            offset_y = region.y if region else 0

            # 1. Essai : localisation dynamique du bouton par HSV
            center = self._detector.find_end_turn_button_center(frame)
            if center is not None:
                cx, cy = center[0] + offset_x, center[1] + offset_y
                self.log_event.emit(
                    f"🏁 Fin de tour (HSV) → clic ({cx},{cy})", "info",
                )
                self._input.click(cx, cy, button="left")
                return

            # 2. Fallback : ratio configuré
            ww = region.w if region else w
            hh = region.h if region else h
            cx = offset_x + int(ww * self._config.end_turn_btn_ratio_x)
            cy = offset_y + int(hh * self._config.end_turn_btn_ratio_y)
            self.log_event.emit(
                f"🏁 Fin de tour (ratio fallback) → clic ({cx},{cy})", "info",
            )
            self._input.click(cx, cy, button="left")
        except Exception as exc:
            self.log_event.emit(f"⚠ Échec fin de tour : {exc}", "error")
