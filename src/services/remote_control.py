"""Discord remote control (optional).

Activates only if `DISCORD_ENABLED=true` in .env. Runs in its own asyncio loop,
communicating with the bot thread via a thread-safe queue.

Supported slash commands (MVP):
    /start <mode> <job_or_zone>
    /stop
    /pause
    /resume
    /status
    /stop_loss <xp_per_hour>
    /screenshot
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger


@dataclass(slots=True)
class Command:
    """A command emitted by Discord and consumed by the bot."""

    name: str
    user_id: int
    args: dict[str, Any] = field(default_factory=dict)


class RemoteControl(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def notify(self, message: str, screenshot_path: str | None = None) -> None: ...
    def pull_commands(self) -> list[Command]: ...


class DiscordControl:
    """discord.py-based remote control.

    Lives in a dedicated thread running its own asyncio event loop.
    """

    def __init__(
        self,
        token: str,
        guild_id: int | None,
        allowed_user_ids: set[int],
        rate_limit_per_minute: int = 10,
    ) -> None:
        self._token = token
        self._guild_id = guild_id
        self._allowed_user_ids = allowed_user_ids
        self._rate_limit = rate_limit_per_minute
        self._command_queue: queue.Queue[Command] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        logger.info("Starting Discord remote control (guild={})", self._guild_id)
        self._running = True
        # Actual implementation: spin a thread running asyncio.run(main_bot())
        # with discord.Client subclass defining slash commands.
        # Left as NotImplemented to keep the skeleton compilable.
        raise NotImplementedError(
            "Implement me: spawn thread with asyncio loop, register discord.app_commands, "
            "enqueue Command() into self._command_queue on each valid invocation."
        )

    def stop(self) -> None:
        self._running = False
        logger.info("Discord remote control stopped")

    def notify(self, message: str, screenshot_path: str | None = None) -> None:
        raise NotImplementedError("Implement me: send message (+ attach file) to configured channel.")

    def pull_commands(self) -> list[Command]:
        """Drain the queue (non-blocking). Called from the bot tick."""
        commands: list[Command] = []
        while True:
            try:
                commands.append(self._command_queue.get_nowait())
            except queue.Empty:
                break
        return commands
