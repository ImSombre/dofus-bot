"""Système de macros : enregistrement et exécution de séquences de clics/actions.

Usage type (fin de DJ → rejoindre groupe) :
    macro = Macro(name="rejoindre_dj", steps=[
        ClickStep(x=1250, y=800, button="right", delay_ms_after=600),  # clic droit NPC
        ClickStep(x=1280, y=850, delay_ms_after=800),                  # option "Discuter"
        ClickStep(x=1000, y=700, delay_ms_after=500),                  # option "Rejoindre le groupe"
        WaitStep(duration_ms=2000),                                     # attendre loading
    ])

    player = MacroPlayer(input_svc)
    player.play(macro)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Union

from loguru import logger

from src.services.input_service import InputService


StepType = Literal["click", "key", "wait"]


@dataclass
class ClickStep:
    """Clic souris à une position absolue écran."""
    x: int
    y: int
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False
    delay_ms_after: int = 500
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "click",
            "x": self.x, "y": self.y, "button": self.button,
            "double": self.double,
            "delay_ms_after": self.delay_ms_after,
            "description": self.description,
        }


@dataclass
class KeyStep:
    """Appui clavier (ex: 'enter', 'escape', 'space', 'a'…)."""
    key: str
    delay_ms_after: int = 300
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "key",
            "key": self.key,
            "delay_ms_after": self.delay_ms_after,
            "description": self.description,
        }


@dataclass
class WaitStep:
    """Pause fixe (en ms) — utile pour attendre un chargement."""
    duration_ms: int
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "wait",
            "duration_ms": self.duration_ms,
            "description": self.description,
        }


Step = Union[ClickStep, KeyStep, WaitStep]


def step_from_dict(d: dict) -> Step | None:
    t = d.get("type")
    if t == "click":
        return ClickStep(
            x=int(d["x"]), y=int(d["y"]),
            button=d.get("button", "left"),
            double=bool(d.get("double", False)),
            delay_ms_after=int(d.get("delay_ms_after", 500)),
            description=d.get("description", ""),
        )
    if t == "key":
        return KeyStep(
            key=str(d["key"]),
            delay_ms_after=int(d.get("delay_ms_after", 300)),
            description=d.get("description", ""),
        )
    if t == "wait":
        return WaitStep(
            duration_ms=int(d["duration_ms"]),
            description=d.get("description", ""),
        )
    return None


@dataclass
class Macro:
    """Séquence d'actions nommée."""
    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Macro":
        steps: list[Step] = []
        for sd in d.get("steps", []):
            s = step_from_dict(sd)
            if s is not None:
                steps.append(s)
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            steps=steps,
        )


class MacroPlayer:
    """Exécute une Macro step par step via InputService."""

    def __init__(
        self,
        input_svc: InputService,
        log_callback=None,
        stop_flag=None,  # callable qui retourne True pour arrêter
    ) -> None:
        self._input = input_svc
        self._log_cb = log_callback
        self._stop_flag = stop_flag

    def _log(self, msg: str, level: str = "info") -> None:
        if level == "error":
            logger.error(msg)
        elif level == "warn":
            logger.warning(msg)
        else:
            logger.info(msg)
        if self._log_cb is not None:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass

    def _should_stop(self) -> bool:
        if self._stop_flag is None:
            return False
        try:
            return bool(self._stop_flag())
        except Exception:
            return False

    def play(self, macro: Macro) -> bool:
        """Exécute la macro complète. Retourne False si interrompu."""
        self._log(f"▶ Macro '{macro.name}' : {len(macro.steps)} étape(s)", "info")
        for i, step in enumerate(macro.steps, 1):
            if self._should_stop():
                self._log(f"⏹ Macro interrompue au step {i}", "warn")
                return False
            try:
                self._execute_step(i, step)
            except Exception as exc:
                self._log(f"⚠ Step {i} a raté : {exc}", "error")
                return False
        self._log(f"✓ Macro '{macro.name}' terminée", "info")
        return True

    def _execute_step(self, index: int, step: Step) -> None:
        desc = step.description or ""
        if isinstance(step, ClickStep):
            desc_suffix = f" — {desc}" if desc else ""
            self._log(
                f"  step {index} : clic {step.button}{' x2' if step.double else ''} "
                f"({step.x},{step.y}){desc_suffix}",
            )
            if step.double and hasattr(self._input, "double_click"):
                self._input.double_click(step.x, step.y, button=step.button, jitter=False)
            else:
                self._input.click(step.x, step.y, button=step.button, jitter=False)
            self._sleep_ms(step.delay_ms_after)
        elif isinstance(step, KeyStep):
            desc_suffix = f" — {desc}" if desc else ""
            self._log(f"  step {index} : touche '{step.key}'{desc_suffix}")
            self._input.press_key(step.key)
            self._sleep_ms(step.delay_ms_after)
        elif isinstance(step, WaitStep):
            desc_suffix = f" — {desc}" if desc else ""
            self._log(f"  step {index} : attente {step.duration_ms} ms{desc_suffix}")
            self._sleep_ms(step.duration_ms)

    def _sleep_ms(self, ms: int) -> None:
        """Sleep découpé pour respecter le stop_flag."""
        slice_ms = 100
        elapsed = 0
        while elapsed < ms and not self._should_stop():
            step = min(slice_ms, ms - elapsed)
            time.sleep(step / 1000)
            elapsed += step
