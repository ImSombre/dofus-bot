"""Client LLM générique — supporte Ollama ET LM Studio (API OpenAI-compatible).

Usage :
    # Ollama (format /api/generate)
    client = LLMClient(provider="ollama", model="gemma3:12b")

    # LM Studio (format /v1/chat/completions, compatible OpenAI)
    client = LLMClient(provider="lmstudio", model="google/gemma-3-12b",
                       base_url="http://localhost:1234/v1")

    # Appel avec image (modèle vision)
    text = client.ask_with_image(system, user, image_bgr)
    data = client.ask_json(system, user, image_bgr=image_bgr)

Les deux providers acceptent les images base64 (format multimodal).
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Literal

from loguru import logger

try:
    import cv2  # noqa: F401
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

try:
    import requests  # noqa: F401
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


ProviderType = Literal["ollama", "lmstudio", "gemini", "anthropic"]


@dataclass
class LLMResponse:
    success: bool
    text: str = ""
    error: str = ""
    latency_ms: float = 0.0
    # Scale factor appliqué à l'image envoyée au LLM.
    # Ex: si écran 2560×1440 et image envoyée à 1280×720 → image_scale = 0.5
    # Pour retrouver les coords écran : coord_ecran = coord_llm / image_scale
    image_scale: float = 1.0
    image_width: int = 0
    image_height: int = 0


class LLMClient:
    """Client unifié Ollama / LM Studio (OpenAI-compatible)."""

    PROVIDERS: dict[str, str] = {
        "ollama": "http://localhost:11434",
        "lmstudio": "http://localhost:1234/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "anthropic": "https://api.anthropic.com/v1",
    }

    def __init__(
        self,
        provider: ProviderType = "ollama",
        model: str = "qwen2.5vl:3b",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 500,
        timeout_sec: float = 60.0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.base_url = (base_url or self.PROVIDERS.get(provider, "")).rstrip("/")
        self.api_key = api_key or ""
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec

    # ---------- Disponibilité ----------

    def is_available(self) -> bool:
        if not _HAS_REQUESTS:
            return False
        try:
            import requests  # noqa: PLC0415
            if self.provider in ("gemini", "anthropic"):
                # Si on a une clé API, on considère disponible (pas de ping gratuit)
                return bool(self.api_key)
            url = f"{self.base_url}/api/tags" if self.provider == "ollama" \
                else f"{self.base_url}/models"
            r = requests.get(url, timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        if not _HAS_REQUESTS:
            return []
        try:
            import requests  # noqa: PLC0415
            if self.provider == "ollama":
                r = requests.get(f"{self.base_url}/api/tags", timeout=3.0)
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
            elif self.provider == "gemini":
                if not self.api_key:
                    return []
                r = requests.get(
                    f"{self.base_url}/models?key={self.api_key}",
                    timeout=5.0,
                )
                if r.status_code != 200:
                    return []
                data = r.json()
                # Filtre : seulement les modèles qui supportent generateContent
                names = []
                for m in data.get("models", []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        # Strip "models/" prefix
                        n = m.get("name", "").replace("models/", "")
                        names.append(n)
                return names
            elif self.provider == "anthropic":
                # Anthropic n'a pas d'endpoint list-models public. Liste statique.
                return [
                    "claude-haiku-4-5-20251001",
                    "claude-sonnet-4-6",
                    "claude-sonnet-4-5-20250929",
                    "claude-3-5-haiku-20241022",
                ]
            else:
                r = requests.get(f"{self.base_url}/models", timeout=3.0)
                data = r.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception as exc:
            logger.debug("list_models échec : {}", exc)
            return []

    def has_model(self) -> bool:
        models = self.list_models()
        # Matching souple : on accepte prefix/suffix
        return any(self.model in m or m in self.model for m in models) if models else False

    # ---------- Ask ----------

    def ask(
        self,
        user_prompt: str,
        system: str | None = None,
        image_bgr=None,
    ) -> LLMResponse:
        """Appel générique. Route vers Ollama ou LM Studio selon provider."""
        if not _HAS_REQUESTS:
            return LLMResponse(success=False, error="requests non installé")

        t0 = time.perf_counter()
        try:
            if self.provider == "ollama":
                response = self._ask_ollama(user_prompt, system, image_bgr)
            elif self.provider == "gemini":
                response = self._ask_gemini(user_prompt, system, image_bgr)
            elif self.provider == "anthropic":
                response = self._ask_anthropic(user_prompt, system, image_bgr)
            else:
                response = self._ask_openai(user_prompt, system, image_bgr)
            response.latency_ms = (time.perf_counter() - t0) * 1000
            return response
        except Exception as exc:
            return LLMResponse(
                success=False,
                error=str(exc),
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    def ask_json(
        self,
        user_prompt: str,
        system: str | None = None,
        image_bgr=None,
        fallback: dict | None = None,
    ) -> dict:
        """Extrait un JSON de la réponse. Injecte `_image_scale` si image fournie.

        Le dict retourné contient des métadonnées techniques (clés commençant par `_`) :
          - `_image_scale` : facteur de redimensionnement appliqué à l'image (1.0 si pas)
          - `_image_width`, `_image_height` : taille réelle envoyée au LLM
        Le caller peut diviser ses coords par `_image_scale` pour retrouver les pixels écran.
        """
        response = self.ask(user_prompt, system=system, image_bgr=image_bgr)
        if not response.success:
            logger.warning("LLM ask échec : {}", response.error)
            out = dict(fallback or {})
            out["_error"] = response.error or "Erreur LLM inconnue"
            out["_raw_text"] = response.text or ""
            out["_image_scale"] = response.image_scale
            out["_image_width"] = response.image_width
            out["_image_height"] = response.image_height
            return out
        parsed = self._extract_json(response.text)
        if parsed is None:
            parsed = dict(fallback or {})
        # Toujours garder la réponse brute pour debug (utile pour diagnostiquer
        # les parsing ambigus, les JSON incomplets, etc.)
        parsed["_raw_text"] = (response.text or "")[:500]
        parsed["_image_scale"] = response.image_scale
        parsed["_image_width"] = response.image_width
        parsed["_image_height"] = response.image_height
        return parsed

    # ---------- Internals ----------

    def _ask_ollama(
        self,
        user_prompt: str,
        system: str | None,
        image_bgr,
    ) -> LLMResponse:
        import requests  # noqa: PLC0415
        payload: dict = {
            "model": self.model,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if system:
            payload["system"] = system
        scale, img_w, img_h = 1.0, 0, 0
        if image_bgr is not None:
            b64, scale, img_w, img_h = self._encode_image_b64(image_bgr)
            if b64:
                payload["images"] = [b64]

        r = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout_sec,
        )
        if r.status_code != 200:
            return LLMResponse(
                success=False,
                error=f"HTTP {r.status_code}: {r.text[:200]}",
                image_scale=scale, image_width=img_w, image_height=img_h,
            )
        data = r.json()
        return LLMResponse(
            success=True, text=data.get("response", "").strip(),
            image_scale=scale, image_width=img_w, image_height=img_h,
        )

    def _ask_openai(
        self,
        user_prompt: str,
        system: str | None,
        image_bgr,
    ) -> LLMResponse:
        """Format OpenAI chat/completions — utilisé par LM Studio, vLLM, etc."""
        import requests  # noqa: PLC0415

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})

        scale, img_w, img_h = 1.0, 0, 0
        if image_bgr is not None:
            b64, scale, img_w, img_h = self._encode_image_b64(image_bgr)
            if b64:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                })
            else:
                messages.append({"role": "user", "content": user_prompt})
        else:
            messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        r = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=self.timeout_sec,
        )
        if r.status_code != 200:
            return LLMResponse(
                success=False,
                error=f"HTTP {r.status_code}: {r.text[:200]}",
                image_scale=scale, image_width=img_w, image_height=img_h,
            )
        data = r.json()
        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(
                success=False, error="Pas de réponse",
                image_scale=scale, image_width=img_w, image_height=img_h,
            )
        text = choices[0].get("message", {}).get("content", "").strip()
        return LLMResponse(
            success=True, text=text,
            image_scale=scale, image_width=img_w, image_height=img_h,
        )

    # Modèles Anthropic vision avec fallback (vitesse > coût > qualité).
    # Tarifs 2026 (par 1M tokens) :
    #   - haiku-4-5 : $1 input / $5 output — ultra rapide, recommandé combat
    #   - sonnet-4-6 : $3 input / $15 output — top qualité
    _ANTHROPIC_FALLBACK_CHAIN = [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-3-5-haiku-20241022",
    ]

    def _ask_anthropic(
        self,
        user_prompt: str,
        system: str | None,
        image_bgr,
    ) -> LLMResponse:
        """Anthropic Claude Messages API avec vision + retry + fallback.

        Doc : https://docs.claude.com/en/api/messages
        Tarifs : https://claude.com/pricing — Haiku 4.5 ~$1/1M input.
        """
        import requests  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        if not self.api_key:
            return LLMResponse(
                success=False,
                error="Clé API Anthropic manquante — obtiens-la sur https://console.anthropic.com/settings/keys",
            )

        scale, img_w, img_h = 1.0, 0, 0
        content: list[dict] = []
        if image_bgr is not None:
            b64, scale, img_w, img_h = self._encode_image_b64(image_bgr)
            if b64:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                })
        content.append({"type": "text", "text": user_prompt})

        # Prefill : on commence la réponse assistant par "{" pour forcer Claude
        # à continuer en JSON. C'est la méthode officielle Anthropic pour garantir
        # une sortie JSON (pas de responseMimeType comme Gemini).
        payload: dict = {
            "model": self.model,
            "max_tokens": max(self.max_tokens, 1024),
            "temperature": self.temperature,
            "messages": [
                {"role": "user", "content": content},
                {"role": "assistant", "content": "{"},
            ],
        }
        # Prompt caching Anthropic (v1.2.0) : le system prompt est identique
        # d'un appel à l'autre, on le marque cache_control=ephemeral pour que
        # Anthropic le garde en cache 5 min → économie 90% sur coût input.
        # Doc : https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
        if system:
            payload["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                },
            ]

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            # Active le prompt caching (GA depuis late-2024, pas besoin du beta
            # header récent, mais on le met par sécurité pour les anciennes régions).
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

        models_to_try = [self.model]
        for fb in self._ANTHROPIC_FALLBACK_CHAIN:
            if fb != self.model and fb not in models_to_try:
                models_to_try.append(fb)

        last_error = ""
        for model in models_to_try:
            payload["model"] = model
            for retry in range(2):
                try:
                    r = requests.post(
                        f"{self.base_url}/messages",
                        headers=headers,
                        json=payload,
                        timeout=self.timeout_sec,
                    )
                except requests.exceptions.Timeout:
                    last_error = f"{model}: timeout {self.timeout_sec}s"
                    logger.warning("Anthropic timeout {} (retry {}/2)", model, retry + 1)
                    _time.sleep(0.5 + retry * 1.5)
                    continue
                except Exception as exc:
                    last_error = f"{model}: {exc}"
                    logger.warning("Anthropic erreur {} : {}", model, exc)
                    break

                if r.status_code == 200:
                    data = r.json()
                    blocks = data.get("content", [])
                    text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                    text = "\n".join(t for t in text_parts if t).strip()
                    if not text:
                        last_error = f"{model}: réponse vide"
                        break
                    # Le prefill "{" n'est PAS répété dans la réponse : on le re-préfixe
                    # pour que _extract_json trouve un JSON complet.
                    if not text.startswith("{"):
                        text = "{" + text
                    if model != self.model:
                        logger.info("Anthropic fallback réussi sur '{}' (demandé : {})", model, self.model)
                    return LLMResponse(
                        success=True, text=text,
                        image_scale=scale, image_width=img_w, image_height=img_h,
                    )

                if r.status_code in (429, 529, 503):
                    wait = 0.5 + retry * 1.5
                    last_error = f"{model}: HTTP {r.status_code}"
                    logger.warning(
                        "Anthropic {} sur {} — attente {:.1f}s (retry {}/2)",
                        r.status_code, model, wait, retry + 1,
                    )
                    _time.sleep(wait)
                    continue

                last_error = f"{model}: HTTP {r.status_code}: {r.text[:200]}"
                logger.warning("Anthropic HTTP {} sur {} : {}", r.status_code, model, r.text[:200])
                break

        return LLMResponse(
            success=False, error=f"Tous les modèles Anthropic ont échoué. Dernier : {last_error}",
            image_scale=scale, image_width=img_w, image_height=img_h,
        )

    # Liste des modèles Gemini à essayer en fallback si le primaire est surchargé (503)
    # ou timeout. Ordre : plus récent/rapide → plus stable → moins sollicité.
    # gemini-2.0-flash retiré (404 no longer available).
    # v0.3.3 : flash > flash-lite pour le raisonnement spatial Dofus
    # (flash-lite hallucinait les positions des mobs et re-castait en boucle).
    _GEMINI_FALLBACK_CHAIN = [
        "gemini-2.5-flash",        # équilibre vitesse/spatial (défaut)
        "gemini-flash-latest",
        "gemini-2.5-flash-lite",   # fallback si flash surchargé
        "gemini-1.5-flash",        # legacy stable
        "gemini-2.5-pro",          # dernier recours (quota payant plus cher)
    ]

    def _ask_gemini(
        self,
        user_prompt: str,
        system: str | None,
        image_bgr,
    ) -> LLMResponse:
        """Google Gemini API avec retry sur 503/timeout + fallback modèle.

        Doc : https://ai.google.dev/api/generate-content
        Quota gratuit généreux. Retry automatique si serveur surchargé.
        """
        import requests  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        if not self.api_key:
            return LLMResponse(
                success=False,
                error="Clé API Gemini manquante — obtiens-la sur https://aistudio.google.com/app/apikey",
            )

        # Structure des "parts" : texte + image(s)
        parts: list[dict] = []
        img_scale, img_w, img_h = 1.0, 0, 0
        if image_bgr is not None:
            b64, img_scale, img_w, img_h = self._encode_image_b64(image_bgr)
            if b64:
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64,
                    },
                })
        parts.append({"text": user_prompt})

        payload: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
                "responseMimeType": "application/json",
                # Désactive le "thinking" interne de Gemini 2.5 (consomme les tokens
                # avant même d'écrire la réponse visible → JSON tronqué).
                "thinkingConfig": {
                    "thinkingBudget": 0,
                },
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        # Construit la chaîne de modèles à essayer : user's choice d'abord, puis fallbacks
        models_to_try = [self.model]
        for fb in self._GEMINI_FALLBACK_CHAIN:
            if fb != self.model and fb not in models_to_try:
                models_to_try.append(fb)

        last_error = ""
        for attempt, model in enumerate(models_to_try):
            # Retry 2 fois par modèle en cas de 503 / timeout (backoff léger)
            # Réduit pour éviter les attentes de 75s+ quand Google surcharge.
            for retry in range(2):
                try:
                    url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"
                    r = requests.post(url, json=payload, timeout=self.timeout_sec)
                except requests.exceptions.Timeout:
                    last_error = f"{model}: timeout {self.timeout_sec}s"
                    logger.warning("Gemini timeout sur {} (retry {}/2)", model, retry + 1)
                    _time.sleep(1 + retry * 2)
                    continue
                except Exception as exc:
                    last_error = f"{model}: {exc}"
                    logger.warning("Gemini erreur {} : {}", model, exc)
                    break   # essaye le modèle suivant

                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        last_error = f"{model}: pas de candidate"
                        logger.warning("Gemini {} : pas de candidate dans la réponse", model)
                        break
                    cand = candidates[0]
                    content = cand.get("content", {})
                    content_parts = content.get("parts", [])
                    finish_reason = cand.get("finishReason", "?")
                    if not content_parts:
                        last_error = (
                            f"{model}: réponse vide (finishReason={finish_reason}). "
                            f"Probablement safety filter Google — on essaie un autre modèle."
                        )
                        logger.warning(
                            "Gemini {} : réponse vide (finishReason={}) — essai modèle suivant",
                            model, finish_reason,
                        )
                        break
                    text = content_parts[0].get("text", "").strip()
                    if not text:
                        last_error = f"{model}: texte vide"
                        break
                    if finish_reason == "MAX_TOKENS":
                        logger.warning(
                            "Gemini {} : réponse TRONQUÉE (MAX_TOKENS). Augmente max_tokens.",
                            model,
                        )
                    elif finish_reason not in ("STOP", "?"):
                        logger.info(
                            "Gemini {} : finishReason={}", model, finish_reason,
                        )
                    if model != self.model:
                        logger.info("Gemini fallback réussi sur '{}' (demandé : {})", model, self.model)
                    return LLMResponse(
                        success=True, text=text,
                        image_scale=img_scale, image_width=img_w, image_height=img_h,
                    )

                # HTTP != 200
                if r.status_code in (503, 429):
                    # Serveur surchargé ou rate limit : backoff court puis retry
                    wait = 1 + retry * 2   # 1s puis 3s
                    last_error = f"{model}: HTTP {r.status_code}"
                    logger.warning(
                        "Gemini {} sur {} — attente {}s (retry {}/3)",
                        r.status_code, model, wait, retry + 1,
                    )
                    _time.sleep(wait)
                    continue
                else:
                    # Autre erreur HTTP : pas de retry, essaye modèle suivant
                    last_error = f"{model}: HTTP {r.status_code}: {r.text[:200]}"
                    logger.warning("Gemini HTTP {} sur {} : {}", r.status_code, model, r.text[:200])
                    break

        return LLMResponse(
            success=False, error=f"Tous les modèles ont échoué. Dernier : {last_error}",
            image_scale=img_scale, image_width=img_w, image_height=img_h,
        )

    @staticmethod
    def _draw_coord_grid(img_bgr, step: int = 200) -> None:
        """Dessine une grille de coordonnées sur l'image (in-place).

        Aide le LLM à donner des coords précises : il lit les labels au lieu
        de deviner à partir des proportions.
        """
        if not _HAS_CV2 or img_bgr is None:
            return
        try:
            import cv2 as _cv2  # noqa: PLC0415
            h, w = img_bgr.shape[:2]
            color_grid = (0, 255, 255)   # jaune
            color_text = (0, 255, 255)
            for x in range(0, w, step):
                _cv2.line(img_bgr, (x, 0), (x, h), color_grid, 1, _cv2.LINE_AA)
                _cv2.putText(
                    img_bgr, str(x), (x + 2, 14),
                    _cv2.FONT_HERSHEY_SIMPLEX, 0.4, color_text, 1, _cv2.LINE_AA,
                )
            for y in range(0, h, step):
                _cv2.line(img_bgr, (0, y), (w, y), color_grid, 1, _cv2.LINE_AA)
                _cv2.putText(
                    img_bgr, str(y), (2, y + 14),
                    _cv2.FONT_HERSHEY_SIMPLEX, 0.4, color_text, 1, _cv2.LINE_AA,
                )
        except Exception:
            pass

    @staticmethod
    def _encode_image_b64(
        img_bgr,
        max_side: int = 1024,
        quality: int = 70,
        draw_grid: bool = False,
    ) -> tuple[str | None, float, int, int]:
        """Encode une image BGR en JPEG base64, en traçant le scale factor.

        Retourne (b64_string, scale, final_width, final_height).
        Si on ne peut pas encoder, retourne (None, 1.0, 0, 0).

        `scale` = ratio appliqué (ex: 0.5 si on a divisé par 2).
        Pour retrouver les coords écran : coord_ecran = coord_image / scale.
        """
        if not _HAS_CV2 or img_bgr is None:
            return (None, 1.0, 0, 0)
        try:
            # Copie pour ne pas modifier l'image source
            img_bgr = img_bgr.copy()
            h, w = img_bgr.shape[:2]
            scale = 1.0
            if max(h, w) > max_side:
                scale = max_side / max(h, w)
                new_w = int(w * scale)
                new_h = int(h * scale)
                img_bgr = cv2.resize(
                    img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA,
                )
                h, w = new_h, new_w
            # Dessine une grille de coords APRÈS resize (donc coords dans l'espace image envoyé)
            if draw_grid:
                LLMClient._draw_coord_grid(img_bgr, step=100)
            ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                return (None, scale, 0, 0)
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            return (b64, scale, w, h)
        except Exception as exc:
            logger.debug("encode image échec : {}", exc)
            return (None, 1.0, 0, 0)

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extrait le premier bloc JSON {...} d'une réponse LLM.

        Robuste : si le JSON est tronqué (pas de } final), tente de le réparer
        en fermant automatiquement les accolades/crochets ouverts.
        """
        start = text.find("{")
        if start < 0:
            logger.warning("Pas de JSON dans la réponse : {}", text[:300])
            return None

        candidate = text[start:]
        end = candidate.rfind("}")

        # Cas 1 : JSON bien fermé
        if end > 0:
            try:
                return json.loads(candidate[: end + 1])
            except json.JSONDecodeError:
                pass   # fall-through vers la réparation

        # Cas 2 : JSON tronqué → tente de réparer en fermant les brackets ouverts
        # On parcourt le texte et on compte les {/[/" pour reconstruire la fin.
        open_braces = 0
        open_brackets = 0
        in_string = False
        escape = False
        last_valid_end = -1
        for i, ch in enumerate(candidate):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                open_braces += 1
            elif ch == "}":
                open_braces -= 1
                if open_braces == 0 and open_brackets == 0:
                    last_valid_end = i
            elif ch == "[":
                open_brackets += 1
            elif ch == "]":
                open_brackets -= 1

        # Si on a un JSON complet quelque part, essaie-le
        if last_valid_end > 0:
            try:
                return json.loads(candidate[: last_valid_end + 1])
            except json.JSONDecodeError:
                pass

        # Dernière tentative : ajoute les fermetures manquantes à la fin du dernier caractère "sûr"
        # (avant un éventuel token incomplet, on coupe à la dernière virgule ou accolade valide)
        truncated = candidate.rstrip(",\n\r\t ")
        # si on finit dans une string, tronque avant le dernier "
        if in_string:
            last_quote = truncated.rfind('"')
            if last_quote > 0:
                truncated = truncated[:last_quote]
        # si on finit avec une virgule / deux points sans valeur, tronque
        while truncated and truncated[-1] in ",:":
            truncated = truncated[:-1].rstrip()
        # ferme les brackets puis les braces
        truncated += "]" * max(open_brackets, 0)
        truncated += "}" * max(open_braces, 0)
        try:
            parsed = json.loads(truncated)
            logger.info("JSON tronqué réparé avec succès ({} chars)", len(truncated))
            return parsed
        except json.JSONDecodeError as exc:
            logger.warning("JSON tronqué impossible à réparer ({}) : {}", exc, text[:300])
            return None
