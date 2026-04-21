"""Tracking des stats détaillées d'une session combat.

Enregistre :
  - Nb combats démarrés / gagnés / perdus / échappés
  - Nb casts de sort (par slot)
  - Nb kills (approximé par mobs qui disparaissent entre 2 scans)
  - Durée moyenne/max par combat
  - Latence moyenne LLM
  - Répartition des décisions : moteur règles vs LLM

Persisté dans data/combat_stats.json (append-safe).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


STATS_FILE = Path("data/combat_stats.json")


@dataclass
class CombatSession:
    """Statistiques d'un seul combat."""
    started_at: str = ""
    ended_at: str = ""
    duration_sec: float = 0.0
    outcome: str = ""  # "victory" | "defeat" | "escape" | "ongoing"
    class_name: str = ""
    casts_by_slot: dict[str, int] = field(default_factory=dict)
    kills_estimated: int = 0
    turns_played: int = 0
    rule_decisions: int = 0
    llm_decisions: int = 0
    llm_latencies: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def avg_llm_latency(self) -> float:
        return sum(self.llm_latencies) / len(self.llm_latencies) if self.llm_latencies else 0.0


@dataclass
class GlobalStats:
    """Aggrégats de toutes les sessions."""
    total_combats: int = 0
    total_victories: int = 0
    total_defeats: int = 0
    total_escapes: int = 0
    total_kills: int = 0
    total_turns: int = 0
    total_rule_decisions: int = 0
    total_llm_decisions: int = 0
    sum_duration_sec: float = 0.0
    sum_llm_latency_sec: float = 0.0

    def win_rate(self) -> float:
        if self.total_combats == 0:
            return 0.0
        return 100.0 * self.total_victories / self.total_combats

    def avg_combat_duration(self) -> float:
        if self.total_combats == 0:
            return 0.0
        return self.sum_duration_sec / self.total_combats

    def avg_llm_latency(self) -> float:
        if self.total_llm_decisions == 0:
            return 0.0
        return self.sum_llm_latency_sec / self.total_llm_decisions

    def llm_ratio(self) -> float:
        """% de décisions qui ont nécessité un LLM (indicateur d'efficacité moteur)."""
        total = self.total_rule_decisions + self.total_llm_decisions
        if total == 0:
            return 0.0
        return 100.0 * self.total_llm_decisions / total


class CombatStatsTracker:
    """Tracker singleton de stats combat."""

    def __init__(self, persistence_path: Path | str | None = None) -> None:
        self._path = Path(persistence_path) if persistence_path else STATS_FILE
        self._current: CombatSession | None = None
        self._global = GlobalStats()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if "global" in data:
                for k, v in data["global"].items():
                    if hasattr(self._global, k):
                        setattr(self._global, k, v)
            logger.info(
                "Stats combat chargées : {} combats, {:.1f}% win",
                self._global.total_combats, self._global.win_rate(),
            )
        except Exception as exc:
            logger.debug("Chargement stats échec : {}", exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"global": asdict(self._global)}, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug("Sauvegarde stats échec : {}", exc)

    # ---------- API ----------

    def on_combat_start(self, class_name: str) -> None:
        """Début d'un combat : reset le tracker courant."""
        self._current = CombatSession(
            started_at=datetime.now().isoformat(timespec="seconds"),
            class_name=class_name,
        )

    def on_cast(self, slot: str) -> None:
        if self._current is None:
            return
        self._current.casts_by_slot[str(slot)] = self._current.casts_by_slot.get(str(slot), 0) + 1

    def on_turn(self) -> None:
        if self._current is None:
            return
        self._current.turns_played += 1

    def on_decision(self, source: str, latency_ms: float = 0.0) -> None:
        """source = 'rules' | 'llm'"""
        if self._current is None:
            return
        if source == "llm":
            self._current.llm_decisions += 1
            self._current.llm_latencies.append(latency_ms)
        else:
            self._current.rule_decisions += 1

    def on_kill(self) -> None:
        if self._current is None:
            return
        self._current.kills_estimated += 1

    def on_combat_end(self, outcome: str) -> dict[str, Any]:
        """Finalise le combat courant, met à jour les aggrégats globaux."""
        if self._current is None:
            return {}

        self._current.ended_at = datetime.now().isoformat(timespec="seconds")
        try:
            started = datetime.fromisoformat(self._current.started_at)
            ended = datetime.fromisoformat(self._current.ended_at)
            self._current.duration_sec = (ended - started).total_seconds()
        except Exception:
            self._current.duration_sec = 0.0
        self._current.outcome = outcome

        # Update globals
        self._global.total_combats += 1
        if outcome == "victory":
            self._global.total_victories += 1
        elif outcome == "defeat":
            self._global.total_defeats += 1
        elif outcome == "escape":
            self._global.total_escapes += 1
        self._global.total_kills += self._current.kills_estimated
        self._global.total_turns += self._current.turns_played
        self._global.total_rule_decisions += self._current.rule_decisions
        self._global.total_llm_decisions += self._current.llm_decisions
        self._global.sum_duration_sec += self._current.duration_sec
        self._global.sum_llm_latency_sec += sum(self._current.llm_latencies) / 1000.0  # ms→s

        snapshot = asdict(self._current)
        self._current = None
        self._save()
        return snapshot

    def get_global_stats(self) -> GlobalStats:
        return self._global

    def format_summary(self) -> str:
        g = self._global
        return (
            f"📊 Stats combat : {g.total_combats} combats, "
            f"{g.win_rate():.0f}% win, "
            f"~{g.avg_combat_duration():.0f}s/combat, "
            f"moteur règles={100 - g.llm_ratio():.0f}% des décisions, "
            f"LLM latence={g.avg_llm_latency():.1f}s"
        )


# Instance globale partagée
_TRACKER: CombatStatsTracker | None = None


def get_tracker() -> CombatStatsTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = CombatStatsTracker()
    return _TRACKER
