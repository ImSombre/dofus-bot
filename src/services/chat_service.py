"""Envoi de commandes dans le chat Dofus.

Flow type :
    chat = ChatService(input_svc)
    chat.send_command(".zaap", click_at=(960, 540))   # ouvre le menu zaap

Flow pratique sur Dofus 2.x (serveur privé user) :
    1. Clic gauche sur le jeu → assure le focus clavier
    2. Touche Espace → ouvre le chat (bind user)
    3. Tape la commande
    4. Entrée → envoie + ferme le chat
"""
from __future__ import annotations

import time

from loguru import logger

from src.services.input_service import InputService


class ChatService:
    """Envoie des commandes au chat Dofus.

    La touche d'ouverture du chat dépend des binds user : Espace ou Entrée.
    On propose d'abord un clic sur le jeu pour garantir le focus clavier, puis
    on appuie sur `chat_open_key`, on tape, et on valide avec Entrée.
    """

    def __init__(
        self,
        input_svc: InputService,
        chat_open_key: str = "space",      # user confirmé : Espace ouvre le chat
        wait_after_click_ms: int = 200,
        wait_after_open_ms: int = 300,
        wait_before_send_ms: int = 150,
    ) -> None:
        self._input = input_svc
        self._chat_key = chat_open_key
        self._wait_after_click_ms = wait_after_click_ms
        self._wait_after_open_ms = wait_after_open_ms
        self._wait_before_send_ms = wait_before_send_ms

    def send_command(
        self,
        text: str,
        click_at: tuple[int, int] | None = None,
    ) -> None:
        """Envoie une commande (ex: `.zaap`).

        Args:
            text: la commande à envoyer (avec le préfixe `.` ou `/` si nécessaire)
            click_at: (x, y) écran où cliquer avant d'ouvrir le chat.
                      Recommandé : centre de la fenêtre Dofus sur une zone vide.
                      Si None : pas de clic préalable (suppose focus déjà acquis).
        """
        logger.info("ChatService : envoi '{}' (click_at={})", text, click_at)

        # 1. Clic sur le jeu pour garantir le focus clavier
        if click_at is not None:
            try:
                self._input.click(int(click_at[0]), int(click_at[1]), button="left")
            except Exception as exc:
                logger.warning("ChatService : clic focus échoué — {}", exc)
            time.sleep(self._wait_after_click_ms / 1000)

        # 2. Ouvre le chat (Espace chez l'user, Entrée standard)
        self._input.press_key(self._chat_key)
        time.sleep(self._wait_after_open_ms / 1000)

        # 3. Tape le texte
        self._input.type_text(text)
        time.sleep(self._wait_before_send_ms / 1000)

        # 4. Envoie (Entrée)
        self._input.press_key("enter")
