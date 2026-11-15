"""Thin wrapper around the Gemini API (google-genai) with retry/backoff.

If no API key is configured, `LLMClient.available` is False and the game runs in
offline mode (rule-based parsing + the engine's own text). That keeps Version 2
playable and fully testable without any API calls.
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

load_dotenv()


class LLMClient:
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._client = None
        if key and os.getenv("ESCAPE_ROOM_OFFLINE", "").lower() not in ("1", "true", "yes"):
            from google import genai

            self._client = genai.Client(api_key=key)

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate(self, system: str, prompt: str, *, temperature: float = 0.7,
                 schema=None, retries: int = 3) -> str:
        from google.genai import errors, types

        cfg = {"system_instruction": system, "temperature": temperature}
        if schema is not None:
            cfg.update(response_mime_type="application/json", response_schema=schema)
        config = types.GenerateContentConfig(**cfg)

        for attempt in range(retries):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
                return (resp.text or "").strip()
            except errors.APIError as exc:
                if getattr(exc, "code", None) in (429, 500, 503) and attempt < retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise
        return ""
