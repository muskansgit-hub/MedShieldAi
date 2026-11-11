from __future__ import annotations

import time

import httpx


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "base"
    model = ""

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"

    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 512) -> str:
        raise NotImplementedError

    @staticmethod
    def _post(url: str, json: dict, headers: dict, timeout: float = 90.0, retries: int = 3) -> dict:
        """POST with retry/backoff on rate limits and transient server errors."""
        last = None
        for attempt in range(retries):
            try:
                r = httpx.post(url, json=json, headers=headers, timeout=timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = ProviderError(f"HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(2 ** attempt * 2)
                    continue
                if r.status_code >= 400:
                    raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}")
                return r.json()
            except httpx.HTTPError as e:
                last = ProviderError(f"{e.__class__.__name__}: {e}")
                time.sleep(2 ** attempt)
        raise last or ProviderError("request failed")
