"""Entrypoint du bot.

Orchestration :
    1. Parse les arguments CLI.
    2. Charge la configuration (pydantic-settings).
    3. Configure le logger (loguru).
    4. Instancie les services (DI manuelle).
    5. Démarre l'application Qt avec la fenêtre principale.

Lancement :
    python -m src.main            # GUI
    python -m src.main --headless # futur — mode CLI sans GUI
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _redirect_stdout_stderr_if_pythonw() -> None:
    """Sous pythonw.exe, sys.stdout / sys.stderr sont None → redirige vers
    un fichier log pour éviter BrokenPipeError / crash silencieux."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        here = Path(__file__).resolve().parent.parent
        log_dir = here / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / "bot.log"
        # Ouvre en append pour conserver l'historique
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
    except Exception:
        # Dernier recours : DEVNULL pour éviter les crash de print()
        try:
            devnull = open(os.devnull, "w", encoding="utf-8")
            if sys.stdout is None:
                sys.stdout = devnull
            if sys.stderr is None:
                sys.stderr = devnull
        except Exception:
            pass


# IMPORTANT : redirect AVANT d'importer loguru qui veut écrire sur stderr
_redirect_stdout_stderr_if_pythonw()

from loguru import logger  # noqa: E402

from src.config.settings import Settings  # noqa: E402


def _enable_dpi_awareness() -> None:
    """Active la DPI-awareness Windows AVANT tout import Qt/mss/pygetwindow.

    Tente plusieurs APIs dans l'ordre de priorité pour maximiser les chances.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415
        # 1. API la plus récente : SetProcessDpiAwarenessContext (Win10 1703+)
        # PER_MONITOR_AWARE_V2 = -4
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
            return
        except Exception:
            pass
        # 2. SetProcessDpiAwareness (Win 8.1+) — PROCESS_PER_MONITOR_DPI_AWARE = 2
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        # 3. SetProcessDPIAware (Win Vista+) — legacy
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception as exc:  # pragma: no cover
        print(f"DPI awareness non activée : {exc}", file=sys.stderr)


def _log_screen_info() -> None:
    """Log les dimensions écran depuis plusieurs sources pour diagnostic DPI."""
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415
        user32 = ctypes.windll.user32

        # Source 1 : GetSystemMetrics (écran primaire virtuel)
        w1 = user32.GetSystemMetrics(0)
        h1 = user32.GetSystemMetrics(1)

        # Source 2 : GetDeviceCaps HORZRES/VERTRES sur le DC desktop
        try:
            dc = user32.GetDC(0)
            gdi32 = ctypes.windll.gdi32
            # HORZRES = 8, VERTRES = 10
            w2 = gdi32.GetDeviceCaps(dc, 8)
            h2 = gdi32.GetDeviceCaps(dc, 10)
            # DESKTOPHORZRES = 118, DESKTOPVERTRES = 117 (vrais pixels physiques)
            w3 = gdi32.GetDeviceCaps(dc, 118)
            h3 = gdi32.GetDeviceCaps(dc, 117)
            user32.ReleaseDC(0, dc)
        except Exception:
            w2 = h2 = w3 = h3 = 0

        logger.info("🖥  Écran (GetSystemMetrics)         : {}×{}", w1, h1)
        logger.info("🖥  Écran (HORZRES/VERTRES)          : {}×{}", w2, h2)
        logger.info("🖥  Écran (DESKTOPHORZRES — physique) : {}×{}", w3, h3)
        if w1 != w3 and w3 > 0:
            logger.warning(
                "⚠ Différence détectée → DPI awareness probablement PAS active. "
                "Windows applique un scaling (ratio ~{:.2f}).",
                w3 / max(w1, 1),
            )
    except Exception:
        pass


# Doit être appelé le plus tôt possible, avant tout import Qt/mss
_enable_dpi_awareness()


def configure_logging(settings: Settings) -> None:
    """Configure loguru (stdout + rotation fichier)."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "bot_{time:YYYY-MM-DD}.log",
        level=settings.log_level,
        rotation=f"{settings.log_rotation_mb} MB",
        retention=f"{settings.log_retention_days} days",
        compression="zip",
        enqueue=True,
        serialize=False,
    )
    # Structured sink (one JSON per line) — for future dashboards
    logger.add(
        log_dir / "bot_structured_{time:YYYY-MM-DD}.jsonl",
        level="INFO",
        rotation=f"{settings.log_rotation_mb} MB",
        retention=f"{settings.log_retention_days} days",
        serialize=True,
        enqueue=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dofus-bot", description="Dofus 2.64 farming & combat bot")
    parser.add_argument("--headless", action="store_true", help="Run without GUI (not implemented in MVP)")
    parser.add_argument("--config", type=Path, default=None, help="Path to alternative .env file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = Settings(_env_file=str(args.config) if args.config else ".env")  # type: ignore[call-arg]
    configure_logging(settings)
    logger.info("Dofus Bot v{} starting", settings.version)
    logger.debug("Settings: {}", settings.model_dump(exclude={"discord_token"}))
    _log_screen_info()

    # Ensure runtime directories exist
    for directory in (settings.log_dir, settings.screenshots_dir, Path(settings.db_path).parent):
        Path(directory).mkdir(parents=True, exist_ok=True)

    # Check Tesseract au démarrage et lance install auto si absent (pour la FM)
    try:
        from src.services.tesseract_installer import ensure_tesseract_installed  # noqa: PLC0415

        def _tesseract_cb(result: str) -> None:
            if result == "ok":
                logger.info("🎉 Tesseract installé automatiquement (FM disponible)")
            else:
                logger.warning(
                    "⚠ Tesseract n'a pas pu s'installer automatiquement. "
                    "Installe-le via https://github.com/UB-Mannheim/tesseract/wiki"
                )

        status = ensure_tesseract_installed(callback=_tesseract_cb)
        if status == "ok":
            logger.info("✓ Tesseract OCR disponible")
        elif status == "installing":
            logger.info("⏳ Tesseract manquant → installation en arrière-plan (~30s)")
        elif status == "unsupported":
            logger.info("ℹ Tesseract non installable auto (OS non Windows ou winget absent)")
    except Exception as exc:
        logger.debug("Check Tesseract échec : {}", exc)

    if args.headless:
        logger.error("Headless mode not implemented yet — aborting.")
        return 2

    # Lazy imports: Qt is heavy, do not load it in headless mode
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

    from src.handlers.state_machine import BotStateMachine  # noqa: PLC0415
    from src.services.input_service import PyAutoGuiInputService  # noqa: PLC0415
    from src.services.pathfinding import PathfindingService  # noqa: PLC0415
    from src.services.persistence import PersistenceService  # noqa: PLC0415
    from src.services.vision import MssVisionService  # noqa: PLC0415
    from src.ui.app import build_app  # noqa: PLC0415

    persistence = PersistenceService(db_path=settings.db_path)
    persistence.initialize()

    input_svc = PyAutoGuiInputService(humanize=settings.humanize_clicks)
    vision = MssVisionService(
        tesseract_path=settings.tesseract_path,
        lang=settings.tesseract_lang,
        tessdata_dir=settings.tessdata_dir,
        window_title=settings.dofus_window_title,
        input_svc=input_svc,
    )
    pathfinder = PathfindingService.load_from_json(settings.maps_graph_path)

    state_machine = BotStateMachine(
        vision=vision,
        input_svc=input_svc,
        pathfinder=pathfinder,
        persistence=persistence,
        settings=settings,
    )

    app = QApplication(sys.argv)

    from src.ui.styles import DARK_QSS  # noqa: PLC0415

    app.setStyleSheet(DARK_QSS)

    window = build_app(
        state_machine=state_machine,
        persistence=persistence,
        settings=settings,
        vision=vision,
    )
    window.show()
    exit_code = app.exec()
    logger.info("Application exited with code {}", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
