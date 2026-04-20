"""Client HTTP local pour Ollama (IA LLM multimodal, 100% local, gratuit).

Ollama tourne sur http://localhost:11434 par défaut. On expose :
  - `is_available()` : check si le service Ollama répond
  - `decide(prompt)` : envoie un prompt texte, récupère la réponse
  - `decide_vision(prompt, image_bgr)` : envoie prompt + image à un modèle multimodal
  - `decide_json(prompt)` / `decide_vision_json(prompt, image)` : parse JSON

Modèles texte : `phi3:mini` (2.3 GB), `qwen2.5:3b`, `llama3.2:3b`
Modèles vision : `llama3.2-vision:11b` (7.9 GB), `minicpm-v:8b` (5 GB), `llava:7b`

Installation Ollama : https://ollama.com/download
`ollama pull llama3.2-vision:11b`
"""
from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass

from loguru import logger

try:
    import numpy as np  # noqa: F401
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

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


@dataclass
class OllamaResponse:
    success: bool
    text: str = ""
    error: str = ""
    latency_ms: float = 0.0


class OllamaClient:
    """Client HTTP léger pour Ollama local."""

    DEFAULT_MODEL = "phi3:mini"
    DEFAULT_URL = "http://localhost:11434"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.3,
        num_predict: int = 150,
        request_timeout_sec: float = 30.0,
    ) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.base_url = (base_url or self.DEFAULT_URL).rstrip("/")
        self.temperature = temperature
        self.num_predict = num_predict
        self.request_timeout = request_timeout_sec

    # ---------- Disponibilité ----------

    def is_available(self) -> bool:
        """Ping Ollama. Retourne False si absent/arrêté."""
        if not _HAS_REQUESTS:
            return False
        try:
            import requests  # noqa: PLC0415
            r = requests.get(f"{self.base_url}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Liste les modèles installés dans Ollama."""
        if not _HAS_REQUESTS:
            return []
        try:
            import requests  # noqa: PLC0415
            r = requests.get(f"{self.base_url}/api/tags", timeout=3.0)
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as exc:
            logger.debug("Ollama list_models échec : {}", exc)
            return []

    def has_model(self) -> bool:
        """True si le modèle courant est déjà téléchargé."""
        models = self.list_models()
        # Ollama renvoie "phi3:mini" ou "phi3:mini:latest"
        return any(m.startswith(self.model) for m in models)

    # ---------- Génération ----------

    def decide(
        self,
        prompt: str,
        system: str | None = None,
        images_bgr: list | None = None,
    ) -> OllamaResponse:
        """Envoie un prompt (+ images optionnelles) à Ollama.

        `images_bgr` : liste d'images numpy BGR (shape HxWx3) — pour modèles vision.
        """
        if not _HAS_REQUESTS:
            return OllamaResponse(success=False, error="Module requests absent")

        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,
            },
        }
        if system:
            payload["system"] = system

        # Ajout des images (base64 JPEG) pour modèles multimodaux
        if images_bgr:
            encoded = []
            for img in images_bgr:
                b64 = self._encode_image_jpeg_b64(img)
                if b64:
                    encoded.append(b64)
            if encoded:
                payload["images"] = encoded

        t0 = time.perf_counter()
        try:
            import requests  # noqa: PLC0415
            r = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.request_timeout,
            )
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code != 200:
                return OllamaResponse(
                    success=False,
                    error=f"HTTP {r.status_code}: {r.text[:200]}",
                    latency_ms=latency,
                )
            data = r.json()
            text = data.get("response", "").strip()
            return OllamaResponse(success=True, text=text, latency_ms=latency)
        except Exception as exc:
            return OllamaResponse(
                success=False,
                error=str(exc),
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    @staticmethod
    def _encode_image_jpeg_b64(img_bgr, max_side: int = 1280, quality: int = 75) -> str | None:
        """Encode une image numpy BGR en base64 JPEG pour l'API Ollama.

        Redimensionne si trop grande (bande passante + vitesse du LLM).
        """
        if not _HAS_CV2 or img_bgr is None:
            return None
        try:
            h, w = img_bgr.shape[:2]
            if max(h, w) > max_side:
                scale = max_side / max(h, w)
                new_w = int(w * scale)
                new_h = int(h * scale)
                img_bgr = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                return None
            return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception as exc:
            logger.debug("Encode image échec : {}", exc)
            return None

    def decide_json(
        self,
        prompt: str,
        system: str | None = None,
        fallback: dict | None = None,
        images_bgr: list | None = None,
    ) -> dict:
        """Demande une réponse en JSON (avec ou sans images). Retourne le fallback si parse échoue."""
        response = self.decide(prompt, system=system, images_bgr=images_bgr)
        if not response.success:
            logger.warning("Ollama decide échec : {}", response.error)
            return fallback or {}

        text = response.text
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            logger.warning("Pas de JSON dans la réponse : {}", text[:200])
            return fallback or {}

        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            logger.warning("JSON invalide ({}): {}", exc, text[:200])
            return fallback or {}
