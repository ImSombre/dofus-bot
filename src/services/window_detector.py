"""Détection intelligente de la fenêtre Dofus parmi toutes les fenêtres ouvertes.

Objectif : scanner toutes les fenêtres, scorer chacune selon :
  - match du titre (regex "Dofus", version)
  - taille plausible (ratio 16:9 ou 4:3, minimum 800x600)
  - pixels signature (couleur de fond sombre typique Dofus)

Retourne une liste triée par score. L'UI affichera un sélecteur si plusieurs
candidats dépassent un seuil.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from loguru import logger


@dataclass
class DofusWindow:
    title: str
    handle: int = 0
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    is_active: bool = False

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height > 0 else 0.0

    @property
    def resume(self) -> str:
        return f"{self.title} — {self.width}x{self.height} (score {self.score:.1f})"

    def focus(self) -> bool:
        """Met cette fenêtre au premier plan. Retourne True si succès."""
        try:
            import pygetwindow as gw
            matches = gw.getWindowsWithTitle(self.title)
            if not matches:
                return False
            w = matches[0]
            if w.isMinimized:
                w.restore()
            try:
                w.activate()
            except Exception:
                # Windows refuse parfois l'activation sans input user récent — fallback : click
                import pyautogui
                cx = self.left + self.width // 2
                cy = self.top + 20  # clic sur la barre de titre
                pyautogui.click(cx, cy)
            return True
        except Exception as exc:
            logger.debug("Échec focus fenêtre '{}': {}", self.title, exc)
            return False


class SmartWindowDetector:
    """Détecte et score les fenêtres Dofus candidates."""

    # Regex sur le titre : Dofus 2.x, Dofus - Pseudo, etc.
    RE_DOFUS_TITLE = re.compile(r"\bDofus\b", re.IGNORECASE)
    RE_DOFUS_VERSION = re.compile(r"Dofus\s*(?:2\.|v)", re.IGNORECASE)

    # Titres à exclure absolument (notre propre bot, dev tools, etc.)
    # Regex insensible à la casse — toute fenêtre matchant l'un de ces patterns est ignorée.
    EXCLUDED_PATTERNS = [
        re.compile(r"^Dofus Bot", re.IGNORECASE),              # notre propre app
        re.compile(r"Dofus Bot —", re.IGNORECASE),
        re.compile(r"dofus-bot", re.IGNORECASE),                # onglet VSCode / navigateur
        re.compile(r"install\.ps1", re.IGNORECASE),
    ]

    # Contraintes taille
    MIN_WIDTH = 800
    MIN_HEIGHT = 600
    # Ratios acceptés ± 0.1
    RATIOS_OK = (16 / 9, 16 / 10, 4 / 3, 21 / 9)

    # Seuil pour considérer une fenêtre comme candidate fiable
    SCORE_CONFIDENT = 60.0

    def __init__(
        self,
        configured_title: str | None = None,
        extra_excluded: list[str] | None = None,
    ) -> None:
        self._configured_title = (configured_title or "").strip() or None
        self._extra_excluded = [re.compile(p, re.IGNORECASE) for p in (extra_excluded or [])]

    def _is_excluded(self, title: str) -> bool:
        patterns = list(self.EXCLUDED_PATTERNS) + self._extra_excluded
        return any(p.search(title) for p in patterns)

    def scan(self) -> list[DofusWindow]:
        """Retourne toutes les fenêtres candidates triées par score descendant."""
        try:
            import pygetwindow as gw
        except ImportError:
            logger.warning("pygetwindow non installé — aucune détection possible")
            return []

        results: list[DofusWindow] = []
        try:
            all_windows = gw.getAllWindows()
        except Exception as exc:
            logger.warning("Échec de l'énumération des fenêtres : {}", exc)
            return []

        for w in all_windows:
            try:
                title = getattr(w, "title", "") or ""
                if not title.strip():
                    continue
                if self._is_excluded(title):
                    continue
                if not getattr(w, "visible", True):
                    continue
                width = getattr(w, "width", 0)
                height = getattr(w, "height", 0)
                if width <= 0 or height <= 0:
                    continue

                dw = DofusWindow(
                    title=title,
                    left=getattr(w, "left", 0),
                    top=getattr(w, "top", 0),
                    width=width,
                    height=height,
                    is_active=getattr(w, "isActive", False),
                )
                dw.score, dw.reasons = self._score(dw)
                if dw.score > 0:
                    results.append(dw)
            except Exception:
                continue

        results.sort(key=lambda d: d.score, reverse=True)
        return results

    def _score(self, w: DofusWindow) -> tuple[float, list[str]]:
        """Score une fenêtre et retourne (score, raisons).

        Score max théorique = 100.
        Composantes :
          - titre Dofus                : +50 (base) +10 si version match
          - titre configuré exact      : +20
          - taille >= min              : +10
          - ratio plausible (±0.1)     : +10
        """
        score = 0.0
        reasons: list[str] = []

        title_lower = w.title.lower()
        title_match = bool(self.RE_DOFUS_TITLE.search(w.title))
        if title_match:
            score += 50
            reasons.append("titre contient « Dofus »")
            if self.RE_DOFUS_VERSION.search(w.title):
                score += 10
                reasons.append("version 2.x détectée")
        elif self._configured_title and self._configured_title.lower() in title_lower:
            # Cas d'un titre personnalisé qui ne contient pas le mot Dofus
            score += 40
            reasons.append("correspond au titre configuré")
        else:
            return 0.0, []

        # Bonus si match exact sur le titre configuré
        if self._configured_title and self._configured_title.lower() in title_lower:
            score += 20
            reasons.append("titre configuré présent")

        # Taille
        if w.width >= self.MIN_WIDTH and w.height >= self.MIN_HEIGHT:
            score += 10
            reasons.append(f"taille OK ({w.width}x{w.height})")
        else:
            reasons.append(f"taille faible ({w.width}x{w.height})")

        # Ratio
        if w.height > 0 and any(abs(w.ratio - r) < 0.1 for r in self.RATIOS_OK):
            score += 10
            reasons.append(f"ratio OK ({w.ratio:.2f})")

        return score, reasons

    def best(self) -> DofusWindow | None:
        """Retourne la meilleure fenêtre détectée ou None."""
        candidates = self.scan()
        return candidates[0] if candidates else None

    def confident_candidates(self) -> list[DofusWindow]:
        """Retourne uniquement les fenêtres au-dessus du seuil de confiance."""
        return [w for w in self.scan() if w.score >= self.SCORE_CONFIDENT]


def format_window_list(windows: Iterable[DofusWindow]) -> str:
    """Rend une liste de fenêtres lisible pour les logs."""
    lines = []
    for i, w in enumerate(windows, 1):
        lines.append(f"  {i}. {w.resume}")
        for r in w.reasons:
            lines.append(f"       • {r}")
    return "\n".join(lines) if lines else "  (aucune fenêtre détectée)"
